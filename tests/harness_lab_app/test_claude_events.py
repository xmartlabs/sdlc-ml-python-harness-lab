"""Claude stream-json observability: event parsing, rendering, usage, attribution.

The claude harness now runs with --output-format stream-json; the raw events go
to *.events.jsonl while the step log gets a human-readable rendering. These
tests cover the three consumers: the log renderer (bench_run), the usage
aggregator (bench_run), and the attribution parser (harness_lab_app).
No network, no claude CLI.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from harness_lab_app import run_failures
from harness_lab_app.attribution import (
    HARNESS_READING_SURFACE,
    build_attribution_from_events,
    parse_claude_events,
    read_step_events,
)

_SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

import bench_run  # noqa: E402


def _event(etype: str, **kw) -> str:
    return json.dumps({"type": etype, **kw})


def _assistant(*blocks: dict) -> str:
    return _event("assistant", message={"content": list(blocks)})


def _tool(name: str, **inp) -> dict:
    return {"type": "tool_use", "name": name, "input": inp}


SAMPLE_EVENTS = [
    _event("system", subtype="init", model="claude-haiku-4-5-20251001"),
    _assistant({"type": "text", "text": "Let me look at the governance docs."}),
    _assistant(_tool("Read", file_path="/app/.github/architecture.md")),
    _assistant(_tool("Read", file_path="/app/memory/context.md")),
    _assistant(_tool("Skill", skill="systematic_debugging")),
    _assistant(_tool("Bash", command="python -m pytest tests/x.py -q")),
    _assistant(_tool("Edit", file_path="/app/workspace/r1/src/mod.py")),
    _assistant(_tool("Task", subagent_type="tester", prompt="run the suite")),
    _event(
        "result",
        subtype="success",
        num_turns=7,
        duration_ms=120000,
        total_cost_usd=0.0123,
        usage={"input_tokens": 5000, "output_tokens": 900},
    ),
]


def test_parse_claude_events_extracts_signals():
    parsed = parse_claude_events(SAMPLE_EVENTS)
    assert parsed["tools"] == {"read": 2, "edit": 1, "bash": 1, "shell": 0}
    assert ".github/architecture.md" in parsed["files_read"]
    assert "memory/context.md" in parsed["files_read"]
    assert parsed["files_edited"] == ["workspace/r1/src/mod.py"]
    assert parsed["bash_commands"] == {"python": 1}
    # Skill tool = real invocation, not just a SKILL.md read.
    assert parsed["skills_consulted"] == ["systematic_debugging"]
    assert parsed["agent_modes"] == {"tester": 1}


def test_build_attribution_from_events_governance_coverage():
    data = build_attribution_from_events(SAMPLE_EVENTS, steps_parsed=1)
    usage = data["harness_usage"]
    assert ".github/architecture.md" in usage["governance_docs_read"]
    assert "memory/context.md" in usage["governance_docs_read"]
    assert usage["governance_coverage"] == f"2/{len(HARNESS_READING_SURFACE)}"
    assert "events-based" in usage["skills_note"]
    assert "CLAUDE.md" in usage["governance_docs_available"]


def test_read_step_events_collects_step_files(tmp_path):
    steps = tmp_path / "steps"
    steps.mkdir()
    (steps / "step-1.events.jsonl").write_text(SAMPLE_EVENTS[0] + "\n")
    (steps / "step-2.events.jsonl").write_text(SAMPLE_EVENTS[1] + "\n")
    lines, count = read_step_events(tmp_path)
    assert count == 2
    assert len(lines) == 2
    assert read_step_events(tmp_path / "nope")[0] == []


def test_render_claude_event_readable_lines():
    init = bench_run.render_claude_event(SAMPLE_EVENTS[0])
    assert "claude session init" in init
    text = bench_run.render_claude_event(SAMPLE_EVENTS[1])
    assert "governance docs" in text
    tool = bench_run.render_claude_event(
        _assistant(_tool("Bash", command="pytest -q\nmore"))
    )
    assert tool.startswith("→ Bash(pytest -q more")
    result = bench_run.render_claude_event(SAMPLE_EVENTS[-1])
    assert "turns=7" in result and "cost=$0.0123" in result
    # A `user` event is a tool result going back to the model — the one moment
    # claude announces that a request is in flight. The run watchdog reads this
    # marker to tell a quiet gateway from a wedged tool call; without it every
    # silence in a claude run was charged to the agent as a stall.
    assert (
        bench_run.render_claude_event(_event("user", message={}))
        == "# awaiting model\n"
    )
    assert run_failures.CLAUDE_AWAITING_MARKER in bench_run.render_claude_event(
        _event("user", message={})
    )
    # Everything else is still dropped from the readable log.
    assert bench_run.render_claude_event(_event("stream_event", message={})) is None
    # Non-JSON passes through untouched (e.g. stderr mixed in).
    assert bench_run.render_claude_event("plain line\n") == "plain line\n"


def test_collect_claude_usage_aggregates(tmp_path):
    steps = tmp_path / "steps"
    steps.mkdir()
    (steps / "step-1.events.jsonl").write_text("\n".join(SAMPLE_EVENTS) + "\n")
    (steps / "step-2.events.jsonl").write_text(
        _event(
            "result",
            subtype="success",
            num_turns=3,
            duration_ms=60000,
            total_cost_usd=0.005,
            usage={"input_tokens": 2000, "output_tokens": 400},
        )
        + "\n"
    )
    usage = bench_run.collect_claude_usage(tmp_path)
    assert usage["turns"] == 10
    assert usage["tool_calls"] == 6
    assert usage["input_tokens"] == 7000
    assert usage["output_tokens"] == 1300
    assert usage["cost_usd"] == 0.0173
    assert usage["time_minutes"] == 3.0
    assert usage["files_modified"] == ["/app/workspace/r1/src/mod.py"]
    assert usage["detected_model"] == "claude-haiku-4-5-20251001"


def test_collect_claude_usage_empty_when_no_events(tmp_path):
    assert bench_run.collect_claude_usage(tmp_path) == {}


def test_claude_cmd_includes_stream_json():
    cmd = bench_run.build_claude_cmd("claude-haiku-4-5-20251001", "do it")
    assert "--output-format" in cmd and "stream-json" in cmd and "--verbose" in cmd
    assert cmd[-1] == "do it"
