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
    assess_validity,
    compare,
    confidence_interval,
    evidence_strength,
    objective_signature,
    paired_bootstrap_interval,
    paired_by_case,
    pass_at_k,
    pass_k_by_arm,
    plan_repetitions,
    repetition_warnings,
    success_delta,
    summarize_condition,
    verdict,
    wilson_interval,
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
        (3, "a judgement"),
        (7, None),
    ],
)
def test_repetition_counts_state_what_they_do_not_support(n, fragment):
    warnings = repetition_warnings(n)
    if fragment is None:
        # Five or more is a validation: nothing left to caveat about the count.
        assert warnings == []
    else:
        assert fragment in warnings[0]


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
# An experiment shows its uncertainty (ADR-0064)
# --------------------------------------------------------------------------


def test_a_success_rate_interval_matches_the_wilson_score_interval():
    """3 of 5 is "60% [23-88]": the width a validation-sized arm really has."""
    low, high = wilson_interval(3, 5)
    assert low == pytest.approx(0.2307, abs=1e-3)
    assert high == pytest.approx(0.8824, abs=1e-3)


@pytest.mark.parametrize(
    ("successes", "trials", "expected"),
    [
        # A normal approximation gives [0, 0] here and claims certainty from
        # five runs. Wilson keeps the upper bound honest.
        (0, 5, (0.0, 0.4345)),
        (5, 5, (0.5655, 1.0)),
        (1, 1, (0.2065, 1.0)),
    ],
)
def test_a_unanimous_arm_still_has_an_interval(successes, trials, expected):
    low, high = wilson_interval(successes, trials)
    assert low == pytest.approx(expected[0], abs=1e-3)
    assert high == pytest.approx(expected[1], abs=1e-3)
    assert 0.0 <= low <= high <= 1.0


def test_no_interval_without_a_scored_run():
    assert wilson_interval(0, 0) is None


def test_a_condition_carries_the_interval_of_its_success_rate():
    summary = summarize_condition(
        "arm",
        [{"task_validation_pass": verdict} for verdict in (True, True, True)]
        + [{"task_validation_pass": False}, {"task_validation_pass": False}, {}],
    )
    assert summary.success_rate == pytest.approx(0.6)
    # Denominated in scored runs, like the rate it brackets.
    assert summary.success_rate_ci == wilson_interval(3, 5)
    assert summarize_condition("arm", [{}, {}]).success_rate_ci is None


def _cases(**cells):
    """`_cases(a="PPF")` -> {"a": [True, True, False]}."""
    return {case: [mark == "P" for mark in marks] for case, marks in cells.items()}


def test_the_delta_interval_is_reproducible():
    """A page that redraws a different interval on every refresh is noise."""
    control = _cases(a="PFF", b="FFF", c="PPF")
    treatment = _cases(a="PPF", b="PFF", c="PPP")

    first = paired_bootstrap_interval(control, treatment)
    second = paired_bootstrap_interval(control, treatment)

    assert first == second
    assert first is not None


def test_resampling_cases_widens_an_interval_that_one_case_would_narrow():
    """Forty runs spread over four cases are not forty independent runs.

    The treatment fixes two cases completely and leaves two untouched. Read as
    one pool of 40 runs per arm, +50 points looks settled; resampled by case,
    a different draw of four cases can show anything from 0 to +100.
    """
    control = _cases(a="F" * 10, b="F" * 10, c="F" * 10, d="F" * 10)
    treatment = _cases(a="P" * 10, b="P" * 10, c="F" * 10, d="F" * 10)
    pooled_control = {"all": [False] * 40}
    pooled_treatment = {"all": [True] * 20 + [False] * 20}

    clustered = paired_bootstrap_interval(control, treatment)
    pooled = paired_bootstrap_interval(pooled_control, pooled_treatment)

    assert clustered is not None
    assert pooled is not None
    assert clustered[0] < pooled[0]
    assert clustered[1] > pooled[1]
    assert clustered[0] <= 0.0


def test_the_bootstrap_needs_a_case_both_arms_scored():
    assert paired_bootstrap_interval(_cases(a="PP"), _cases(b="FF")) is None
    assert paired_bootstrap_interval({}, {}) is None


def _counted(label, passes, fails):
    return summarize_condition(
        label,
        [{"task_validation_pass": True}] * passes
        + [{"task_validation_pass": False}] * fails,
    )


