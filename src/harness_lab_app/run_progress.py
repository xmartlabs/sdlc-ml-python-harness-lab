"""What a run is doing right now, read from the files it is writing.

A long run used to be a log and a spinner: the operator saw the agent's raw
output, could not tell which phase it was in, how long that phase had taken,
what it had cost, or whether the last line was two seconds or forty minutes old
(racing3d-v1-K-001 sat 28 minutes inside a silent test with nothing on screen
saying so). Everything needed to say it is already on disk while the run is
live:

- ``steps/step-N-prompt.md`` names the phase (``## Phase N of M — name``);
- ``steps/step-N.events.jsonl`` grows as the agent works, each line stamped
  with ``_received_at`` on arrival, and ends with a ``result`` event carrying the
  step's real cost and turns. A harness that writes no event stream (opencode)
  is read from ``steps/step-N.log`` instead: turns, times and the last action,
  never a cost;
- ``steps/step-N.meta.json`` appears when step N is done, with its seconds;
- ``live_steps.json`` holds the grade of each finished phase, written by the
  worker while the run goes on (see ``phase_watch.LivePhaseGrader``); a phase
  with no live grade shows the per-step reward's (``fill_final_grades``).

Pure reads, no database, so the API can serve it on every poll and a test can
build it from a temporary directory.
"""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from pathlib import Path

LIVE_STEPS_FILE = "live_steps.json"
SLOW_STATE_FILE = "slow_phase.json"

_PHASE_RE = re.compile(r"^## Phase (\d+) of (\d+)\s*[—-]\s*(.+?)\s*$", re.MULTILINE)
# A step exists once the harness writes either of its streams: the event
# stream (claude) or the plain log (opencode and any harness without events).
_STEP_FILE_RE = re.compile(r"^step-(\d+)\.(?:events\.jsonl|log)$")


