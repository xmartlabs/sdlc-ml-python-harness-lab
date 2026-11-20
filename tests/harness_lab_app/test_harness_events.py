"""codex / gemini event streams normalized into the claude shape (ADR-0094).

The fixtures are the exact event shapes of the pinned CLIs:
codex-cli 0.171.0 (`codex-rs/exec/src/exec_events.rs` at `rust-v0.171.0`) and
@google/gemini-cli 0.70.0 (`StreamJsonFormatter` in the shipped bundle). Each
test feeds the translation into the *unchanged* readers the lab already has —
attribution, trajectory, step progress — because "the translated stream is
read the same way as claude's" is the property that matters, not the shape of
an intermediate dict.
"""

from __future__ import annotations

import json
from pathlib import Path

from harness_lab_app.attribution import parse_claude_events
from harness_lab_app.harness_events import (
    codex_to_claude,
    gemini_to_claude,
    render_codex_event,
    render_gemini_event,
    translate,
)
from harness_lab_app.run_failures import (
    awaits_model_response,
    is_model_request,
    is_stream_error,
)
from harness_lab_app.run_progress import step_activity
from harness_lab_app.trajectory import from_claude_events


def _lines(*events: dict) -> list[str]:
    return [json.dumps(event) for event in events]


# A codex step: read a skill with a shell command, edit two files, finish.
# Usage on `turn.completed` is the thread's *cumulative* total.
CODEX_STEP = _lines(
    {"type": "thread.started", "thread_id": "th-1"},
    {"type": "turn.started"},
    {
        "type": "item.started",
        "item": {
            "id": "item_0",
            "type": "command_execution",
            "command": "bash -lc 'cat .codex/skills/systematic_debugging/SKILL.md'",
            "aggregated_output": "",
            "exit_code": None,
            "status": "in_progress",
        },
    },
    {
        "type": "item.completed",
        "item": {
            "id": "item_0",
            "type": "command_execution",
            "command": "bash -lc 'cat .codex/skills/systematic_debugging/SKILL.md'",
            "aggregated_output": "---\nname: systematic_debugging\n",
            "exit_code": 0,
            "status": "completed",
        },
    },
    {
        "type": "item.completed",
        "item": {"id": "item_1", "type": "reasoning", "text": "plan the fix"},
    },
    {
        "type": "item.completed",
        "item": {
            "id": "item_2",
            "type": "file_change",
            "changes": [
                {"path": "/app/workspace/run-1/main.py", "kind": "add"},
                {"path": "/app/workspace/run-1/util.py", "kind": "update"},
            ],
            "status": "completed",
        },
    },
    {
        "type": "item.completed",
        "item": {
            "id": "item_3",
            "type": "command_execution",
            "command": "pytest -q",
            "aggregated_output": "1 failed",
            "exit_code": 1,
            "status": "failed",
        },
    },
    {
        "type": "turn.completed",
        "usage": {
            "input_tokens": 1000,
            "cached_input_tokens": 600,
            "cache_write_input_tokens": 0,
            "output_tokens": 50,
            "reasoning_output_tokens": 20,
        },
    },
    {"type": "turn.started"},
    {
        "type": "item.completed",
        "item": {"id": "item_4", "type": "agent_message", "text": "Done."},
    },
    {
        "type": "turn.completed",
        "usage": {
            "input_tokens": 2500,
            "cached_input_tokens": 1800,
            "cache_write_input_tokens": 0,
            "output_tokens": 90,
            "reasoning_output_tokens": 30,
        },
    },
)