def test_the_success_delta_carries_its_interval_in_points():
    control = _counted("native", 1, 4)
    treatment = _counted("injected", 4, 1)

    delta = success_delta(
        control,
        treatment,
        control_cases=_cases(a="PFFFF"),
        treatment_cases=_cases(a="PPPPF"),
    )

    assert delta["points"] == pytest.approx(60.0)
    assert delta["delta_ci_low"] < delta["points"] < delta["delta_ci_high"]
    assert delta["paired_cases"] == 1
    assert delta["interval_crosses_zero"] is (delta["delta_ci_low"] <= 0.0)


def test_a_tie_over_five_runs_says_its_interval_includes_zero():
    control = _counted("native", 3, 2)
    treatment = _counted("injected", 3, 2)

    delta = success_delta(
        control,
        treatment,
        control_cases=_cases(a="PPPFF"),
        treatment_cases=_cases(a="PPFPF"),
    )

    assert delta["points"] == pytest.approx(0.0)
    assert delta["delta_ci_low"] < 0.0 < delta["delta_ci_high"]
    assert delta["interval_crosses_zero"] is True


def test_a_unanimous_split_is_not_reported_as_certain():
    """0/5 against 5/5 resamples to +100 every time — the bootstrap's blind spot.

    Every repetition agrees, so a plain bootstrap has nothing to resample and
    returns [+100, +100]: certainty from ten runs. The interval is never
    narrower than Newcombe's, which is built from the arms' own Wilson
    intervals and does not collapse at 0/n or n/n.
    """
    control = _counted("native", 0, 5)
    treatment = _counted("injected", 5, 0)

    delta = success_delta(
        control,
        treatment,
        control_cases=_cases(a="FFFFF"),
        treatment_cases=_cases(a="PPPPP"),
    )

    assert delta["delta_ci_high"] == pytest.approx(100.0)
    assert delta["delta_ci_low"] == pytest.approx(38.6, abs=0.5)


def test_the_delta_interval_without_per_case_outcomes_still_brackets_the_point():
    """Callers that only hold the arm totals still get an interval."""
    control = _counted("native", 2, 3)
    treatment = _counted("injected", 4, 1)

    delta = success_delta(control, treatment)

    assert delta["paired_cases"] == 0
    assert delta["delta_ci_low"] < 40.0 < delta["delta_ci_high"]


def test_no_delta_interval_until_both_arms_are_scored():
    delta = success_delta(_counted("a", 2, 1), summarize_condition("b", [{}]))
    assert delta["points"] is None
    assert delta["delta_ci_low"] is None
    assert delta["delta_ci_high"] is None
    assert delta["interval_crosses_zero"] is None


def test_pass_hat_k_is_strict_where_pass_at_k_is_lenient():
    """pass^k is "every one of k tries passes" — the reliability a user feels."""
    result = pass_at_k(_cases(a="PPP", b="PFF"))

    assert result == {
        "k": 3,
        "cases": 2,
        "pass_at_k": pytest.approx(1.0),
        "pass_hat_k": pytest.approx(0.5),
    }


def test_pass_at_k_uses_the_unbiased_estimator_when_a_case_has_spare_runs():
    """With n > k the estimator averages over every k-subset of the n runs."""
    result = pass_at_k(_cases(a="PPFF"), k=2)

    # pass^2 = C(2,2)/C(4,2); pass@2 = 1 - C(2,2)/C(4,2).
    assert result["pass_hat_k"] == pytest.approx(1 / 6)
    assert result["pass_at_k"] == pytest.approx(5 / 6)


def test_pass_at_one_is_the_mean_case_rate():
    result = pass_at_k(_cases(a="PF", b="PPPF"), k=1)
    assert result["pass_at_k"] == pytest.approx((0.5 + 0.75) / 2)
    assert result["pass_hat_k"] == result["pass_at_k"]


def test_every_arm_is_read_at_the_same_k():
    """pass^3 against pass^2 compares two different questions."""
    arms = pass_k_by_arm(
        {"control": _cases(a="PPP", b="PFP"), "treatment": _cases(a="PP", b="PP")}
    )

    assert arms["control"]["k"] == arms["treatment"]["k"] == 2
    assert arms["treatment"]["pass_hat_k"] == pytest.approx(1.0)


def test_an_arm_with_no_scored_case_has_no_pass_k():
    assert pass_at_k({}) is None
    assert pass_k_by_arm({"a": {}, "b": _cases(x="P")})["a"] is None


def test_a_case_with_fewer_runs_than_k_is_left_out_not_padded():
    """pass^3 over two runs would have to invent the third."""
    assert pass_at_k(_cases(a="PP"), k=3) is None
    result = pass_at_k(_cases(a="PP", b="PPF"), k=3)
    assert result is not None
    assert result["cases"] == 1
    assert result["pass_hat_k"] == pytest.approx(0.0)


