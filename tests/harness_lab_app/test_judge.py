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
