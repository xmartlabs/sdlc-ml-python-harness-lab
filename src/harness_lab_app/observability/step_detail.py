"""Per-call detail recovered from what a step left on disk.

A step trace with a 24k-char log tail as its only output is a wall of text.
Both harness lanes actually record enough to reconstruct the timeline inside a
step, and this module turns those artifacts into neutral facts the exporter
renders as child observations:

* **claude lane** — `steps/step-N.events.jsonl` (stream-json teed verbatim by
  `bench_run.py`): one *generation* per assistant message id (real model, real
  per-turn token usage) and one *tool* per `tool_use`/`tool_result` pair. The
  terminal `result` event carries the step's true usage, cost, stop reason and
  latency.
* **opencode lane** — the step log itself: every LLM call is bracketed by
  `message=stream providerID=… modelID=…` and the next `message=loop` line,
  both with real wall-clock timestamps.

Everything here is pure parsing — no I/O, no clock, no config — so the whole
shape is assertable in tests.

**Timing honesty.** claude stream-json events carry no timestamps of their
own. Lines teed after the `_received_at` stamp was added parse to real clocks
(`timing="events"`); older files fall back to spreading events evenly across
the step window (`timing="interpolated"`), and that word is written into every
affected observation's metadata rather than pretended away. opencode logs
always stamp real clocks (`timing="log"`).

**Usage honesty.** Per-turn `output_tokens` in claude stream events are
streaming snapshots that undercount the finished message; they are exported
with `output_tokens_source: "stream_snapshot"` so nobody sums them and calls
it a total. The terminal `result` event and `usage.json` remain the totals of
record.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

__all__ = [
    "Generation",
    "StepDetail",
    "ToolCall",
    "parse_claude_step_events",
    "parse_opencode_step_log",
]


def _parse_iso(raw: str | None) -> datetime | None:
    if not raw:
        return None
    try:
        parsed = datetime.fromisoformat(raw.strip().rstrip("Z"))
    except ValueError:
        return None
    return parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed


@dataclass(frozen=True)
class Generation:
    """One LLM call: a claude assistant message or an opencode stream window."""

    index: int
    model: str
    text: str
    # Event order within the step, for interpolation when no clock exists.
    first_event: int
    last_event: int
    start: datetime | None = None
    end: datetime | None = None
    usage: dict[str, int] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ToolCall:
    """One tool invocation with its matched result (claude lane only)."""

    index: int
    name: str
    input_text: str
    output_text: str
    is_error: bool
    first_event: int
    last_event: int
    start: datetime | None = None
    end: datetime | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class StepDetail:
    """Everything recoverable about the inside of one step."""

    source: str  # "claude-events" | "opencode-log" | ""
    timing: str  # "events" | "interpolated" | "log" | ""
    generations: list[Generation] = field(default_factory=list)
    tool_calls: list[ToolCall] = field(default_factory=list)
    # Terminal facts from the claude `result` event; {} elsewhere.
    result: dict[str, Any] = field(default_factory=dict)
    # Highest event index seen, so callers can interpolate proportionally.
    event_count: int = 0

    @property
    def empty(self) -> bool:
        return not (self.generations or self.tool_calls or self.result)


EMPTY_DETAIL = StepDetail(source="", timing="")


# --------------------------------------------------------------------------
# claude lane: stream-json events
# --------------------------------------------------------------------------


def _tool_result_text(content: Any) -> str:
    """A tool_result's content is a string or a list of content blocks."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = [
            block.get("text", "")
            for block in content
            if isinstance(block, dict) and block.get("type") == "text"
        ]
        return "\n".join(part for part in parts if part)
    return json.dumps(content, ensure_ascii=False, default=str) if content else ""


def _merge_usage(into: dict[str, int], usage: Any) -> None:
    """Keep the max seen per counter: later stream snapshots supersede earlier."""
    if not isinstance(usage, dict):
        return
    for key in (
        "input_tokens",
        "output_tokens",
        "cache_read_input_tokens",
        "cache_creation_input_tokens",
    ):
        value = usage.get(key)
        if isinstance(value, int):
            into[key] = max(into.get(key, 0), value)


