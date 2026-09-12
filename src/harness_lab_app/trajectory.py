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

# A skill is "invoked" when the agent calls the Skill tool with its name, or
# opens its SKILL.md by any other means (Read, cat, a subagent). Either leaves
# a path like `.../skills/<name>/SKILL.md` in the call's input.
_SKILL_FILE_RE = re.compile(r"skills/([A-Za-z0-9_.-]+)/SKILL\.md")


def _skill_mentions(name: str, payload: dict[str, Any]) -> list[str]:
    """Skill names a single tool call invoked, in the order they appear."""
    if name == "Skill":
        skill = str(payload.get("skill") or "").strip()
        return [skill] if skill else []
    return _SKILL_FILE_RE.findall(json.dumps(payload))


def _strip_app(path: str) -> str:
    return path.removeprefix("/app/").strip('"')


def _parse_events(lines: list[str]) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    for raw in lines:
        try:
            event = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if isinstance(event, dict):
            events.append(event)
    return events


def _errored_tool_uses(events: list[dict[str, Any]]) -> set[str]:
    """Ids of the tool calls whose result came back as an error.

    Results arrive as ``user`` events carrying ``tool_result`` blocks, each
    pointing back at its ``tool_use_id``. Read in a pass of their own because
    a result always follows its call, and the call is what carries the skill
    name.
    """
    errored: set[str] = set()
    for event in events:
        if event.get("type") != "user":
            continue
        for block in (event.get("message") or {}).get("content", []):
            if not isinstance(block, dict) or block.get("type") != "tool_result":
                continue
            if block.get("is_error") and block.get("tool_use_id"):
                errored.add(str(block["tool_use_id"]))
    return errored


def from_claude_events(lines: list[str]) -> dict[str, Any]:
    """Ordered tool calls and the closing message from stream-json events.

    A skill call the harness rejected is **not** an invocation. ``Skill`` with
    a name the CLI never registered answers ``Unknown skill`` and hands the
    agent nothing, and a read of a ``SKILL.md`` that is not there does the
    same; counting either as "the agent opened the skill" is how a run whose
    catalog was never loaded scored as a fully activated treatment. Those
    calls are kept in ``steps`` (they happened, and a judge should see the
    agent reaching for something it could not get) and tallied separately in
    ``skill_call_errors``, so the observation can say *why* nothing was
    opened rather than merely that it was not.
    """
    events = _parse_events(lines)
    errored = _errored_tool_uses(events)
    steps: list[dict[str, Any]] = []
    skill_calls: list[str] = []
    skill_call_errors: list[str] = []
    final_output = ""
    for event in events:
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
                payload = block.get("input") or {}
                mentions = _skill_mentions(name, payload)
                if str(block.get("id") or "") in errored:
                    skill_call_errors.extend(mentions)
                else:
                    skill_calls.extend(mentions)
                steps.append(
                    {
                        "index": len(steps) + 1,
                        "tool": name,
                        "input": _summarize_input(name, payload),
                    }
                )
    return {
        "steps": steps,
        "final_output": final_output,
        "skill_calls": skill_calls,
        "skill_call_errors": skill_call_errors,
    }


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
    skill_calls: list[str] = []
    for line in (log_text or "").splitlines():
        match = _PERMISSION_RE.search(line)
        if not match:
            continue
        tool = _ACTION_TOOL.get(match.group("action"))
        if tool is None:
            continue
        target = _strip_app(match.group("target"))
        skill_calls.extend(_SKILL_FILE_RE.findall(target))
        steps.append(
            {
                "index": len(steps) + 1,
                "tool": tool,
                "input": target,
            }
        )
    return {
        "steps": steps,
        "final_output": "",
        "skill_calls": skill_calls,
        "skill_call_errors": [],
    }


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
    parsed: dict[str, Any] = {
        "steps": [],
        "final_output": "",
        "skill_calls": [],
        "skill_call_errors": [],
    }
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
    # Counted over the full record, not the clipped steps: an invocation on
    # step 130 of 200 is still an invocation.
    skill_invocations: dict[str, int] = {}
    for name in parsed.get("skill_calls") or []:
        skill_invocations[name] = skill_invocations.get(name, 0) + 1
    # Kept apart from the invocations, never folded in: "reached for it five
    # times and got nothing" is the fact that explains a zero above.
    skill_call_errors: dict[str, int] = {}
    for name in parsed.get("skill_call_errors") or []:
        skill_call_errors[name] = skill_call_errors.get(name, 0) + 1
    return {
        "source": source,
        "task": task,
        "steps": steps,
        "step_count": len(parsed["steps"]),
        "truncated": truncated,
        "tools_called": tools_called,
        "expected_tools": list(expected_tools),
        "final_output": parsed["final_output"],
        "skill_invocations": skill_invocations,
        "skill_call_errors": skill_call_errors,
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