def _parse_ts(value: Any) -> float | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def _read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text())
    except (OSError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def phase_title(text: str) -> tuple[int, int, str] | None:
    """``(number, total, name)`` from a prompt's ``## Phase N of M — name``."""
    match = _PHASE_RE.search(text)
    if match is None:
        return None
    # Phase 1's heading carries an instruction, "(do only this now)", that is
    # not part of its name.
    name = re.sub(r"\s*\([^)]*\)\s*$", "", match.group(3)).strip()
    return int(match.group(1)), int(match.group(2)), name


def _last_action(event: dict[str, Any]) -> str | None:
    """A one-line description of what an assistant event did."""
    for item in (event.get("message") or {}).get("content") or []:
        if not isinstance(item, dict):
            continue
        if item.get("type") == "tool_use":
            name = str(item.get("name") or "tool")
            data = item.get("input") or {}
            detail = data.get("command") or data.get("file_path") or data.get("pattern")
            text = f"{name}: {detail}" if detail else name
            return " ".join(str(text).split())[:160]
        if item.get("type") == "text" and str(item.get("text") or "").strip():
            return " ".join(str(item["text"]).split())[:160]
    return None


# Events that are the agent doing something. Everything else in the stream is
# the CLI talking about itself — above all `tool_progress`, a heartbeat Claude
# Code sends every 30 s while a tool runs, which made a two-minute wait on one
# command read as "quiet for 3 s" (chess3d-v1-K-004).
_AGENT_EVENTS = frozenset({"assistant", "user", "result"})


def _tool_uses(event: dict[str, Any]) -> list[tuple[str, str, str]]:
    """``(id, tool, detail)`` of every tool call an assistant event makes."""
    calls = []
    for item in (event.get("message") or {}).get("content") or []:
        if isinstance(item, dict) and item.get("type") == "tool_use" and item.get("id"):
            data = item.get("input") or {}
            detail = data.get("command") or data.get("file_path") or data.get("pattern")
            calls.append(
                (
                    str(item["id"]),
                    str(item.get("name") or "tool"),
                    " ".join(str(detail or "").split())[:200],
                )
            )
    return calls


def _tool_results(event: dict[str, Any]) -> set[str]:
    return {
        str(item.get("tool_use_id"))
        for item in (event.get("message") or {}).get("content") or []
        if isinstance(item, dict) and item.get("type") == "tool_result"
    }


def step_activity(path: Path) -> dict[str, Any]:
    """Turns, output tokens, cost (once known) and the last action of a step.

    Also ``waiting_on``: the tool call still running — asked for, no result
    yet — and when it started, so a long command reads as one, not as silence.
    """
    turns = 0
    output_tokens = 0
    first_ts: float | None = None
    last_ts: float | None = None
    last_action: str | None = None
    result: dict[str, Any] | None = None
    pending: dict[str, dict[str, Any]] = {}
    try:
        handle = path.open(errors="replace")
    except OSError:
        return {}
    with handle:
        for line in handle:
            try:
                event = json.loads(line)
            except ValueError:
                continue
            if not isinstance(event, dict):
                continue
            kind = event.get("type")
            ts = _parse_ts(event.get("_received_at"))
            if ts is not None and kind in _AGENT_EVENTS:
                first_ts = ts if first_ts is None else first_ts
                last_ts = ts
            if kind == "assistant":
                turns += 1
                usage = (event.get("message") or {}).get("usage") or {}
                output_tokens += int(usage.get("output_tokens") or 0)
                last_action = _last_action(event) or last_action
                for tool_id, tool, detail in _tool_uses(event):
                    pending[tool_id] = {"tool": tool, "detail": detail, "since": ts}
            elif kind == "user":
                for tool_id in _tool_results(event):
                    pending.pop(tool_id, None)
            elif kind == "result":
                result = event
                pending.clear()
    running = list(pending.values())
    activity: dict[str, Any] = {
        "turns": turns,
        "output_tokens": output_tokens,
        "first_event_at": first_ts,
        "last_event_at": last_ts,
        "last_action": last_action,
        # The oldest call still open: the one the agent is blocked on.
        "waiting_on": running[0] if running else None,
    }
    if result is not None:
        activity["finished"] = True
        activity["cost_usd"] = result.get("total_cost_usd")
        activity["turns"] = result.get("num_turns") or turns
    return activity


_ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")
_LOG_TS_RE = re.compile(r"^timestamp=(\S+)")
# One agent turn in opencode's `--print-logs` output.
_LOG_TURN_RE = re.compile(r"\bmessage=loop\b.*\bstep=\d+")
# What opencode prints for each tool call: `$ cmd`, `→ Read f`, `← Edit f`,
# `✱ Glob …`, `✗ Write failed`.
_LOG_ACTION_RE = re.compile(r"^(?:\$|→|←|✱|✗|⚙)\s+(\S.*)$")


def log_activity(path: Path) -> dict[str, Any]:
    """Turns and the last action of a step, from a harness's plain step log.

    For harnesses that write no ``events.jsonl`` (opencode, the local models'
    lane). Each ``message=loop … step=N`` line is one agent turn and carries a
    timestamp; tool calls are the lines opencode marks with ``$``, ``→``, ``←``
    or ``✱``. Cost is never known here: a local model reports none.
    """
    turns = 0
    first_ts: float | None = None
    last_ts: float | None = None
    last_action: str | None = None
    try:
        handle = path.open(errors="replace")
    except OSError:
        return {}
    with handle:
        for raw in handle:
            match = _LOG_TS_RE.match(raw)
            if match is not None:
                ts = _parse_ts(match.group(1))
                if ts is not None:
                    first_ts = ts if first_ts is None else first_ts
                    last_ts = ts
                if _LOG_TURN_RE.search(raw):
                    turns += 1
                continue
            action = _LOG_ACTION_RE.match(_ANSI_RE.sub("", raw).strip())
            if action is not None:
                last_action = " ".join(action.group(0).split())[:160]
    return {
        "turns": turns,
        "output_tokens": 0,
        "first_event_at": first_ts,
        "last_event_at": last_ts,
        "last_action": last_action,
    }


def _activity(steps_dir: Path, number: int) -> dict[str, Any]:
    """A step's activity from its event stream, or from its plain log without one."""
    events = steps_dir / f"step-{number}.events.jsonl"
    if events.is_file():
        return step_activity(events)
    return log_activity(steps_dir / f"step-{number}.log")


def _step_numbers(steps_dir: Path) -> list[int]:
    if not steps_dir.is_dir():
        return []
    numbers = {
        int(match.group(1))
        for entry in steps_dir.iterdir()
        if (match := _STEP_FILE_RE.match(entry.name))
    }
    return sorted(numbers)


def fill_final_grades(progress: dict[str, Any], objective: dict[str, Any]) -> None:
    """Give each phase without a live grade the grade the per-step reward gave it.

    ``objective`` is the run's ``Validation.objective``; its ``per_step`` rows
    carry ``passed``/``total`` per step once the run has been validated. Only
    rows that measured something are used, and a live grade is never replaced.
    """
    per_step = objective.get("per_step") if isinstance(objective, dict) else None
    rows = per_step.get("steps") if isinstance(per_step, dict) else None
    if not isinstance(rows, list):
        return
    final: dict[int, dict[str, Any]] = {}
    for row in rows:
        if not isinstance(row, dict) or not isinstance(row.get("step"), int):
            continue
        if isinstance(row.get("passed"), int) and isinstance(row.get("total"), int):
            final[row["step"]] = {
                "passed": row["passed"],
                "total": row["total"],
                "progress": row.get("progress"),
            }
    for step in progress.get("steps") or []:
        if step.get("grade") is None and step.get("step") in final:
            step["grade"] = final[step["step"]]


def _waiting_on(waiting_on: dict[str, Any] | None, now: float) -> dict[str, Any] | None:
    """The command the running phase is blocked on, with how long it has run."""
    if not waiting_on:
        return None
    waiting = dict(waiting_on)
    since = waiting.pop("since", None)
    waiting["seconds"] = round(now - since, 1) if since is not None else None
    return waiting


def progress_clock(ended_at: datetime | None) -> float | None:
    """The ``now`` to read a run's progress at: its end once it has one.

    A finished run's clock stopped when it ended, so an open phase of a cut run
    is not still counting hours later. SQLite returns naive datetimes, which
    the lab always stores in UTC.
    """
    if ended_at is None:
        return None
    if ended_at.tzinfo is None:
        ended_at = ended_at.replace(tzinfo=UTC)
    return ended_at.timestamp()


def read_progress(
    run_dir: Path,
    *,
    max_steps: int = 0,
    first_prompt: str = "",
    now: float | None = None,
) -> dict[str, Any]:
    """The live state of a run, built from its run directory.

    ``first_prompt`` is step 1's prompt (the case prompt), whose phase heading
    is not in a ``step-1-prompt.md``. ``max_steps`` is the run's step budget,
    used when no phase heading says how many phases there are.
    """
    now = now if now is not None else datetime.now(UTC).timestamp()
    steps_dir = run_dir / "steps"
    live = _read_json(run_dir / LIVE_STEPS_FILE)
    raw_grades = live.get("steps")
    grades: dict[str, Any] = raw_grades if isinstance(raw_grades, dict) else {}
    rows: list[dict[str, Any]] = []
    waiting_by_step: dict[int, Any] = {}
    total_cost = 0.0
    cost_known = True
    # Whether any step reported a cost at all. A local model reports none, and
    # its total is unknown, not US$0.00.
    any_cost = False
    for number in _step_numbers(steps_dir):
        prompt_path = steps_dir / f"step-{number}-prompt.md"
        prompt = (
            prompt_path.read_text(errors="replace")
            if prompt_path.is_file()
            else (first_prompt if number == 1 else "")
        )
        title = phase_title(prompt)
        activity = _activity(steps_dir, number)
        waiting_by_step[number] = activity.get("waiting_on")
        meta = _read_json(steps_dir / f"step-{number}.meta.json")
        done = bool(meta) or bool(activity.get("finished"))
        started = activity.get("first_event_at")
        if meta.get("seconds") is not None:
            seconds = float(meta["seconds"])
        elif started is not None:
            seconds = max(0.0, now - started)
        else:
            seconds = None
        if activity.get("cost_usd") is not None:
            total_cost += float(activity["cost_usd"])
            any_cost = True
        else:
            cost_known = False
        rows.append(
            {
                "step": number,
                "phase": title[0] if title else number,
                "phase_total": title[1] if title else (max_steps or None),
                "name": title[2] if title else "",
                "done": done,
                "seconds": round(seconds, 1) if seconds is not None else None,
                "turns": activity.get("turns", 0),
                "output_tokens": activity.get("output_tokens", 0),
                "cost_usd": activity.get("cost_usd"),
                "last_action": activity.get("last_action"),
                "last_event_at": activity.get("last_event_at"),
                "grade": grades.get(str(number)),
            }
        )
    current = next((row for row in reversed(rows) if not row["done"]), None)
    current_waiting = (
        waiting_by_step.get(current["step"]) if current is not None else None
    )
    last_event = max(
        (row["last_event_at"] for row in rows if row["last_event_at"]), default=None
    )
    slow = _read_json(run_dir / SLOW_STATE_FILE)
    return {
        "steps": rows,
        "current_step": current["step"] if current else None,
        "phase_total": next(
            (row["phase_total"] for row in rows if row["phase_total"]),
            max_steps or None,
        ),
        "cost_usd": round(total_cost, 4) if any_cost else None,
        "cost_complete": cost_known,
        "quiet_seconds": round(now - last_event, 1) if last_event else None,
        "last_action": current["last_action"] if current else None,
        "waiting_on": _waiting_on(current_waiting, now),
        "slow": slow or None,
    }
