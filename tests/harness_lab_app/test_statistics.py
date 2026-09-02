"""Aggregation that refuses to overstate what a handful of runs shows.

Most of these tests are about what the module *declines* to say: no spread from
one observation, no confidence interval under three, no "improved" without a
declared direction, no success rate denominated in runs that never produced a
verdict.
"""

from __future__ import annotations

import pytest

from harness_lab_app.statistics import (
    aggregate_values,
    compare,
    confidence_interval,
    evidence_strength,
    paired_by_case,
    repetition_warnings,
    success_delta,
    summarize_condition,
    verdict,
)


def test_one_observation_reports_no_spread():
    """Reporting 0.0 would claim perfect consistency from a single run."""
    aggregate = aggregate_values("cost_usd", [1.5])
    assert aggregate.mean == 1.5
    assert aggregate.stdev is None
    assert aggregate.spread == "1.5"


def test_a_spread_is_shown_next_to_every_mean():
    aggregate = aggregate_values("cost_usd", [1.0, 2.0, 3.0])
    assert aggregate.median == 2.0
    assert "±" in aggregate.spread


def test_a_missing_value_is_counted_never_imputed():
    """A run with no cost has unknown cost; zero would flatter it."""
    summary = summarize_condition("arm", [{"cost_usd": 1.0}, {"cost_usd": 3.0}, {}])
    aggregate = summary.metrics["cost_usd"]
    assert aggregate.n == 2
    assert aggregate.missing == 1
    assert aggregate.mean == 2.0


@pytest.mark.parametrize(
    ("n", "fragment"),
    [
        (0, "No runs"),
        (1, "exploratory result"),
        (3, "exploratory comparison"),
        (7, "useful comparison"),
    ],
)
def test_repetition_counts_state_what_they_do_not_support(n, fragment):
    assert fragment in repetition_warnings(n)[0]


def test_ten_repetitions_needs_no_caveat():
    assert repetition_warnings(10) == []


def test_success_rate_is_denominated_in_runs_that_produced_a_verdict():
    """A run that never finished is unknown, not a failure."""
    summary = summarize_condition(
        "arm",
        [
            {"task_validation_pass": True},
            {"task_validation_pass": False},
            {},  # never produced a verdict
        ],
    )
    assert summary.runs == 3
    assert summary.scored_runs == 2
    assert summary.success_rate == 0.5


def test_a_condition_with_no_verdicts_has_no_success_rate():
    assert summarize_condition("arm", [{}, {}]).success_rate is None


# --------------------------------------------------------------------------
# Comparison
# --------------------------------------------------------------------------


def _arm(label, costs, verdicts):
    return summarize_condition(
        label,
        [
            {"cost_usd": cost, "task_validation_pass": verdict}
            for cost, verdict in zip(costs, verdicts, strict=True)
        ],
    )


def test_a_delta_knows_which_way_is_better():
    control = _arm("native", [2.0, 2.2, 1.8], [False, False, True])
    treatment = _arm("injected", [1.0, 1.1, 0.9], [True, True, True])

    (delta,) = [
        d
        for d in compare(control, treatment, {"cost_usd": "lower_is_better"})
        if d.name == "cost_usd"
    ]
    assert delta.absolute == pytest.approx(-1.0)
    # Cheaper is better, so a negative delta is an improvement.
    assert delta.improved is True


def test_a_metric_with_no_declared_direction_makes_no_claim():
    control = _arm("a", [1.0, 1.0, 1.0], [True, True, True])
    treatment = _arm("b", [2.0, 2.0, 2.0], [True, True, True])
    (delta,) = [d for d in compare(control, treatment, {}) if d.name == "cost_usd"]
    assert delta.absolute == pytest.approx(1.0)
    assert delta.improved is None


def test_a_delta_inside_the_noise_is_flagged_as_such():
    control = _arm("a", [1.0, 3.0, 2.0], [True, True, True])
    treatment = _arm("b", [1.2, 3.2, 2.2], [True, True, True])
    (delta,) = [
        d
        for d in compare(control, treatment, {"cost_usd": "lower_is_better"})
        if d.name == "cost_usd"
    ]
    assert delta.exceeds_spread is False