def test_with_too_few_runs_the_plan_assumes_a_coin_flip():
    """Below ten scored runs an observed rate is too loose to plan from."""
    plan = plan_repetitions([_counted("a", 4, 0), _counted("b", 0, 4)], cases=1)

    assert plan["basis"] == "assumed"
    assert plan["base_rate"] == 0.5
    assert plan["difference_points"] == 20
    assert plan["runs_per_arm"] == 93
    assert plan["repetitions_per_case"] == 93


def test_the_plan_spreads_its_runs_over_the_cases():
    plan = plan_repetitions([_counted("a", 1, 1)], cases=3)
    assert plan["runs_per_arm"] == 93
    assert plan["repetitions_per_case"] == 31


def test_an_observed_rate_far_from_half_needs_fewer_runs():
    plan = plan_repetitions([_counted("a", 12, 0), _counted("b", 11, 1)], cases=1)

    assert plan["basis"] == "observed"
    # The noisier of the two observed arms sets the plan, never the calmer one.
    assert plan["base_rate"] == pytest.approx(11 / 12)
    assert 0 < plan["runs_per_arm"] < 93


def test_an_arm_without_enough_runs_keeps_the_plan_conservative():
    plan = plan_repetitions([_counted("a", 12, 0), _counted("b", 1, 0)], cases=1)
    assert plan["basis"] == "assumed"
    assert plan["runs_per_arm"] == 93


def test_no_plan_for_a_design_without_cases():
    assert plan_repetitions([_counted("a", 1, 1)], cases=0) is None


def test_a_delta_interval_that_includes_zero_is_said_in_the_verdict():
    control, treatment = _pair(
        [True, False, True, False, False], [True, True, False, True, False]
    )
    answer = verdict(
        mode="harness_evaluation",
        cases=1,
        repetitions=5,
        control=control,
        treatment=treatment,
        success=success_delta(
            control,
            treatment,
            control_cases=_cases(a="PFPFF"),
            treatment_cases=_cases(a="PPFPF"),
        ),
    )

    caveat = next(c for c in answer["caveats"] if "95% interval" in c)
    assert "includes zero" in caveat
    # Descriptive, never the vocabulary of a test.
    assert not any("significan" in c.lower() for c in answer["caveats"])


def test_an_interval_clear_of_zero_adds_no_interval_caveat():
    control, treatment = _pair([False] * 5, [True] * 5)
    answer = verdict(
        mode="harness_evaluation",
        cases=1,
        repetitions=5,
        control=control,
        treatment=treatment,
        success=success_delta(control, treatment),
    )

    assert not any("includes zero" in c for c in answer["caveats"])


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
    assert answer["strength"] == "validation"
    assert answer["evidence"] == "1 case \u00d7 5 repetitions"
    # Never the vocabulary of proof.
    assert "significant" not in answer["claim"].lower()


def test_two_repetitions_are_a_judgement():
    """Two per arm is the budget-sized level that earns a directional headline."""
    control, treatment = _pair([False, False], [True, True], reps=2)
    answer = verdict(
        mode="harness_evaluation",
        cases=1,
        repetitions=2,
        control=control,
        treatment=treatment,
        success=success_delta(control, treatment),
    )

    assert answer["headline"] == "Likely improvement"
    assert answer["strength"] == "judgement"
    assert any("judgement" in caveat for caveat in answer["caveats"])


def test_the_same_move_over_one_repetition_is_only_observed():
    control, treatment = _pair([False], [True], reps=1)
    answer = verdict(
        mode="harness_evaluation",
        cases=1,
        repetitions=1,
        control=control,
        treatment=treatment,
        success=success_delta(control, treatment),
    )

    assert answer["headline"] == "Improvement observed"
    assert answer["strength"] == "exploratory"


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
    # Success tied, cost did not: `_pair` gives the treatment 1.5 against 2.0.
    assert answer["headline"] == "Same outcome, lower cost"


@pytest.mark.parametrize(
    ("reps", "word"),
    [
        (1, "exploratory"),
        (3, "judgement"),
        (6, "validation"),
        (12, "validation"),
    ],
)
def test_evidence_strength_uses_prudent_words_only(reps, word):
    assert evidence_strength(reps) == word


# --------------------------------------------------------------------------
# Subject validation — a pass rate per arm, and no claim beyond it (ADR-0040)
# --------------------------------------------------------------------------


