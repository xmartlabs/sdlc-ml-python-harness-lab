"""Reconstructing what an agent did, in order, from a finished run's artifacts."""

from __future__ import annotations

import json

from harness_lab_app.trajectory import (
    MAX_STEPS,
    build,
    expected_tools_for,
    from_claude_events,
    from_opencode_logs,
)


def event(*blocks) -> str:
    return json.dumps({"type": "assistant", "message": {"content": list(blocks)}})


def tool_use(name, **payload) -> dict:
    return {"type": "tool_use", "name": name, "input": payload}


def test_claude_events_keep_order_and_identifying_argument():
    lines = [
        event(tool_use("Read", file_path="/app/src/api.py")),
        event(tool_use("Edit", file_path="/app/src/api.py")),
        event(
            tool_use("Bash", command="pytest -q tests/"),
            {"type": "text", "text": "done"},
        ),
    ]

    parsed = from_claude_events(lines)

    assert [step["tool"] for step in parsed["steps"]] == ["Read", "Edit", "Bash"]
    # Container paths are repo-relative, and an Edit records its target rather
    # than the file body it would otherwise drown a judge in.
    assert parsed["steps"][1]["input"] == "src/api.py"
    assert parsed["steps"][2]["input"] == "pytest -q tests/"
    assert parsed["final_output"] == "done"


def test_claude_events_prefer_the_result_event_for_the_closing_message():
    lines = [
        event(tool_use("Read", file_path="a.py")),
        json.dumps({"type": "result", "result": "I fixed the parser."}),
    ]
    assert from_claude_events(lines)["final_output"] == "I fixed the parser."


def test_malformed_event_lines_are_skipped_not_fatal():
    lines = ["{not json", event(tool_use("Bash", command="ls"))]
    assert len(from_claude_events(lines)["steps"]) == 1


def test_opencode_logs_give_order_without_arguments():
    text = "\n".join(
        [
            "message=evaluated permission=read pattern=/app/README.md",
            "noise that matches nothing",
            'message=evaluated permission=bash pattern="make test"',
        ]
    )

    parsed = from_opencode_logs(text)

    assert [step["tool"] for step in parsed["steps"]] == ["Read", "Bash"]
    assert parsed["final_output"] == ""


def test_build_prefers_events_over_logs():
    built = build(
        event_lines=[event(tool_use("Skill", skill="writing-plans"))],
        log_text="message=evaluated permission=read pattern=x.md",
    )
    assert built["source"] == "claude_events"
    assert built["tools_called"] == ["Skill"]


def test_build_falls_back_to_logs_when_no_events():
    built = build(
        event_lines=[], log_text="message=evaluated permission=edit pattern=x.py"
    )
    assert built["source"] == "opencode_logs"


def test_a_run_with_nothing_recorded_has_no_trajectory():
    """Not an empty trajectory — none. The distinction is the whole point."""
    assert build(event_lines=[], log_text="") is None
    assert build(event_lines=["{bad"], log_text="") is None


def test_long_trajectories_are_clipped_and_say_so():
    lines = [
        event(tool_use("Read", file_path=f"f{i}.py")) for i in range(MAX_STEPS + 40)
    ]

    built = build(event_lines=lines)

    assert built["truncated"] is True
    assert len(built["steps"]) == MAX_STEPS
    # The full count survives the clip, so nothing reads the sample as the total.
    assert built["step_count"] == MAX_STEPS + 40


def test_expected_tools_come_only_from_what_the_case_declares():
    assert expected_tools_for(files_expected=(), validation_commands=()) == ()
    assert expected_tools_for(
        files_expected=("src/a.py",), validation_commands=("pytest",)
    ) == ("Edit", "Bash")


def tool_result(tool_use_id: str, content: str, *, error: bool = False) -> str:
    return json.dumps(
        {
            "type": "user",
            "message": {
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": tool_use_id,
                        "content": content,
                        "is_error": error,
                    }
                ]
            },
        }
    )


def test_a_rejected_skill_call_is_not_an_invocation():
    """`Unknown skill` hands the agent nothing.

    Counting it is how a run whose catalog was never registered with the CLI
    scored as a fully activated treatment (ADR-0061).
    """
    call = {**tool_use("Skill", skill="systematic_debugging"), "id": "t1"}
    lines = [
        event(call),
        tool_result(
            "t1",
            "<tool_use_error>Unknown skill: systematic_debugging</tool_use_error>",
            error=True,
        ),
    ]

    parsed = from_claude_events(lines)

    assert parsed["skill_calls"] == []
    assert parsed["skill_call_errors"] == ["systematic_debugging"]
    # The step itself survives: the agent reached for the skill, and a judge
    # reading the trajectory should see it reaching and getting nothing.
    assert [step["tool"] for step in parsed["steps"]] == ["Skill"]

    built = build(event_lines=lines)
    assert built["skill_invocations"] == {}
    assert built["skill_call_errors"] == {"systematic_debugging": 1}


def test_a_skill_call_whose_result_succeeded_still_counts():
    call = {**tool_use("Skill", skill="tdd"), "id": "t2"}
    lines = [event(call), tool_result("t2", "# TDD\nRed, green, refactor.")]
    assert from_claude_events(lines)["skill_calls"] == ["tdd"]


def test_a_failed_read_of_a_skill_file_is_not_an_invocation_either():
    call = {
        **tool_use("Read", file_path="/app/.claude/skills/tdd/SKILL.md"),
        "id": "t3",
    }
    lines = [event(call), tool_result("t3", "File does not exist.", error=True)]
    parsed = from_claude_events(lines)
    assert parsed["skill_calls"] == []
    assert parsed["skill_call_errors"] == ["tdd"]


def test_a_call_with_no_recorded_result_still_counts():
    """A truncated record is not evidence of failure."""
    lines = [event(tool_use("Skill", skill="tdd"))]
    assert from_claude_events(lines)["skill_calls"] == ["tdd"]
    assert from_claude_events(lines)["skill_call_errors"] == []