def parse_claude_step_events(lines: list[str]) -> StepDetail:
    """Turn raw stream-json lines into generations, tool calls and totals.

    Stream events repeat a message id as its content blocks arrive, so blocks
    are grouped by `message.id` — 126 events collapse to 60 turns — and each
    turn keeps the max usage snapshot seen for it.
    """

    class _Turn:
        __slots__ = (
            "end",
            "first",
            "last",
            "model",
            "start",
            "texts",
            "thinking",
            "usage",
        )

        def __init__(self, first: int) -> None:
            self.first = first
            self.last = first
            self.model = ""
            self.start: datetime | None = None
            self.end: datetime | None = None
            self.texts: list[str] = []
            self.thinking = 0
            self.usage: dict[str, int] = {}

    turns: dict[str, _Turn] = {}
    tool_open: dict[str, dict[str, Any]] = {}  # tool_use_id -> pending call
    closed: list[dict[str, Any]] = []
    result: dict[str, Any] = {}
    stamped = 0
    index = -1

    for raw in lines:
        raw = raw.strip()
        if not raw:
            continue
        try:
            event = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if not isinstance(event, dict):
            continue
        index += 1
        received = _parse_iso(event.get("_received_at"))
        if received is not None:
            stamped += 1

        kind = event.get("type")
        raw_message = event.get("message")
        message: dict[str, Any] = raw_message if isinstance(raw_message, dict) else {}

        if kind == "assistant":
            mid = str(message.get("id") or f"turn-{index}")
            turn = turns.get(mid)
            if turn is None:
                turn = turns[mid] = _Turn(index)
                turn.start = received
            turn.last = index
            if received is not None:
                turn.end = received
            turn.model = str(message.get("model") or turn.model)
            _merge_usage(turn.usage, message.get("usage"))
            for block in message.get("content") or []:
                if not isinstance(block, dict):
                    continue
                btype = block.get("type")
                if btype == "text":
                    turn.texts.append(str(block.get("text", "")))
                elif btype == "thinking":
                    turn.thinking += len(str(block.get("thinking", "")))
                elif btype == "tool_use":
                    tool_open[str(block.get("id", ""))] = {
                        "name": str(block.get("name", "")),
                        "input": block.get("input"),
                        "first": index,
                        "start": received,
                    }
        elif kind == "user":
            for block in message.get("content") or []:
                if not isinstance(block, dict) or block.get("type") != "tool_result":
                    continue
                use_id = str(block.get("tool_use_id", ""))
                pending = tool_open.pop(use_id, None)
                if pending is None:
                    continue
                closed.append(
                    {
                        **pending,
                        "use_id": use_id,
                        "output": _tool_result_text(block.get("content")),
                        "is_error": bool(block.get("is_error")),
                        "last": index,
                        "end": received,
                    }
                )
        elif kind == "result":
            raw_usage = event.get("usage")
            usage: dict[str, Any] = raw_usage if isinstance(raw_usage, dict) else {}
            result = {
                "stop_reason": event.get("stop_reason"),
                "num_turns": event.get("num_turns"),
                "duration_ms": event.get("duration_ms"),
                "duration_api_ms": event.get("duration_api_ms"),
                "ttft_ms": event.get("ttft_ms"),
                "total_cost_usd": event.get("total_cost_usd"),
                "is_error": event.get("is_error"),
                "result_text": event.get("result"),
                "permission_denials": len(event.get("permission_denials") or []),
                "usage": {
                    key: value for key, value in usage.items() if isinstance(value, int)
                },
                "models": sorted((event.get("modelUsage") or {}).keys()),
            }

    # A tool_use that never saw its result (the step died mid-call) still
    # deserves a row: that unfinished call is often the crash site.
    for use_id, pending in tool_open.items():
        closed.append(
            {
                **pending,
                "use_id": use_id,
                "output": "",
                "is_error": False,
                "last": pending["first"],
                "end": pending["start"],
                "unresolved": True,
            }
        )
    closed.sort(key=lambda call: call["first"])
    tools = [
        ToolCall(
            index=i + 1,
            name=call["name"],
            input_text=json.dumps(call["input"], ensure_ascii=False, default=str)
            if call["input"] is not None
            else "",
            output_text=call["output"],
            is_error=call["is_error"],
            first_event=call["first"],
            last_event=call["last"],
            start=call["start"],
            end=call["end"],
            metadata={
                "tool_use_id": call["use_id"],
                **({"unresolved": True} if call.get("unresolved") else {}),
            },
        )
        for i, call in enumerate(closed)
    ]

    generations = [
        Generation(
            index=i + 1,
            model=turn.model,
            text="\n".join(text for text in turn.texts if text),
            first_event=turn.first,
            last_event=turn.last,
            start=turn.start,
            end=turn.end,
            usage=dict(turn.usage),
            metadata={
                "message_id": mid,
                **({"thinking_chars": turn.thinking} if turn.thinking else {}),
                **(
                    {"output_tokens_source": "stream_snapshot"}
                    if turn.usage.get("output_tokens")
                    else {}
                ),
            },
        )
        for i, (mid, turn) in enumerate(
            sorted(turns.items(), key=lambda item: item[1].first)
        )
    ]

    if not (generations or tools or result):
        return EMPTY_DETAIL
    # One stamped line is enough: items without a stamp of their own still get
    # interpolated individually by the exporter.
    timing = "events" if stamped else "interpolated"
    return StepDetail(
        source="claude-events",
        timing=timing,
        generations=generations,
        tool_calls=tools,
        result=result,
        event_count=index + 1,
    )