def test_subject_validation_reports_each_arm_and_never_pools_them():
    """The sentence this mode licenses names the subject and one arm at a time.

    Pooling "8 of 9 under claude" with "4 of 9 under opencode" into "12 of 18"
    would describe a configuration nobody runs — the exact composite the lab
    refuses everywhere else.
    """
    arms = [
        _arm("claude/sonnet", [1.0] * 9, [True] * 8 + [False]),
        _arm("opencode/sonnet", [1.0] * 9, [True] * 4 + [False] * 5),
    ]
    answer = verdict(
        mode="subject_validation",
        cases=3,
        repetitions=3,
        subject="notion-export skill",
        arms=arms,
    )

    assert answer["causal"] is False
    assert answer["headline"] == "Reliability per arm"
    assert "notion-export skill passed 8 of 9 under claude/sonnet" in answer["claim"]
    assert "4 of 9 under opencode/sonnet" in answer["claim"]
    # One highlight per arm, each carrying its own numerator and denominator.
    assert [h["label"] for h in answer["highlights"]] == [
        "claude/sonnet",
        "opencode/sonnet",
    ]
    assert [(h["successes"], h["scored_runs"]) for h in answer["highlights"]] == [
        (8, 9),
        (4, 9),
    ]
    assert any("says nothing about the harness" in c for c in answer["caveats"])


def test_subject_validation_names_every_arm_even_on_a_tie():
    """Two arms at the same rate are two arms, not one arm twice."""
    answer = verdict(
        mode="subject_validation",
        cases=1,
        repetitions=3,
        subject="the exporter",
        arms=[
            _arm("claude", [1.0] * 3, [True] * 3),
            _arm("opencode", [1.0] * 3, [True] * 3),
        ],
    )

    assert "3 of 3 under claude and 3 of 3 under opencode" in answer["claim"]


def test_subject_validation_with_one_arm_states_it_plainly():
    answer = verdict(
        mode="subject_validation",
        cases=1,
        repetitions=5,
        subject="the MCP server",
        arms=[_arm("claude", [1.0] * 5, [True] * 5)],
    )

    assert "the MCP server passed 5 of 5 scored run(s) under claude" in answer["claim"]


def test_subject_validation_keeps_its_disclaimers_before_anything_is_scored():
    """The design caveats hold at zero runs — when they matter most."""
    answer = verdict(
        mode="subject_validation",
        cases=1,
        repetitions=3,
        subject="the MCP server",
        arms=[_arm("claude", [], [])],
    )

    assert answer["headline"] == "Not enough evidence yet"
    assert answer["causal"] is False
    assert any("not pooled" in c for c in answer["caveats"])


# --------------------------------------------------------------------------
# Validity — read before the verdict
# --------------------------------------------------------------------------


def _codes(validity):
    return [issue["code"] for issue in validity["issues"]]


def test_a_treatment_arm_that_never_invoked_its_skill_invalidates_the_comparison():
    """The treatment did not happen, so the delta is about something else."""
    control, treatment = _pair([False] * 5, [True] * 5)
    validity = assess_validity(
        mode="harness_evaluation",
        control=control,
        treatment=treatment,
        success=success_delta(control, treatment),
        skill_usage={treatment.label: {"skills": ["caveman"], "runs": 5, "invoked": 0}},
    )
    assert validity["status"] == "invalid"
    assert _codes(validity) == ["treatment_not_activated"]
    assert validity["issues"][0]["arm"] == treatment.label


def test_a_control_arm_that_opened_nothing_is_not_an_issue_by_itself():
    """An ablated arm has no skills to open — the asymmetry is the design."""
    control, treatment = _pair([False] * 5, [True] * 5)
    validity = assess_validity(
        mode="harness_evaluation",
        control=control,
        treatment=treatment,
        success=success_delta(control, treatment),
        skill_usage={control.label: {"skills": [], "runs": 5, "invoked": 0}},
    )
    assert validity["status"] == "valid"
    assert validity["issues"] == []


def test_a_control_that_passes_everything_leaves_no_headroom():
    control, treatment = _pair([True] * 5, [True] * 5)
    validity = assess_validity(
        mode="harness_evaluation",
        control=control,
        treatment=treatment,
        success=success_delta(control, treatment),
    )
    assert validity["status"] == "invalid"
    assert _codes(validity) == ["no_headroom"]


def test_a_ceiling_that_still_measured_a_regression_only_degrades():
    """A regression *was* detectable, so the design answered something."""
    control, treatment = _pair([True] * 5, [True, True, True, True, False])
    validity = assess_validity(
        mode="harness_evaluation",
        control=control,
        treatment=treatment,
        success=success_delta(control, treatment),
    )
    assert validity["status"] == "degraded"
    assert _codes(validity) == ["no_headroom"]