def test_a_single_repetition_cannot_say_whether_a_delta_beats_the_noise():
    control = _arm("a", [1.0], [True])
    treatment = _arm("b", [5.0], [True])
    (delta,) = [
        d
        for d in compare(control, treatment, {"cost_usd": "lower_is_better"})
        if d.name == "cost_usd"
    ]
    # There is no spread to compare against, so "unknown" — not "yes".
    assert delta.exceeds_spread is None


def test_identical_repetitions_do_not_manufacture_confidence():
    """Both arms constant means a zero noise floor, not a proven delta.

    This is the shape a coarse metric takes at low repetition counts — 10,10
    against 1,1 — and comparing against a floor of 0.0 would wave it through
    as "beyond noise" on the strength of having measured the same thing twice.
    """
    control = _arm("a", [10.0, 10.0], [True, True])
    treatment = _arm("b", [1.0, 1.0], [True, True])
    (delta,) = [
        d
        for d in compare(control, treatment, {"cost_usd": "lower_is_better"})
        if d.name == "cost_usd"
    ]
    assert delta.absolute == pytest.approx(-9.0)
    assert delta.exceeds_spread is None


def test_a_real_spread_in_one_arm_still_supports_the_verdict():
    control = _arm("a", [10.0, 14.0], [True, True])
    treatment = _arm("b", [1.0, 1.0], [True, True])
    (delta,) = [
        d
        for d in compare(control, treatment, {"cost_usd": "lower_is_better"})
        if d.name == "cost_usd"
    ]
    # stdev(10,14) ≈ 2.83, and the delta of 11 clears it.
    assert delta.exceeds_spread is True


def test_success_delta_is_reported_in_percentage_points():
    control = _arm("a", [1.0, 1.0, 1.0, 1.0], [True, False, False, False])
    treatment = _arm("b", [1.0, 1.0, 1.0, 1.0], [True, True, True, False])
    assert success_delta(control, treatment)["points"] == pytest.approx(50.0)


# --------------------------------------------------------------------------
# The case matrix
# --------------------------------------------------------------------------


def test_the_case_matrix_shows_what_an_average_hides():
    """+1 net can be 'fixed four, broke three'."""
    rows = paired_by_case(
        {"c1": False, "c2": True, "c3": True, "c4": False},
        {"c1": True, "c2": True, "c3": False, "c4": False},
    )
    movements = {row["case_id"]: row["movement"] for row in rows}
    assert movements == {
        "c1": "fixed",
        "c2": "unchanged",
        "c3": "broken",
        "c4": "unchanged",
    }


def test_a_case_only_one_arm_ran_is_incomplete_not_a_regression():
    (row,) = paired_by_case({"c1": True}, {})
    assert row["movement"] == "incomplete"


# --------------------------------------------------------------------------
# Intervals
# --------------------------------------------------------------------------


@pytest.mark.parametrize("values", [[1.0], [1.0, 2.0]])
def test_no_confidence_interval_below_three_observations(values):
    assert confidence_interval(values) is None


def test_an_interval_brackets_the_mean():
    low, high = confidence_interval([1.0, 2.0, 3.0, 4.0])
    assert low < 2.5 < high


# --------------------------------------------------------------------------
# The ten-second answer (§10)
# --------------------------------------------------------------------------


def _pair(control_passes, treatment_passes, *, reps=5):
    control = _arm("native", [2.0] * reps, control_passes)
    treatment = _arm("injected", [1.5] * reps, treatment_passes)
    return control, treatment


def test_discovery_never_produces_a_causal_verdict():
    """Mode C describes a repository; it concludes nothing about a change."""
    answer = verdict(mode="discovery", cases=6, repetitions=1)

    assert answer["causal"] is False
    assert answer["headline"] == "Exploratory — no comparison"
    assert "no causal claim" in answer["claim"]
    assert answer["highlights"] == []


def test_cross_repo_reports_per_repository_and_says_so():
    """Mode B varies the repository, so the arms are not pooled into a result."""
    answer = verdict(mode="cross_repo", cases=4, repetitions=5)

    assert answer["causal"] is False
    assert answer["headline"] == "Per-repository result"
    assert any("not pooled" in caveat for caveat in answer["caveats"])


