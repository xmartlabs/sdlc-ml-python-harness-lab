"""Small-model capability (ADR-0069): reliable, not just possible, per difficulty tier."""

from __future__ import annotations

import pytest

from harness_lab_app.capability import (
    CAPABILITY,
    NOT_ESTABLISHED,
    RELIABLE,
    TRANSPORT,
    capability_frontier,
    classify_failure_text,
    tier_reliability,
)


def _case(passes: int, runs: int = 5) -> list[bool]:
    return [True] * passes + [False] * (runs - passes)


# --- failure classes -----------------------------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        "AI_InvalidToolInputError: invalid input for tool edit",
        "NoSuchToolError: model tried to call unavailable tool 'write_file'",
        "Error: request exceeds the context window (n_ctx: 8192)",
        "This model's maximum context length is 32768 tokens",
        "json: cannot unmarshal object into string tool argument",
    ],
)
def test_transport_failures_are_not_charged_to_capability(text):
    """A malformed tool call or a context overflow says the model-harness pair
    could not *talk*, not that the model could not *solve* — 2026 studies of
    small local models find these are the majority of losses."""
    assert classify_failure_text(text) == TRANSPORT


def test_a_wrong_answer_is_a_capability_failure():
    assert (
        classify_failure_text("AssertionError: expected 3 appointments, got 2")
        == CAPABILITY
    )


def test_no_text_is_no_class():
    assert classify_failure_text("") is None


# --- reliability per tier ---------------------------------------------------------


def test_every_case_passing_four_of_five_is_reliable():
    cases = {f"c{i}": _case(5) for i in range(3)} | {"c3": _case(4)}
    result = tier_reliability(cases)
    assert result["status"] == RELIABLE
    assert result["wilson_low"] >= 0.70


def test_one_case_below_four_of_five_breaks_reliability_however_good_the_rest():
    cases = {f"c{i}": _case(5) for i in range(5)} | {"weak": _case(3)}
    result = tier_reliability(cases)
    assert result["status"] == NOT_ESTABLISHED
    assert "weak" in result["reason"]


def test_three_out_of_three_is_capable_not_reliable():
    """3/3 has a Wilson lower bound of 0.44: it shows the model can, not that it does."""
    cases = {f"c{i}": _case(3, runs=3) for i in range(4)}
    result = tier_reliability(cases)
    assert result["status"] != RELIABLE
    assert result["capable"] is True


def test_too_few_cases_cannot_establish_a_tier():
    result = tier_reliability({"only": _case(5)})
    assert result["status"] == NOT_ESTABLISHED
    assert "cases" in result["reason"]


def test_pass_hat_k_uses_the_unbiased_estimator():
    result = tier_reliability({f"c{i}": _case(4) for i in range(4)})
    # C(4,3)/C(5,3) = 4/10 per case
    assert result["pass_hat_3"] == pytest.approx(0.4)


# --- the frontier --------------------------------------------------------------


def test_the_frontier_is_the_highest_tier_reached_without_a_gap():
    tiers = {
        "T1": {f"c{i}": _case(5) for i in range(4)},
        "T2": {f"c{i}": _case(5) for i in range(4)},
        "T3": {f"c{i}": _case(1) for i in range(4)},
    }
    frontier = capability_frontier(tiers, order=["T1", "T2", "T3"])
    assert frontier["frontier"] == "T2"
    assert frontier["tiers"]["T3"]["status"] == NOT_ESTABLISHED


def test_a_reliable_higher_tier_after_a_failed_lower_one_does_not_raise_the_frontier():
    tiers = {
        "T1": {f"c{i}": _case(1) for i in range(4)},
        "T2": {f"c{i}": _case(5) for i in range(4)},
    }
    assert capability_frontier(tiers, order=["T1", "T2"])["frontier"] is None


def test_a_tier_with_too_few_cases_still_says_whether_the_runs_passed():
    """One case at 5/5 cannot be *reliable* (too few cases), but it is not the same
    as one at 0/5: the reading must keep 'it passes every time' visible."""
    strong = tier_reliability({"only": _case(5)})
    weak = tier_reliability({"only": _case(0)})
    assert strong["status"] == NOT_ESTABLISHED and weak["status"] == NOT_ESTABLISHED
    assert strong["runs_ok"] is True
    assert weak["runs_ok"] is False


def test_the_frontier_names_the_case_shortfall():
    tiers = {"1": {"a": _case(5)}, "2": {"b": _case(5)}}
    frontier = capability_frontier(tiers, order=["1", "2"])
    assert frontier["case_shortfall"] == {"1": 3, "2": 3}
    assert frontier["frontier_if_enough_cases"] == "2"