# --------------------------------------------------------------------------
# opencode lane: the step log's own clocks
# --------------------------------------------------------------------------

_OC_LINE_RE = re.compile(
    r"^timestamp=(\S+?)Z?\s+level=\S+.*?message=(\S+|\"[^\"]*\")(.*)$"
)
_OC_FIELD_RE = re.compile(r"(\w[\w.]*)=(\"[^\"]*\"|\S+)")


def _oc_fields(rest: str) -> dict[str, str]:
    return {key: value.strip('"') for key, value in _OC_FIELD_RE.findall(rest)}


def parse_opencode_step_log(log_text: str) -> StepDetail:
    """LLM-call windows from an opencode `--print-logs` step log.

    `message=stream providerID=… modelID=…` opens a call; the next timestamped
    `loop`/`stream` line closes it. The assistant's rendered text is whatever
    non-log lines land between the two markers.
    """
    generations: list[Generation] = []
    open_call: dict[str, Any] | None = None
    buffer: list[str] = []
    index = -1

    def close(at: datetime | None) -> None:
        nonlocal open_call, buffer
        if open_call is None:
            return
        generations.append(
            Generation(
                index=len(generations) + 1,
                model=open_call["model"],
                text="\n".join(line for line in buffer if line.strip()),
                first_event=open_call["first"],
                last_event=index,
                start=open_call["start"],
                end=at or open_call["start"],
                metadata={
                    key: value
                    for key, value in (
                        ("agent", open_call["agent"]),
                        ("provider", open_call["provider"]),
                    )
                    if value
                },
            )
        )
        open_call = None
        buffer = []

    for line in log_text.splitlines():
        match = _OC_LINE_RE.match(line)
        if match is None:
            if open_call is not None:
                buffer.append(line)
            continue
        index += 1
        stamp = _parse_iso(match.group(1))
        message = match.group(2).strip('"')
        if message == "stream":
            close(stamp)
            fields = _oc_fields(match.group(3))
            provider = fields.get("providerID", "")
            model = fields.get("modelID", "")
            open_call = {
                "first": index,
                "start": stamp,
                "provider": provider,
                "model": f"{provider}/{model}" if provider and model else model,
                "agent": fields.get("agent", ""),
            }
        elif message == "loop":
            close(stamp)

    close(None)
    if not generations:
        return EMPTY_DETAIL
    return StepDetail(
        source="opencode-log",
        timing="log",
        generations=generations,
        event_count=index + 1,
    )