def test_a_clear_move_over_enough_repetitions_is_a_likely_improvement():
    control, treatment = _pair(
        [False, False, True, True, False], [True, True, True, True, True]
    )
    answer = verdict(
        mode="harness_evaluation",
        cases=1,
        repetitions=5,
        control=control,
        treatment=treatment,
        success=success_delta(control, treatment),
    )

    assert answer["causal"] is True
    assert answer["headline"] == "Likely improvement"
    assert answer["strength"] == "useful comparison"
    assert answer["evidence"] == "1 case \u00d7 5 repetitions"
    # Never the vocabulary of proof.
    assert "significant" not in answer["claim"].lower()


def test_the_same_move_over_two_repetitions_is_only_observed():
    control, treatment = _pair([False, False], [True, True], reps=2)
    answer = verdict(
        mode="harness_evaluation",
        cases=1,
        repetitions=2,
        control=control,
        treatment=treatment,
        success=success_delta(control, treatment),
    )

    assert answer["headline"] == "Improvement observed"
    assert answer["strength"] == "exploratory comparison"
    assert "within observed variance" in answer["caveats"][0].lower()


def test_arms_that_stopped_differently_carry_a_confound_caveat():
    """A stop criterion the arms do not share explains part of the effort gap."""
    control, treatment = _pair(
        [False, False, True, True, False], [True, True, True, True, True]
    )
    answer = verdict(
        mode="harness_evaluation",
        cases=1,
        repetitions=5,
        control=control,
        treatment=treatment,
        success=success_delta(control, treatment),
        stop_reasons={
            control.label: ["budget_exhausted", "budget_exhausted"],
            treatment.label: ["completion_signal", "completion_signal"],
        },
    )

    assert any("stopped for different reasons" in c for c in answer["caveats"])


def test_arms_that_share_a_stop_reason_carry_no_confound_caveat():
    control, treatment = _pair(
        [False, False, True, True, False], [True, True, True, True, True]
    )
    answer = verdict(
        mode="harness_evaluation",
        cases=1,
        repetitions=5,
        control=control,
        treatment=treatment,
        success=success_delta(control, treatment),
        stop_reasons={
            control.label: ["budget_exhausted", "completion_signal"],
            treatment.label: ["completion_signal"],
        },
    )

    assert not any("stopped for different reasons" in c for c in answer["caveats"])


def test_a_drop_is_named_a_regression():
    control, treatment = _pair(
        [True, True, True, True, True], [False, False, True, True, True]
    )
    answer = verdict(
        mode="harness_evaluation",
        cases=1,
        repetitions=8,
        control=control,
        treatment=treatment,
        success=success_delta(control, treatment),
    )

    assert answer["headline"] == "Likely regression"


def test_an_aggregate_gain_still_reports_the_cases_it_broke():
    """ "+8 points" must never be shown without "and broke two cases"."""
    control, treatment = _pair(
        [False, False, False, True, True], [True, True, True, True, True]
    )
    matrix = paired_by_case(
        {"a": False, "b": True, "c": True, "d": None},
        {"a": True, "b": False, "c": True, "d": None},
    )
    answer = verdict(
        mode="harness_evaluation",
        cases=4,
        repetitions=5,
        control=control,
        treatment=treatment,
        success=success_delta(control, treatment),
        case_matrix=matrix,
    )

    assert answer["movements"] == {
        "fixed": 1,
        "broken": 1,
        "unchanged": 1,
        "incomplete": 1,
    }
    assert any("fail under injected" in caveat for caveat in answer["caveats"])
    assert any("no paired outcome" in caveat for caveat in answer["caveats"])


def test_an_unscored_experiment_says_so_instead_of_guessing():
    answer = verdict(mode="harness_evaluation", cases=3, repetitions=5)

    assert answer["headline"] == "Not enough evidence yet"
    assert answer["causal"] is False


def test_headline_metrics_lead_with_success_then_cost():
    control, treatment = _pair([True] * 5, [True] * 5)
    answer = verdict(
        mode="harness_evaluation",
        cases=1,
        repetitions=5,
        control=control,
        treatment=treatment,
        success=success_delta(control, treatment),
    )

    labels = [item["label"] for item in answer["highlights"]]
    assert labels[0] == "Success"
    assert "Cost usd" in labels
    assert answer["headline"] == "No difference observed"


@pytest.mark.parametrize(
    ("reps", "word"),
    [
        (1, "exploratory"),
        (3, "exploratory comparison"),
        (6, "useful comparison"),
        (12, "stronger evidence"),
    ],
)
def test_evidence_strength_uses_prudent_words_only(reps, word):
    assert evidence_strength(reps) == word
