"""The ordered record of what an agent did, reconstructed from a run's artifacts.

Attribution answers "how much of the harness was exercised?" by counting. That
count is order-free on purpose — it aggregates across trials. A trajectory is
the other half: *this* run, step by step, in sequence, which is the only shape a
trajectory evaluator can read.

Two sources, in order of fidelity:

``claude_events``
    ``steps/step-*.events.jsonl`` — real ``tool_use`` blocks with their inputs.
    Order, arguments and the final assistant message all survive.

``opencode_logs``
    ``steps/step-*.log`` — permission lines. Order survives; arguments and
    outputs mostly do not. Enough for "which tools, in what order", not enough
    to reconstruct what the agent was told back.

A run with neither returns ``None``. That is the point: "no trajectory was
recorded" and "the trajectory was bad" are different claims, and only the first
one is true of a run the lab never instrumented.
"""

from __future__ import annotations

import json
import re
from typing import Any

# How many steps a trajectory carries. A 200-step run rendered in full would
# dominate a judge's context and cost more than the run it is judging; the head
# and the tail are where the plan and the outcome live.
MAX_STEPS = 120

_PERMISSION_RE = re.compile(
    r"permission=(?P<action>[a-z]+) pattern=(?P<target>\"[^\"]*\"|\S+)"
)

# opencode permission actions → the tool names a trajectory speaks in, so both
# sources produce comparable `tools_called` lists.
_ACTION_TOOL = {
    "read": "Read",
    "edit": "Edit",
    "write": "Write",
    "bash": "Bash",
    "shell": "Bash",
}

_EDIT_TOOLS = {"Edit", "Write", "MultiEdit", "NotebookEdit"}


def _strip_app(path: str) -> str:
    return path.removeprefix("/app/").strip('"')


def from_claude_events(lines: list[str]) -> dict[str, Any]:
    """Ordered tool calls and the closing message from stream-json events."""
    steps: list[dict[str, Any]] = []
    final_output = ""
    for raw in lines:
        try:
            event = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if event.get("type") == "result":
            final_output = str(event.get("result") or final_output)
            continue
        if event.get("type") != "assistant":
            continue
        for block in (event.get("message") or {}).get("content", []):
            kind = block.get("type")
            if kind == "text":
                text = str(block.get("text") or "").strip()
                if text:
                    final_output = text
            elif kind == "tool_use":
                name = str(block.get("name") or "")
                if not name:
                    continue
                steps.append(
                    {
                        "index": len(steps) + 1,
                        "tool": name,
                        "input": _summarize_input(name, block.get("input") or {}),
                    }
                )
    return {"steps": steps, "final_output": final_output}


def _summarize_input(name: str, payload: dict[str, Any]) -> str:
    """The one argument that identifies a call, not the whole payload.

    A judge reading a trajectory needs to know *which file* was edited and
    *which command* ran. It does not need the file's new contents, which is what
    an unsummarized ``Edit`` input mostly is.
    """
    if name == "Bash":
        return str(payload.get("command") or "")[:400]
    if name in _EDIT_TOOLS or name == "Read":
        target = payload.get("file_path") or payload.get("notebook_path") or ""
        return _strip_app(str(target))
    if name == "Skill":
        return str(payload.get("skill") or "")
    if name in {"Task", "Agent"}:
        return str(payload.get("subagent_type") or "agent")
    if name in {"Grep", "Glob"}:
        return str(payload.get("pattern") or "")
    return ""


def from_opencode_logs(log_text: str) -> dict[str, Any]:
    """Ordered tool calls from permission lines. Lower fidelity, same shape."""
    steps: list[dict[str, Any]] = []
    for line in (log_text or "").splitlines():
        match = _PERMISSION_RE.search(line)
        if not match:
            continue
        tool = _ACTION_TOOL.get(match.group("action"))
        if tool is None:
            continue
        steps.append(
            {
                "index": len(steps) + 1,
                "tool": tool,
                "input": _strip_app(match.group("target")),
            }
        )
    return {"steps": steps, "final_output": ""}


def _clip(steps: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], bool]:
    if len(steps) <= MAX_STEPS:
        return steps, False
    half = MAX_STEPS // 2
    return steps[:half] + steps[-half:], True


def build(
    *,
    event_lines: list[str] | None = None,
    log_text: str = "",
    task: str = "",
    expected_tools: tuple[str, ...] = (),
) -> dict[str, Any] | None:
    """Assemble a run's trajectory, or ``None`` when nothing was recorded."""
    source = ""
    parsed: dict[str, Any] = {"steps": [], "final_output": ""}
    if event_lines:
        parsed = from_claude_events(event_lines)
        source = "claude_events"
    if not parsed["steps"] and log_text:
        parsed = from_opencode_logs(log_text)
        source = "opencode_logs"
    if not parsed["steps"]:
        return None

    steps, truncated = _clip(parsed["steps"])
    tools_called: list[str] = []
    for step in parsed["steps"]:
        if step["tool"] not in tools_called:
            tools_called.append(step["tool"])
    return {
        "source": source,
        "task": task,
        "steps": steps,
        "step_count": len(parsed["steps"]),
        "truncated": truncated,
        "tools_called": tools_called,
        "expected_tools": list(expected_tools),
        "final_output": parsed["final_output"],
    }


def expected_tools_for(
    *, files_expected: tuple[str, ...], validation_commands: tuple[str, ...]
) -> tuple[str, ...]:
    """What a case's own declarations imply the agent had to reach for.

    Deliberately shallow, and only ever derived from something the case already
    states. A case that declares files it expects touched implies an edit; one
    that ships validation commands implies a shell. A case that declares neither
    implies nothing, and the empty tuple is what stops `tool_correctness` from
    being scored against an expectation nobody wrote down.
    """
    tools: list[str] = []
    if files_expected:
        tools.append("Edit")
    if validation_commands:
        tools.append("Bash")
    return tuple(tools)