def test_a_two_run_ceiling_is_evidence_of_two_runs_not_of_a_ceiling():
    control, treatment = _pair([True, True], [True, True], reps=2)
    validity = assess_validity(
        mode="harness_evaluation",
        control=control,
        treatment=treatment,
        success=success_delta(control, treatment),
    )
    assert validity["status"] == "degraded"


def test_an_arm_whose_runs_mostly_crashed_measured_the_connection():
    control, treatment = _pair([False] * 5, [True] * 5)
    validity = assess_validity(
        mode="harness_evaluation",
        control=control,
        treatment=treatment,
        success=success_delta(control, treatment),
        run_health={
            treatment.label: {
                "runs": 4,
                "harness_error": 4,
                "failed": 4,
                "scored_runs": 0,
            }
        },
    )
    assert validity["status"] == "invalid"
    assert "infrastructure_failures" in _codes(validity)


def test_one_bad_run_out_of_five_degrades_rather_than_invalidates():
    control, treatment = _pair([False] * 5, [True] * 5)
    validity = assess_validity(
        mode="harness_evaluation",
        control=control,
        treatment=treatment,
        success=success_delta(control, treatment),
        run_health={
            treatment.label: {
                "runs": 5,
                "harness_error": 1,
                "failed": 1,
                "scored_runs": 4,
            }
        },
    )
    assert validity["status"] == "degraded"


def _paired(n_cases: int) -> tuple:
    """A design with ``n_cases`` paired cases of five repetitions each."""
    control_cases = {
        f"c{i}": [False, False, True, False, False] for i in range(n_cases)
    }
    treatment_cases = {f"c{i}": [True, True, False, True, True] for i in range(n_cases)}
    control = _counted("native", n_cases, 4 * n_cases)
    treatment = _counted("injected", 4 * n_cases, n_cases)
    success = success_delta(
        control,
        treatment,
        control_cases=control_cases,
        treatment_cases=treatment_cases,
    )
    return control, treatment, success


@pytest.mark.parametrize("n_cases", [1, 4])
def test_few_paired_cases_bound_the_reading_without_breaking_it(n_cases):
    """Below five cases the interval describes the cases that ran.

    A Monte Carlo check of this module saw the delta interval cover the
    population effect ~83% of the time at 4 cases x 24 repetitions with an
    uneven effect. That is a reading caveat, not a broken comparison: it
    degrades, never invalidates, and it is listed last.
    """
    control, treatment, success = _paired(n_cases)
    validity = assess_validity(
        mode="harness_evaluation",
        control=control,
        treatment=treatment,
        success=success,
    )
    assert validity["status"] == "degraded"
    assert _codes(validity) == ["few_cases"]
    issue = validity["issues"][0]
    assert issue["severity"] == "degraded"
    assert f"Only {n_cases} paired case" in issue["detail"]


def test_five_paired_cases_raise_no_few_cases_issue():
    control, treatment, success = _paired(5)
    validity = assess_validity(
        mode="harness_evaluation",
        control=control,
        treatment=treatment,
        success=success,
    )
    assert "few_cases" not in _codes(validity)


def test_a_delta_with_no_per_case_outcomes_is_not_called_few_cases():
    """Zero paired cases means none were recorded — a different gap."""
    control, treatment = _pair([False] * 5, [True] * 5)
    validity = assess_validity(
        mode="harness_evaluation",
        control=control,
        treatment=treatment,
        success=success_delta(control, treatment),
    )
    assert "few_cases" not in _codes(validity)


def test_few_cases_sorts_after_an_invalidating_issue():
    control, treatment, success = _paired(2)
    validity = assess_validity(
        mode="harness_evaluation",
        control=control,
        treatment=treatment,
        success=success,
        skill_usage={
            treatment.label: {"skills": ["caveman"], "runs": 10, "invoked": 0}
        },
    )
    assert validity["status"] == "invalid"
    assert _codes(validity) == ["treatment_not_activated", "few_cases"]


def test_a_mode_that_makes_no_causal_claim_has_nothing_to_invalidate():
    for mode in ("discovery", "cross_repo"):
        validity = assess_validity(
            mode=mode,
            skill_usage={"arm": {"skills": ["caveman"], "runs": 5, "invoked": 0}},
        )
        assert validity == {"status": "valid", "issues": []}


def test_subject_validation_reports_its_issues_but_never_invalidates():
    """It has no control arm, so there is no comparison to break."""
    validity = assess_validity(
        mode="subject_validation",
        skill_usage={"claude": {"skills": ["driver"], "runs": 3, "invoked": 0}},
    )
    assert validity["status"] == "degraded"
    assert _codes(validity) == ["treatment_not_activated"]


