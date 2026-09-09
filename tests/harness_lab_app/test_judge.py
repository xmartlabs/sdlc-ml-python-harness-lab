"""LLM-as-judge functional scoring: pure prompt assembly, parsing, and clamping."""

from __future__ import annotations

import pytest

from harness_lab_app.harness_lifecycle import judge


def test_build_judge_message_includes_task_and_diff() -> None:
    message = judge.build_judge_message(
        problem_statement="Fix the config loader to accept TOML.",
        diff="diff --git a/config.py b/config.py\n+toml support",
        objective_summary="F2P 0/1 P2P 18/18",
        report="Added a from_file toml branch.",
    )
    assert "Problem statement" in message
    assert "Fix the config loader" in message
    assert "Agent diff" in message
    assert "toml support" in message
    # The partial test summary rides along when present.
    assert "F2P 0/1" in message


def test_build_judge_message_marks_empty_diff() -> None:
    message = judge.build_judge_message(problem_statement="Do something.", diff="   ")
    assert "(no diff produced)" in message
    # Optional sections are omitted, not rendered empty.
    assert "Automated test summary" not in message
    assert "Agent report" not in message


def test_parse_judge_score_strips_fences_and_clamps() -> None:
    assert judge.parse_judge_score('```json\n{"score": 8, "rationale": "ok"}\n```') == {
        "score": 8,
        "rationale": "ok",
    }
    # Out-of-range scores are clamped into 0..10; floats round to int.
    assert judge.parse_judge_score('{"score": 42, "rationale": "x"}')["score"] == 10
    assert judge.parse_judge_score('{"score": -3, "rationale": "x"}')["score"] == 0
    assert judge.parse_judge_score('{"score": 7.6, "rationale": "x"}')["score"] == 8


def test_parse_judge_score_rejects_non_json() -> None:
    with pytest.raises(ValueError):
        judge.parse_judge_score("the run looks resolved to me")


def test_judge_functional_score_uses_injected_gateway() -> None:
    calls: dict[str, str] = {}

    def fake_gateway(model: str, system: str, user: str) -> str:
        calls["model"] = model
        calls["system"] = system
        calls["user"] = user
        return '{"score": 6, "rationale": "partial fix"}'

    result = judge.judge_functional_score(
        problem_statement="Fix it.",
        diff="diff",
        gateway=fake_gateway,
        model="stub-judge",
    )
    assert result == {"score": 6, "rationale": "partial fix"}
    assert calls["model"] == "stub-judge"
    assert "Fix it." in calls["user"]
    assert "JSON" in calls["system"]


# --------------------------------------------------------------------------
# Evidence-typed rubric levels (2026-09-08)
# --------------------------------------------------------------------------


def test_the_rubric_prompt_forbids_manual_levels_and_demands_a_citation():
    """Two equivalent Tetris builds scored 10 and 2 on identical evidence.

    The rubric's upper levels describe gameplay no diff or headless check can
    establish, and the prompt told the judge both to award what the diff shows
    and to withhold what only a check could prove. Levels now say what can
    establish them, and the prompt stops guessing at the ones only a human can.
    """
    from harness_lab_app.harness_lifecycle.judge import RUBRIC_SYSTEM_PROMPT

    assert "[manual]" in RUBRIC_SYSTEM_PROMPT
    assert "NEVER award" in RUBRIC_SYSTEM_PROMPT
    assert "[diff]" in RUBRIC_SYSTEM_PROMPT
    assert "quote" in RUBRIC_SYSTEM_PROMPT
    assert '"evidence"' in RUBRIC_SYSTEM_PROMPT


def test_parse_keeps_the_citation_when_the_judge_gives_one():
    from harness_lab_app.harness_lifecycle.judge import parse_judge_score

    parsed = parse_judge_score(
        '{"score": 8, "evidence": "def clear_lines(self): ...", "rationale": "level 8"}'
    )

    assert parsed["score"] == 8
    assert parsed["evidence"].startswith("def clear_lines")
    # Older replies carry no citation; that is not a parse failure.
    assert "evidence" not in parse_judge_score('{"score": 2, "rationale": "runs"}')


def test_every_shipped_rubric_tags_every_level():
    """A rubric is the judge's scale; an untagged level is a guess."""
    import re
    from pathlib import Path

    benchmarks = Path(__file__).resolve().parents[2] / "benchmarks"
    # Only the rubrics of cases: a write-up directory may carry a scoring.md too.
    rubrics = sorted(
        task.parent / "scoring.md"
        for task in benchmarks.glob("*/task.yaml")
        if (task.parent / "scoring.md").exists()
    )
    assert rubrics
    for rubric in rubrics:
        rows = [
            line
            for line in rubric.read_text().splitlines()
            if re.match(r"^\| \d+ \|", line)
        ]
        assert rows, rubric
        for row in rows:
            assert re.search(r"\[(check|diff|manual)\]", row), (rubric, row)


def test_the_tetris_v2_rubric_is_provable_by_its_own_tests():
    """Ten shipped assertions mean no level is left to a human (ADR-0050)."""
    import re
    from pathlib import Path

    text = (
        Path(__file__).resolve().parents[2] / "benchmarks" / "tetris-v2" / "scoring.md"
    ).read_text()
    rows = [line for line in text.splitlines() if re.match(r"^\| \d+ \|", line)]
    assert not any("[manual]" in row for row in rows)
