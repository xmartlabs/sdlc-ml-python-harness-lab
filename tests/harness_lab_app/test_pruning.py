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


def test_an_item_whose_removal_changes_nothing_is_removable() -> None:
    cost = {case: [0.30, 0.31, 0.29, 0.30] for case in FIVE_CASES}
    verdict = pruning.assess_removal(
        item="skill:brainstorm_quick",
        control_success=_all(True),
        treatment_success=_all(True),
        control_metrics={"cost_to_resolve_usd": cost},
        treatment_metrics={"cost_to_resolve_usd": cost},
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
        control_metrics={"cost_to_resolve_usd": {c: [0.30] * 3 for c in FIVE_CASES}},
        treatment_metrics={"cost_to_resolve_usd": {c: [0.315] * 3 for c in FIVE_CASES}},
    )
    assert verdict.decision == pruning.REMOVABLE


def test_a_free_model_does_not_hold_every_verdict_open() -> None:
    free = {c: [0.0, 0.0, 0.0] for c in FIVE_CASES}
    verdict = pruning.assess_removal(
        item="skill:x",
        control_success=_all(True),
        treatment_success=_all(True),
        control_metrics={"cost_to_resolve_usd": free},
        treatment_metrics={"cost_to_resolve_usd": free},
    )
    assert verdict.decision == pruning.REMOVABLE
    assert "cost_to_resolve_usd" not in verdict.metric_intervals