def test_an_invalid_verdict_replaces_the_headline_and_keeps_the_numbers():
    """ADR-0041: the numbers are true. They stop leading, they do not vanish."""
    control, treatment = _pair([False] * 5, [True] * 5)
    success = success_delta(control, treatment)
    validity = assess_validity(
        mode="harness_evaluation",
        control=control,
        treatment=treatment,
        success=success,
        skill_usage={treatment.label: {"skills": ["caveman"], "runs": 5, "invoked": 0}},
    )
    answer = verdict(
        mode="harness_evaluation",
        cases=1,
        repetitions=5,
        control=control,
        treatment=treatment,
        success=success,
        validity=validity,
    )
    assert answer["headline"] == "Comparison not valid"
    assert "improvement" not in answer["headline"].lower()
    assert "regression" not in answer["headline"].lower()
    assert "+100 points" in answer["observed"]
    assert answer["tone"] == "invalid"
    assert answer["highlights"]
    assert answer["movements"] == {
        "fixed": 0,
        "broken": 0,
        "unchanged": 0,
        "incomplete": 0,
    }


def test_a_valid_verdict_carries_a_tone_the_frontend_can_read():
    control, treatment = _pair([False] * 5, [True] * 5)
    answer = verdict(
        mode="harness_evaluation",
        cases=1,
        repetitions=5,
        control=control,
        treatment=treatment,
        success=success_delta(control, treatment),
        validity={"status": "valid", "issues": []},
    )
    assert answer["tone"] == "better"
    assert "observed" not in answer


# --------------------------------------------------------------------------
# Highlights carry their direction (2026-09-08)
# --------------------------------------------------------------------------


def test_highlights_say_which_way_is_good():
    """ "10,712 → 7,223 tokens" is an improvement for a cost and a regression for
    a rate; the page used to leave that to the reader."""
    from harness_lab_app.statistics import _highlight, summarize_condition

    control = summarize_condition(
        "c",
        [{"task_validation_pass": True, "billed_tokens": 900_000.0}],
    )
    treatment = summarize_condition(
        "t",
        [{"task_validation_pass": True, "billed_tokens": 200_000.0}],
    )

    found = _highlight("Billed tokens", "billed_tokens", control, treatment)

    assert found is not None
    assert found["direction"] == "lower_is_better"


def test_headline_metrics_lead_with_the_graded_outcome_and_the_billed_cost():
    from harness_lab_app.statistics import HEADLINE_METRICS

    assert HEADLINE_METRICS[0] == "test_pass_rate"
    assert "billed_tokens" in HEADLINE_METRICS
    # The uncached count hid a 71% cost increase behind a lower number.
    assert "total_tokens" not in HEADLINE_METRICS


# --------------------------------------------------------------------------
# The floor, which is the ceiling's mirror (ADR-0062)
# --------------------------------------------------------------------------


def _signature(command: str, exit_code: int = 2):
    return objective_signature(
        {
            "checks": [
                {
                    "command": command,
                    "exit_code": exit_code,
                    "passed": False,
                    "conclusive": True,
                }
            ]
        }
    )


def _floor_validity(control, treatment, **kwargs):
    return assess_validity(
        mode="harness_evaluation",
        control=control,
        treatment=treatment,
        arms=[control, treatment],
        success=success_delta(control, treatment),
        **kwargs,
    )


def test_two_arms_that_never_pass_cannot_be_ranked():
    """0.0 versus 0.0 is a delta of exactly zero, and reads as 'no difference'.

    That is how `audit-ml-python-base` published "No difference observed" over
    two arms whose every check had failed on the lab's own Makefile.
    """
    control, treatment = _pair([False] * 5, [False] * 5)
    validity = _floor_validity(control, treatment)
    assert validity["status"] == "invalid"
    assert _codes(validity) == ["floor_effect"]


def test_a_floor_at_two_runs_only_degrades():
    """Two failing runs are evidence of two runs, exactly as for a ceiling."""
    control, treatment = _pair([False, False], [False, False], reps=2)
    validity = _floor_validity(control, treatment)
    assert validity["status"] == "degraded"
    assert _codes(validity) == ["floor_effect"]


