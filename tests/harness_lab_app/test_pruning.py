"""Pruning: is a harness item removable without making the agent worse? (ADR-0076)"""

from __future__ import annotations

from harness_lab_app import pruning


def _cases(pattern: dict[str, list[bool]]) -> dict[str, list[bool]]:
    return {case: list(runs) for case, runs in pattern.items()}


def _metric(pattern: dict[str, list[float]]) -> dict[str, list[float]]:
    return {case: list(values) for case, values in pattern.items()}


FIVE_CASES = [f"case-{i}" for i in range(5)]


def _all(value: bool, reps: int = 4) -> dict[str, list[bool]]:
    return {case: [value] * reps for case in FIVE_CASES}


def _flat(value: float, reps: int = 3) -> dict[str, list[float]]:
    return {case: [value] * reps for case in FIVE_CASES}


def _metrics(cost: float = 0.30, auc: float = 1.0) -> dict[str, dict]:
    """Both metrics a removable verdict requires, flat across the cases."""
    return {"cost_to_resolve_usd": _flat(cost), "progress_auc": _flat(auc)}


def test_an_item_whose_removal_changes_nothing_is_removable() -> None:
    cost = {case: [0.30, 0.31, 0.29, 0.30] for case in FIVE_CASES}
    metrics = {"cost_to_resolve_usd": cost, "progress_auc": _flat(1.0)}
    verdict = pruning.assess_removal(
        item="skill:brainstorm_quick",
        control_success=_all(True),
        treatment_success=_all(True),
        control_metrics=metrics,
        treatment_metrics=metrics,
    )
    assert verdict.decision == pruning.REMOVABLE
    assert verdict.paired_cases == 5


def test_an_item_whose_removal_breaks_cases_is_kept() -> None:
    verdict = pruning.assess_removal(
        item="skill:systematic_debugging",
        control_success=_all(True),
        treatment_success=_all(False),
    )
    assert verdict.decision == pruning.KEEP
    assert "success" in verdict.reason


def test_an_item_whose_removal_makes_resolution_dearer_is_kept() -> None:
    # Both arms pass everything — the success rate cannot tell them apart — but
    # without the item it costs twice as much to get there.
    verdict = pruning.assess_removal(
        item="doc:.github/standards.md",
        control_success=_all(True),
        treatment_success=_all(True),
        control_metrics={
            "cost_to_resolve_usd": {c: [0.30, 0.31, 0.29] for c in FIVE_CASES}
        },
        treatment_metrics={
            "cost_to_resolve_usd": {c: [0.60, 0.62, 0.61] for c in FIVE_CASES}
        },
    )
    assert verdict.decision == pruning.KEEP
    assert "cost_to_resolve_usd" in verdict.reason


def test_too_few_cases_is_inconclusive_however_clean_it_looks() -> None:
    verdict = pruning.assess_removal(
        item="skill:x",
        control_success={"only": [True] * 5},
        treatment_success={"only": [True] * 5},
    )
    assert verdict.decision == pruning.INCONCLUSIVE
    assert "case" in verdict.reason


def test_a_noisy_comparison_is_inconclusive_not_removable() -> None:
    # Absence of a difference is not evidence of equivalence: with this much
    # spread the interval reaches past the margin.
    control = {
        c: [True, False, True, False] if i % 2 else [True, True, False, False]
        for i, c in enumerate(FIVE_CASES)
    }
    treatment = {
        c: [False, True, False, False] if i % 2 else [True, False, False, False]
        for i, c in enumerate(FIVE_CASES)
    }
    verdict = pruning.assess_removal(
        item="skill:x", control_success=control, treatment_success=treatment
    )
    assert verdict.decision == pruning.INCONCLUSIVE


def test_the_margin_is_part_of_the_verdict() -> None:
    verdict = pruning.assess_removal(
        item="skill:x",
        control_success=_all(True),
        treatment_success=_all(True),
        success_margin=0.05,
    )
    assert verdict.as_dict()["margins"]["success_rate"] == 0.05


def test_metric_interval_is_paired_by_case() -> None:
    low, high = pruning.paired_mean_interval(
        {"a": [1.0, 1.0], "b": [2.0, 2.0]},
        {"a": [1.5, 1.5], "b": [2.5, 2.5]},
    )
    assert low == high == 0.5
    assert pruning.paired_mean_interval({"a": [1.0]}, {"b": [1.0]}) is None


def test_relative_cost_margin_is_measured_against_the_control() -> None:
    # +5% on a $0.30 control is within a 20% margin.
    verdict = pruning.assess_removal(
        item="skill:x",
        control_success=_all(True),
        treatment_success=_all(True),
        control_metrics=_metrics(cost=0.30),
        treatment_metrics=_metrics(cost=0.315),
    )
    assert verdict.decision == pruning.REMOVABLE


def test_a_free_model_does_not_hold_every_verdict_open() -> None:
    # Zero cost in both arms is a measurement (self-hosted is free), not a
    # missing one: the metric is satisfied, not skipped and not open.
    free = _metrics(cost=0.0)
    verdict = pruning.assess_removal(
        item="skill:x",
        control_success=_all(True),
        treatment_success=_all(True),
        control_metrics=free,
        treatment_metrics=free,
    )
    assert verdict.decision == pruning.REMOVABLE
    assert verdict.metric_intervals["cost_to_resolve_usd"]["note"] == (
        "both arms at zero"
    )


# --------------------------------------------------------------------------
# A required metric that was not measured holds the verdict open (PR #45)
# --------------------------------------------------------------------------


def test_success_alone_never_makes_an_item_removable() -> None:
    """Three clean paired cases, no per-step scores: not enough to remove."""
    verdict = pruning.assess_removal(
        item="skill:x",
        control_success=_all(True),
        treatment_success=_all(True),
    )
    assert verdict.decision == pruning.INCONCLUSIVE
    assert "cost_to_resolve_usd" in verdict.reason
    assert "progress_auc" in verdict.reason
    assert verdict.as_dict()["missing_metrics"] == [
        "cost_to_resolve_usd",
        "progress_auc",
    ]


def test_one_missing_metric_is_enough_to_hold_it_open() -> None:
    verdict = pruning.assess_removal(
        item="skill:x",
        control_success=_all(True),
        treatment_success=_all(True),
        control_metrics={"progress_auc": _flat(1.0)},
        treatment_metrics={"progress_auc": _flat(1.0)},
    )
    assert verdict.decision == pruning.INCONCLUSIVE
    assert verdict.missing_metrics == ["cost_to_resolve_usd"]


def test_a_metric_measured_in_only_one_arm_is_missing() -> None:
    verdict = pruning.assess_removal(
        item="skill:x",
        control_success=_all(True),
        treatment_success=_all(True),
        control_metrics=_metrics(),
        treatment_metrics={"progress_auc": _flat(1.0)},
    )
    assert verdict.decision == pruning.INCONCLUSIVE
    assert verdict.missing_metrics == ["cost_to_resolve_usd"]


def test_a_success_drop_is_kept_even_without_metrics() -> None:
    # Missing evidence can stop a removal; it can never excuse a regression.
    verdict = pruning.assess_removal(
        item="skill:x",
        control_success=_all(True),
        treatment_success=_all(False),
    )
    assert verdict.decision == pruning.KEEP
