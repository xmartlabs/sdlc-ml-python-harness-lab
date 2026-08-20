"""Tests for the regression suite core (suite / metrics / gates).

Pure evaluation logic over synthetic run data — no DB, no Celery, no real runs.
Also loads the real canonical ``benchmarks/harness-regression-v1/suite.yaml``.
"""

from __future__ import annotations

from pathlib import Path

from harness_lab_app.harness_lifecycle import gates, metrics, suite
from harness_lab_app.harness_lifecycle.models import SuccessCriterion

REPO_ROOT = Path(__file__).resolve().parents[2]


# --------------------------------------------------------------------------- #
# Suite parsing + expansion
# --------------------------------------------------------------------------- #


def test_load_canonical_suite() -> None:
    definition = suite.load_default_suite(REPO_ROOT)
    assert definition.suite_id == "harness-regression-v1"
    assert definition.execution.sequential is True
    assert {c.id for c in definition.cases} == {
        "cli-textstats-v1",
        "systematic-debugging-v1",
        "documentation-update-v1",
        "pallets__flask-4992",
    }
    assert definition.gates.pathological_loop_threshold == 3


def test_planned_runs_expansion() -> None:
    definition = suite.load_default_suite(REPO_ROOT)
    plan = suite.planned_runs(definition)
    # (3 + 3 + 3 + 1) repetitions x 2 arms = 20 child runs.
    assert len(plan) == 20
    assert suite.child_runs_per_arm(definition) == 10
    arms = {(p.case_id, p.arm) for p in plan}
    assert ("cli-textstats-v1", "baseline") in arms
    assert ("cli-textstats-v1", "candidate") in arms


# --------------------------------------------------------------------------- #
# Metrics
# --------------------------------------------------------------------------- #


def test_normalize_command() -> None:
    assert metrics.normalize_command("  LS   -la ") == "ls -la"


def test_repeated_command_max() -> None:
    assert metrics.repeated_command_max([]) == 0
    assert metrics.repeated_command_max(["ls", "ls", "cat x"]) == 2
    assert metrics.repeated_command_max(["a", "a", "a", "b", "a"]) == 3
    assert metrics.repeated_command_max(["LS", "ls ", " ls"]) == 3  # normalized-equal


def test_normalize_run() -> None:
    rm = metrics.normalize_run(
        {
            "case_id": "cli-textstats-v1",
            "arm": "candidate",
            "usage": {
                "input_tokens": 400,
                "output_tokens": 600,
                "cost_usd": 0.12,
                "time_minutes": 2.5,
                "steps": 14,
                "files_modified": ["a.py", "b.py"],
                "diff_lines": 40,
            },
            "objective": {"passed": True},
            "composite": 8.0,
            "interventions": 0,
            "commands": ["ls", "ls", "pytest"],
            "harness_hash": "sha256:aaa",
        }
    )
    assert rm.total_tokens == 1000
    assert rm.files_modified == 2
    assert rm.repeated_command_max == 2
    assert rm.passed is True


def test_median_and_percent_increase() -> None:
    assert metrics.median([]) == 0.0
    assert metrics.median([3, 1, 2]) == 2
    assert metrics.median([1, 2, 3, 4]) == 2.5
    assert metrics.percent_increase(100, 125) == 25.0
    assert metrics.percent_increase(0, 0) == 0.0


def _rm(
    case: str,
    arm: str,
    *,
    passed: bool = True,
    composite: float = 8.0,
    tokens: int = 1000,
    cost: float = 0.10,
    duration: float = 1.0,
    steps: int = 10,
    interventions: int = 0,
    commands: list[str] | None = None,
) -> metrics.RunMetrics:
    return metrics.normalize_run(
        {
            "case_id": case,
            "arm": arm,
            "usage": {
                "total_tokens": tokens,
                "cost_usd": cost,
                "time_minutes": duration,
                "steps": steps,
            },
            "objective": {"passed": passed},
            "composite": composite,
            "interventions": interventions,
            "commands": commands or [],
        }
    )


def test_aggregate_and_group() -> None:
    runs = [_rm("c1", "candidate", passed=True), _rm("c1", "candidate", passed=False)]
    agg = metrics.aggregate(runs)
    assert agg.runs == 2
    assert agg.pass_count == 1
    assert agg.pass_ratio == 0.5
    grouped = metrics.group_by_case(runs)
    assert grouped["c1"].runs == 2


