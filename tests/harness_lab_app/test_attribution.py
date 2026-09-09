from __future__ import annotations

import json
from pathlib import Path

from harness_lab_app.attribution import (
    HARNESS_READING_SURFACE,
    build_attribution,
    parse_logs,
    read_step_logs,
)

# Synthetic log lines in opencode's real format (see data/runs/*/steps/*.log).
SAMPLE_LOG = "\n".join(
    [
        "timestamp=T level=INFO run=abc message=evaluated permission=read pattern=.github/architecture.md",
        "timestamp=T level=INFO run=abc message=evaluated permission=read pattern=.github/standards.md",
        "timestamp=T level=INFO run=abc message=evaluated permission=read pattern=memory/context.md",
        "timestamp=T level=INFO run=abc message=evaluated permission=read pattern=/app/.claude/skills/writing-plans/SKILL.md",
        "timestamp=T level=INFO run=abc message=evaluated permission=edit pattern=workspace/r1/main.py",
        "timestamp=T level=INFO run=abc message=evaluated permission=edit pattern=workspace/r1/PLAN.md",
        'timestamp=T level=INFO run=abc message=evaluated permission=bash pattern="BENCH_SMOKE=1 python main.py"',
        'timestamp=T level=INFO run=abc message=evaluated permission=bash pattern="echo hi"',
        'timestamp=T level=INFO run=abc message="touching file" file=/app/workspace/r1/main.py',
        'timestamp=T level=INFO run=abc message="shell tool using shell"',
        "timestamp=T level=INFO run=abc message=loop agent=build step=1",
        "timestamp=T level=INFO run=abc message=loop agent=title step=1",
    ]
)


def test_parse_logs_counts_tools():
    parsed = parse_logs(SAMPLE_LOG)
    assert parsed["tools"]["read"] == 4
    assert parsed["tools"]["edit"] == 2
    assert parsed["tools"]["bash"] == 2
    assert parsed["tools"]["shell"] == 1


def test_parse_logs_extracts_files():
    parsed = parse_logs(SAMPLE_LOG)
    assert ".github/architecture.md" in parsed["files_read"]
    assert "workspace/r1/main.py" in parsed["files_edited"]


def test_parse_logs_bash_command_histogram():
    parsed = parse_logs(SAMPLE_LOG)
    # The env-assignment prefix is skipped; python is the real command.
    assert parsed["bash_commands"].get("python") == 1
    assert parsed["bash_commands"].get("echo") == 1


def test_parse_logs_ignores_noise_agent_modes():
    parsed = parse_logs(SAMPLE_LOG)
    assert parsed["agent_modes"] == {"build": 1}


def test_parse_logs_detects_skill_consulted_from_skill_md_read():
    parsed = parse_logs(SAMPLE_LOG)
    assert parsed["skills_consulted"] == ["writing-plans"]


def test_build_attribution_governance_coverage():
    attribution = build_attribution(
        SAMPLE_LOG, skills_available=["writing-plans", "brainstorming"], steps_parsed=1
    )
    usage = attribution["harness_usage"]
    # architecture.md, standards.md, memory/context.md were read.
    assert ".github/architecture.md" in usage["governance_docs_read"]
    assert usage["governance_coverage"] == f"3/{len(HARNESS_READING_SURFACE)}"
    assert usage["skills_available"] == ["brainstorming", "writing-plans"]
    assert usage["skills_consulted"] == ["writing-plans"]
    assert "3/" in attribution["summary"]


def test_read_step_logs_prefers_step_files(tmp_path: Path):
    steps = tmp_path / "steps"
    steps.mkdir()
    (steps / "step-1.log").write_text("permission=read pattern=OPENCODE.md")
    (steps / "step-2.log").write_text("permission=edit pattern=workspace/r1/main.py")
    text, count = read_step_logs(tmp_path)
    assert count == 2
    assert "OPENCODE.md" in text


def test_read_step_logs_falls_back_to_opencode_log(tmp_path: Path):
    (tmp_path / "opencode.log").write_text("permission=read pattern=OPENCODE.md")
    text, count = read_step_logs(tmp_path)
    assert count == 0
    assert "OPENCODE.md" in text


def test_read_step_logs_missing_is_empty(tmp_path: Path):
    text, count = read_step_logs(tmp_path)
    assert text == ""
    assert count == 0


def test_an_in_target_run_reads_its_own_rulebook_not_the_labs():
    """`workspace/<run>/CLAUDE.md` is the run's rulebook; `CLAUDE.md` is the lab's.

    Both used to normalise to something the surface could not match — or,
    worse, only the lab's did, so the one read that signalled contamination
    counted as compliance.
    """
    from harness_lab_app.attribution import build_attribution_from_events

    events = [
        json.dumps(
            {
                "type": "assistant",
                "message": {
                    "content": [
                        {
                            "type": "tool_use",
                            "name": "Read",
                            "input": {"file_path": path},
                        }
                    ]
                },
            }
        )
        for path in (
            "/app/workspace/r-9/CLAUDE.md",
            "/app/workspace/r-9/memory/context.md",
        )
    ]

    usage = build_attribution_from_events(
        events,
        reading_surface=("CLAUDE.md", "memory/context.md", ".github/standards.md"),
        governance={"source": "target_workspace", "harness_source": "native"},
    )["harness_usage"]

    assert usage["governance_docs_read"] == ["CLAUDE.md", "memory/context.md"]
    assert usage["governance_coverage"] == "2/3"


def test_a_lab_root_run_is_still_read_against_the_lab():
    from harness_lab_app.attribution import build_attribution_from_events

    events = [
        json.dumps(
            {
                "type": "assistant",
                "message": {
                    "content": [
                        {
                            "type": "tool_use",
                            "name": "Read",
                            "input": {"file_path": "/app/CLAUDE.md"},
                        }
                    ]
                },
            }
        )
    ]

    usage = build_attribution_from_events(
        events,
        reading_surface=("CLAUDE.md",),
        governance={"source": "lab_root", "harness_source": "lab_root"},
    )["harness_usage"]

    assert usage["governance_docs_read"] == ["CLAUDE.md"]