def test_an_identical_failing_check_on_every_run_is_the_environment():
    """Two different arms failing the same command the same way decided nothing."""
    control, treatment = _pair([False, False], [False, False], reps=2)
    signature = _signature("cd {run_id}/workspace && make test")
    validity = _floor_validity(
        control,
        treatment,
        check_signatures={
            control.label: [signature, signature],
            treatment.label: [signature, signature],
        },
    )
    assert validity["status"] == "invalid"
    assert "make test" in validity["issues"][0]["detail"]
    assert "the environment decided this" in validity["issues"][0]["detail"]


def test_a_control_at_zero_against_a_treatment_that_passes_is_not_a_floor():
    """One arm moving is exactly the result the design exists to find."""
    control, treatment = _pair([False] * 5, [True] * 5)
    assert _codes(_floor_validity(control, treatment)) == []


def test_a_floor_and_a_ceiling_cannot_both_hold():
    """The control is either 0/n or n/n; the two rules never fire together."""
    control, treatment = _pair([True] * 5, [True] * 5)
    assert _codes(_floor_validity(control, treatment)) == ["no_headroom"]


def test_a_single_failing_run_per_arm_is_not_yet_a_floor():
    control, treatment = _pair([False], [False], reps=1)
    assert _codes(_floor_validity(control, treatment)) == []


def test_subject_validation_caps_the_floor_at_degraded():
    """It has no control, so there is no comparison to invalidate."""
    arm = _arm("claude", [1.0] * 5, [False] * 5)
    validity = assess_validity(mode="subject_validation", arms=[arm])
    assert validity["status"] == "degraded"
    assert _codes(validity) == ["floor_effect"]


# --------------------------------------------------------------------------
# An objective nobody measured
# --------------------------------------------------------------------------


def _unmeasured_health(runs=2):
    return {
        "runs": runs,
        "harness_error": 0,
        "failed": 0,
        "unhealthy": 0,
        "scored_runs": 0,
        "unmeasured": runs,
    }


def test_an_experiment_whose_every_check_was_inconclusive_measured_nothing():
    """Nothing else fires: the runs did not crash, and there is no rate to floor."""
    control, treatment = _pair([], [], reps=0)
    validity = _floor_validity(
        control,
        treatment,
        run_health={
            control.label: _unmeasured_health(),
            treatment.label: _unmeasured_health(),
        },
        check_signatures={
            control.label: [((), ("no_network",))],
            treatment.label: [((), ("not_found",))],
        },
    )
    assert validity["status"] == "invalid"
    assert _codes(validity) == ["objective_unmeasured"]
    detail = validity["issues"][0]["detail"]
    assert "command not found" in detail and "no network" in detail


def test_pending_runs_do_not_read_as_unmeasured():
    """`summarize` is polled while runs are still queued.

    Gating on absence would render a freshly launched experiment as "not valid"
    for its first minutes.
    """
    control, treatment = _pair([], [], reps=0)
    half = {**_unmeasured_health(), "runs": 4, "unmeasured": 2}
    validity = _floor_validity(
        control, treatment, run_health={control.label: half, treatment.label: half}
    )
    assert _codes(validity) == []


def test_one_arm_that_still_measured_something_is_not_an_unmeasured_experiment():
    control = _arm("native", [2.0] * 2, [True] * 2)
    scored = {**_unmeasured_health(), "scored_runs": 2, "unmeasured": 0}
    validity = assess_validity(
        mode="harness_evaluation",
        control=control,
        arms=[control],
        run_health={control.label: scored},
    )
    assert "objective_unmeasured" not in _codes(validity)


def test_unmeasured_leads_the_floor_in_the_ordering():
    """A reader told "nothing resolved" first goes looking for a harder case."""
    arm = _arm("solo", [1.0] * 5, [False] * 5)
    validity = assess_validity(
        mode="harness_evaluation",
        control=arm,
        arms=[arm],
        run_health={arm.label: {**_unmeasured_health(runs=5), "runs": 5}},
    )
    assert _codes(validity) == ["objective_unmeasured", "floor_effect"]


# --------------------------------------------------------------------------
# The signature two runs are compared on
# --------------------------------------------------------------------------


def test_a_signature_forgets_the_run_id_so_two_runs_can_match():
    """Every workspace path carries the run id; raw strings would never match."""

    def objective(run_id):
        return {
            "checks": [
                {
                    "command": f"cd data/runs/{run_id}/workspace && make test",
                    "exit_code": 2,
                    "passed": False,
                    "conclusive": True,
                }
            ]
        }

    assert objective_signature(objective("K-001"), "K-001") == objective_signature(
        objective("K-002"), "K-002"
    )