def test_controlled_match() -> None:
    ok, diffs = metrics.controlled_match(
        {"case": "x", "executor_model": "m"}, {"case": "x", "executor_model": "m"}
    )
    assert ok and diffs == []
    ok, diffs = metrics.controlled_match(
        {"executor_model": "m1"}, {"executor_model": "m2"}
    )
    assert not ok and diffs == ["executor_model"]


# --------------------------------------------------------------------------- #
# Gates
# --------------------------------------------------------------------------- #


def test_success_criteria() -> None:
    criteria = [
        SuccessCriterion(
            metric="repeated_equivalent_command_max", operator="lte", value=2
        ),
        SuccessCriterion(metric="objective_passed", operator="eq", value=True),
    ]
    ok, failed = gates.evaluate_success_criteria(
        criteria, {"repeated_equivalent_command_max": 1, "objective_passed": True}
    )
    assert ok and failed == []
    ok, failed = gates.evaluate_success_criteria(
        criteria, {"repeated_equivalent_command_max": 5, "objective_passed": True}
    )
    assert not ok and failed == ["repeated_equivalent_command_max"]


def _passing_case_maps() -> tuple[dict, dict]:
    definition = suite.load_default_suite(REPO_ROOT)
    baseline = {
        c.id: metrics.aggregate([_rm(c.id, "baseline")]) for c in definition.cases
    }
    candidate = {
        c.id: metrics.aggregate([_rm(c.id, "candidate")]) for c in definition.cases
    }
    return baseline, candidate


def test_hard_gates_all_pass() -> None:
    definition = suite.load_default_suite(REPO_ROOT)
    baseline, candidate = _passing_case_maps()
    results = gates.evaluate_hard_gates(
        definition,
        baseline,
        candidate,
        controlled_ok=True,
        context={
            "release_valid": True,
            "governance_only": True,
            "checks_pass": True,
            "attestation_clean": True,
        },
        proposal_case_id="systematic-debugging-v1",
        proposal_criteria=[
            SuccessCriterion(metric="objective_passed", operator="eq", value=True)
        ],
        proposal_metrics={"objective_passed": True},
    )
    assert gates.all_hard_pass(results) is True


def test_hard_gates_pathological_loop_fails() -> None:
    definition = suite.load_default_suite(REPO_ROOT)
    baseline, candidate = _passing_case_maps()
    # Candidate cli case loops 3 consecutive equivalent commands → fails.
    candidate["cli-textstats-v1"] = metrics.aggregate(
        [_rm("cli-textstats-v1", "candidate", commands=["ls", "ls", "ls"])]
    )
    results = gates.evaluate_hard_gates(
        definition, baseline, candidate, controlled_ok=True
    )
    loop = next(r for r in results if r.name == "no_pathological_loop")
    assert loop.passed is False
    assert gates.all_hard_pass(results) is False


def test_hard_gates_regression_fails() -> None:
    definition = suite.load_default_suite(REPO_ROOT)
    baseline, candidate = _passing_case_maps()
    candidate["documentation-update-v1"] = metrics.aggregate(
        [_rm("documentation-update-v1", "candidate", passed=False)]
    )
    results = gates.evaluate_hard_gates(
        definition, baseline, candidate, controlled_ok=True
    )
    assert gates.all_hard_pass(results) is False
    assert any(
        r.name == "no_regression:documentation-update-v1" and not r.passed
        for r in results
    )


def test_hard_gates_controlled_mismatch_fails() -> None:
    definition = suite.load_default_suite(REPO_ROOT)
    baseline, candidate = _passing_case_maps()
    results = gates.evaluate_hard_gates(
        definition,
        baseline,
        candidate,
        controlled_ok=False,
        controlled_diffs=["executor_model"],
    )
    assert gates.all_hard_pass(results) is False


def test_warnings_flag_token_increase() -> None:
    definition = suite.load_default_suite(REPO_ROOT)
    baseline = metrics.aggregate([_rm("c", "baseline", tokens=1000)])
    candidate = metrics.aggregate([_rm("c", "candidate", tokens=1300)])  # +30% > 20%
    results = gates.evaluate_warnings(definition, baseline, candidate)
    tokens = next(r for r in results if r.name == "tokens")
    assert tokens.passed is False  # warning raised
    assert tokens.severity == "warning"