# A gemini step: activate a skill, read, write, a failed shell, an answer in
# two delta chunks, and the closing stats.
GEMINI_STEP = _lines(
    {
        "type": "init",
        "timestamp": "2026-11-20T02:07:26.497Z",
        "session_id": "ses-g",
        "model": "auto",
    },
    {"type": "message", "role": "user", "content": "build it"},
    {
        "type": "tool_use",
        "tool_name": "activate_skill",
        "tool_id": "t1",
        "parameters": {"name": "systematic_debugging"},
    },
    {"type": "tool_result", "tool_id": "t1", "status": "success", "output": "ok"},
    {
        "type": "tool_use",
        "tool_name": "read_file",
        "tool_id": "t2",
        "parameters": {"file_path": "/app/workspace/run-1/README.md"},
    },
    {"type": "tool_result", "tool_id": "t2", "status": "success", "output": "..."},
    {
        "type": "tool_use",
        "tool_name": "write_file",
        "tool_id": "t3",
        "parameters": {"file_path": "/app/workspace/run-1/main.py", "content": "x"},
    },
    {"type": "tool_result", "tool_id": "t3", "status": "success", "output": ""},
    {
        "type": "tool_use",
        "tool_name": "run_shell_command",
        "tool_id": "t4",
        "parameters": {"command": "pytest -q"},
    },
    {
        "type": "tool_result",
        "tool_id": "t4",
        "status": "error",
        "output": "",
        "error": {"type": "TOOL_EXECUTION_ERROR", "message": "exit 1"},
    },
    {"type": "message", "role": "assistant", "content": "All ", "delta": True},
    {"type": "message", "role": "assistant", "content": "done.", "delta": True},
    {
        "type": "result",
        "status": "success",
        "stats": {
            "total_tokens": 3100,
            "input_tokens": 3000,
            "output_tokens": 100,
            "cached": 2000,
            "input": 1000,
            "duration_ms": 4200,
            "tool_calls": 4,
            "models": {
                "gemini-3.5-flash": {
                    "total_tokens": 600,
                    "input_tokens": 580,
                    "output_tokens": 20,
                    "cached": 400,
                    "input": 180,
                },
                "gemini-2.5-pro": {
                    "total_tokens": 2500,
                    "input_tokens": 2420,
                    "output_tokens": 80,
                    "cached": 1600,
                    "input": 820,
                },
            },
        },
    },
)


def _result(lines: list[str]) -> dict:
    return next(
        event
        for event in (json.loads(line) for line in lines)
        if event.get("type") == "result"
    )


# --- codex ------------------------------------------------------------------


def test_codex_attribution_reads_like_a_claude_run() -> None:
    attribution = parse_claude_events(codex_to_claude(CODEX_STEP))

    assert attribution["tools"]["bash"] == 2
    assert attribution["tools"]["edit"] == 2
    assert attribution["files_edited"] == [
        "workspace/run-1/main.py",
        "workspace/run-1/util.py",
    ]
    assert attribution["bash_commands"] == {"bash": 1, "pytest": 1}


def test_a_codex_skill_is_invoked_by_reading_its_skill_file() -> None:
    # Codex has no Skill tool: it opens SKILL.md with a shell command. The
    # trajectory counts a SKILL.md path in a call as an invocation, so the
    # same observation exists for codex as for claude.
    trajectory = from_claude_events(codex_to_claude(CODEX_STEP))

    assert trajectory["skill_calls"] == ["systematic_debugging"]
    assert trajectory["final_output"] == "Done."


def test_codex_usage_is_the_last_cumulative_total_not_a_sum_of_turns() -> None:
    usage = _result(codex_to_claude(CODEX_STEP))["usage"]

    # 2500 total input of which 1800 cached → 700 uncached (claude's meaning).
    assert usage["input_tokens"] == 700
    assert usage["cache_read_input_tokens"] == 1800
    # Reasoning is already inside codex's output_tokens (protocol.rs
    # `blended_total = non_cached_input + output_tokens`): kept beside, not added.
    assert usage["output_tokens"] == 90
    assert usage["reasoning_output_tokens"] == 30


def test_a_codex_subscription_result_has_no_price() -> None:
    result = _result(codex_to_claude(CODEX_STEP))

    assert result["total_cost_usd"] is None
    assert result["billing_mode"] == "subscription"
    assert result["num_turns"] == 2


def test_a_failed_codex_command_is_an_errored_tool_result() -> None:
    events = [json.loads(line) for line in codex_to_claude(CODEX_STEP)]
    errored = [
        block["tool_use_id"]
        for event in events
        if event["type"] == "user"
        for block in event["message"]["content"]
        if block.get("is_error")
    ]
    assert errored == ["item_3"]


def test_an_unauthenticated_codex_turn_reports_the_providers_error() -> None:
    # Exactly what codex-cli 0.171.0 printed with no login (live probe).
    lines = _lines(
        {"type": "thread.started", "thread_id": "th-x"},
        {"type": "turn.started"},
        {"type": "error", "message": "Reconnecting... 2/5 (unexpected status 401)"},
        {
            "type": "turn.failed",
            "error": {"message": "unexpected status 401 Unauthorized: Missing bearer"},
        },
    )
    result = _result(codex_to_claude(lines))

    assert result["is_error"] is True
    assert "401 Unauthorized" in result["result"]
    assert "usage" not in result  # no turn completed: no figure, not zeros
    rendered = render_codex_event(lines[-1])
    assert rendered is not None and "API Error" in rendered
    assert is_stream_error(render_codex_event(lines[2]) or "", "codex")