def test_a_signature_keeps_inconclusive_checks_out_of_the_failures():
    """A command that never ran did not fail (ADR-0048)."""
    signature = objective_signature(
        {
            "checks": [
                {
                    "command": "make test",
                    "exit_code": 127,
                    "passed": False,
                    "conclusive": False,
                    "inconclusive_reason": "not_found",
                },
                {
                    "command": "pytest",
                    "exit_code": 1,
                    "passed": False,
                    "conclusive": True,
                },
            ]
        }
    )
    assert signature == ((("pytest", 1),), ("not_found",))


def test_a_run_with_no_objective_has_an_empty_signature():
    assert objective_signature(None) == ((), ())


# --------------------------------------------------------------------------
# A tie on success is not a tie on what the success cost
# --------------------------------------------------------------------------


def _priced(label, *, cost, tokens, steps, minutes, passes=True, reps=1):
    return summarize_condition(
        label,
        [
            {
                "cost_usd": cost,
                "billed_tokens": tokens,
                "agent_steps": steps,
                "wall_time_minutes": minutes,
                "task_validation_pass": passes,
            }
        ]
        * reps,
    )


def _tie(control, treatment, stop_reasons=None):
    return verdict(
        mode="harness_evaluation",
        cases=1,
        repetitions=1,
        control=control,
        treatment=treatment,
        success=success_delta(control, treatment),
        case_matrix=[],
        stop_reasons=stop_reasons,
    )


def test_a_tie_on_success_still_names_what_the_outcome_cost():
    """Both arms resolved the case; one did it for a third less of everything.

    "No difference observed" is a sentence about the success rate, and printing
    it over that pair tells the reader the opposite of what the page shows.
    """
    control = _priced("native", cost=1.78, tokens=10_400_000, steps=6, minutes=12.3)
    treatment = _priced("injected", cost=1.25, tokens=6_890_000, steps=4, minutes=7.91)

    answer = _tie(control, treatment)

    assert answer["headline"] == "Same outcome, lower cost"
    assert "matched native on success rate" in answer["claim"]
    assert "fell" in answer["claim"]


def test_a_tie_where_the_treatment_cost_more_says_so():
    control = _priced("native", cost=1.0, tokens=1_000_000, steps=3, minutes=5.0)
    treatment = _priced("injected", cost=2.0, tokens=2_000_000, steps=6, minutes=10.0)

    assert _tie(control, treatment)["headline"] == "Same outcome, higher cost"


def test_a_tie_with_nothing_moving_is_still_no_difference():
    control = _priced("native", cost=1.0, tokens=1_000_000, steps=4, minutes=6.0)
    treatment = _priced("injected", cost=1.0, tokens=1_000_000, steps=4, minutes=6.0)

    answer = _tie(control, treatment)

    assert answer["headline"] == "No difference observed"
    assert "no efficiency metric moved materially" in answer["claim"]


def test_a_move_under_ten_percent_is_not_worth_a_headline():
    """A floor on the wording, like `_MATERIAL_POINTS` — never a significance claim."""
    control = _priced("native", cost=1.00, tokens=1_000_000, steps=4, minutes=6.00)
    treatment = _priced("injected", cost=1.05, tokens=1_040_000, steps=4, minutes=6.20)

    assert _tie(control, treatment)["headline"] == "No difference observed"


def test_a_tie_whose_arms_stopped_differently_does_not_credit_the_direction():
    """Effort and cost partly measure the stop criterion, so the headline may
    say that they moved and may not say who won."""
    control = _priced("native", cost=1.78, tokens=10_400_000, steps=6, minutes=12.3)
    treatment = _priced("injected", cost=1.25, tokens=6_890_000, steps=4, minutes=7.91)

    answer = _tie(
        control,
        treatment,
        stop_reasons={"native": ["no_progress"], "injected": ["completion_signal"]},
    )

    assert answer["headline"] == "Same outcome, different cost"
    assert "cannot be credited to the harness" in answer["claim"]


def test_a_tie_with_metrics_moving_both_ways_says_mixed():
    control = _priced("native", cost=1.0, tokens=2_000_000, steps=6, minutes=12.0)
    treatment = _priced("injected", cost=2.0, tokens=1_000_000, steps=3, minutes=6.0)

    assert _tie(control, treatment)["headline"] == "Same outcome, mixed efficiency"


def test_the_outcome_metrics_are_not_read_as_a_price():
    """`test_pass_rate` is the result, not what the result cost."""
    control = summarize_condition(
        "native", [{"test_pass_rate": 1.0, "task_validation_pass": True}]
    )
    treatment = summarize_condition(
        "injected", [{"test_pass_rate": 0.5, "task_validation_pass": True}]
    )

    assert _tie(control, treatment)["headline"] == "No difference observed"
