from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from harness_lab_app.validation import (
    build_validation_spec,
    collect_cost,
    collect_effort,
    compute_composite,
    parse_objective,
)


def _case(**kwargs):
    defaults = {"type": "build_from_scratch", "validation_commands": []}
    defaults.update(kwargs)
    return SimpleNamespace(**defaults)


def test_collect_cost_parses_usage_json(tmp_path: Path):
    (tmp_path / "usage.json").write_text(
        json.dumps(
            {
                "input_tokens": 200000,
                "output_tokens": 2000,
                "cost_usd": None,
                "time_minutes": 1.0,
                "steps": 8,
            }
        )
    )
    cost = collect_cost(tmp_path)
    assert cost["available"] is True
    assert cost["total_tokens"] == 202000
    assert cost["time_minutes"] == 1.0


def test_collect_cost_missing_usage_is_unavailable(tmp_path: Path):
    assert collect_cost(tmp_path) == {"available": False}


def test_collect_effort_uses_usage_steps_and_interventions(tmp_path: Path):
    (tmp_path / "usage.json").write_text(json.dumps({"steps": 8}))
    effort = collect_effort(tmp_path, interventions=2)
    assert effort == {"steps": 8, "interventions": 2}


def test_collect_effort_falls_back_to_step_logs(tmp_path: Path):
    steps = tmp_path / "steps"
    steps.mkdir()
    (steps / "step-1.log").write_text("x")
    (steps / "step-2.log").write_text("y")
    (tmp_path / "usage.json").write_text(json.dumps({}))
    effort = collect_effort(tmp_path, interventions=None)
    assert effort["steps"] == 2


def test_build_validation_spec_substitutes_workspace():
    case = _case(validation_commands=["BENCH_SMOKE=1 python {workspace}/main.py"])
    spec = build_validation_spec(case, "tetris-v1-K-001")
    assert spec["strategy"] == "smoke"
    assert spec["commands"] == [
        ["sh", "-c", "BENCH_SMOKE=1 python workspace/tetris-v1-K-001/main.py"]
    ]


def test_build_validation_spec_skips_prose_notes():
    case = _case(validation_commands=["Run the game and observe the window."])
    spec = build_validation_spec(case, "r1")
    assert spec["commands"] == []
    assert spec["strategy"] == "manual"


def test_build_validation_spec_tests_strategy_for_bugfix():
    case = _case(type="bugfix", validation_commands=["pytest {workspace}/tests"])
    spec = build_validation_spec(case, "r1")
    assert spec["strategy"] == "tests"


def test_parse_objective_all_pass():
    objective = parse_objective(
        [{"command": "smoke", "exit_code": 0, "output": "ok"}], "smoke"
    )
    assert objective["passed"] is True
    assert objective["checks"][0]["passed"] is True


def test_parse_objective_any_failure_fails():
    objective = parse_objective(
        [
            {"command": "a", "exit_code": 0, "output": ""},
            {"command": "b", "exit_code": 1, "output": "boom"},
        ],
        "tests",
    )
    assert objective["passed"] is False
    assert "1/2" in objective["summary"]


def test_parse_objective_extracts_pytest_summary():
    output = "===== 12 passed, 1 failed in 3.2s ====="
    objective = parse_objective(
        [{"command": "pytest", "exit_code": 1, "output": output}], "tests"
    )
    assert "passed" in objective["checks"][0]["detail"]


def test_parse_objective_no_commands_is_manual():
    objective = parse_objective([], "manual")
    assert objective["passed"] is None
    assert objective["checks"] == []


def test_compute_composite_requires_functional_score():
    assert compute_composite({"passed": True}, {}) is None


def test_compute_composite_uses_functional_when_objective_passes():
    composite = compute_composite({"passed": True}, {"functional_score": 8})
    assert composite == 8.0


def test_compute_composite_caps_when_objective_fails():
    # A high manual score is not credible when the smoke/tests fail.
    composite = compute_composite({"passed": False}, {"functional_score": 9})
    assert composite == 1.0


def test_compute_composite_unknown_objective_does_not_cap():
    composite = compute_composite({"passed": None}, {"functional_score": 7})
    assert composite == 7.0