def test_codex_renderer_marks_requests_for_the_watchdog() -> None:
    started = render_codex_event(json.dumps({"type": "turn.started"})) or ""

    assert is_model_request(started, "codex")
    assert awaits_model_response(started, "codex")


# --- gemini -----------------------------------------------------------------


def test_gemini_activate_skill_is_a_skill_invocation() -> None:
    translated = gemini_to_claude(GEMINI_STEP)

    trajectory = from_claude_events(translated)
    attribution = parse_claude_events(translated)

    assert trajectory["skill_calls"] == ["systematic_debugging"]
    assert "systematic_debugging" in attribution["skills_consulted"]
    assert attribution["files_read"] == ["workspace/run-1/README.md"]
    assert attribution["files_edited"] == ["workspace/run-1/main.py"]
    assert attribution["bash_commands"] == {"pytest": 1}


def test_gemini_text_deltas_become_one_message() -> None:
    trajectory = from_claude_events(gemini_to_claude(GEMINI_STEP))

    assert trajectory["final_output"] == "All done."


def test_gemini_usage_keeps_the_cache_apart_and_names_the_models() -> None:
    result = _result(gemini_to_claude(GEMINI_STEP))

    # `input` is the uncached prompt; `input_tokens` would double-count cache.
    assert result["usage"] == {
        "input_tokens": 1000,
        "cache_read_input_tokens": 2000,
        "output_tokens": 100,
    }
    assert result["duration_ms"] == 4200
    assert set(result["modelUsage"]) == {"gemini-3.5-flash", "gemini-2.5-pro"}
    assert result["total_cost_usd"] is None
    assert result["billing_mode"] == "subscription"


def test_a_gemini_auth_failure_is_reported_not_zeroed() -> None:
    # The shape gemini-cli 0.70.0 printed with a bad key (live probe).
    lines = _lines(
        {"type": "init", "session_id": "s", "model": "gemini-3.5-flash"},
        {"type": "message", "role": "user", "content": "x"},
        {
            "type": "result",
            "status": "error",
            "error": {"type": "Error", "message": "[API Error: API key not valid.]"},
            "stats": {"input_tokens": 0, "output_tokens": 0, "input": 0, "cached": 0},
        },
    )
    result = _result(gemini_to_claude(lines))

    assert result["is_error"] is True
    assert "API key not valid" in result["result"]
    assert "API Error" in (render_gemini_event(lines[-1]) or "")


def test_unknown_gemini_tools_keep_their_name() -> None:
    lines = _lines(
        {
            "type": "tool_use",
            "tool_name": "tracker_create_task",
            "tool_id": "t9",
            "parameters": {},
        },
    )
    event = json.loads(gemini_to_claude(lines)[0])

    assert event["message"]["content"][0]["name"] == "gemini:tracker_create_task"


# --- shared -----------------------------------------------------------------


def test_the_arrival_stamp_survives_translation() -> None:
    stamped = '{"_received_at":"2026-11-20T10:00:00Z",' + CODEX_STEP[0][1:]
    first = json.loads(codex_to_claude([stamped, *CODEX_STEP[1:]])[0])

    assert first["_received_at"] == "2026-11-20T10:00:00Z"


def test_garbage_lines_are_skipped_not_fatal() -> None:
    assert codex_to_claude(["not json", "", "[1,2]"]) == []
    assert gemini_to_claude(["{bad", "null"]) == []


def test_claude_and_opencode_streams_pass_through_untouched() -> None:
    lines = ['{"type":"assistant"}', "plain log line"]

    assert translate("claude", lines) == lines
    assert translate("opencode", lines) == lines


def test_run_progress_reads_a_translated_step(tmp_path: Path) -> None:
    path = tmp_path / "step-1.events.jsonl"
    path.write_text("\n".join(gemini_to_claude(GEMINI_STEP)) + "\n")

    activity = step_activity(path)

    assert activity["turns"] >= 4
    assert activity.get("waiting_on") in (None, [])
