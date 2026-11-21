"""A subscription run's cost, estimated at the provider's API list price.

The real cost of a plan's quota stays unknown (ADR-0094): the estimate sits
beside ``cost_usd`` and never in it, so nothing that ranks or sums costs reads
a figure nobody was billed. The token figures are chess3d-v1-K-015's, a codex
run on gpt-6.2-sol.
"""

from __future__ import annotations

import pytest

from harness_lab_app.api_price_estimate import (
    OPENAI_PRICES,
    estimate_api_cost,
    with_estimate,
)

K015 = {
    "available": True,
    "input_tokens": 274_469,  # non-cached input
    "cache_read_tokens": 5_413_632,
    "output_tokens": 57_932,  # reasoning included
    "cost_usd": None,
    "cost_source": "subscription_quota",
    "billing_mode": "subscription",
}


def test_a_codex_run_is_priced_with_its_cache_at_the_cached_rate() -> None:
    # 274,469 * $2 + 5,413,632 * $0.10 + 57,932 * $10, per 1M tokens.
    expected = (274_469 * 2.00 + 5_413_632 * 0.10 + 57_932 * 10.00) / 1_000_000

    assert estimate_api_cost("gpt-6.2-sol", 274_469, 5_413_632, 57_932) == (
        pytest.approx(expected)
    )
    assert expected == pytest.approx(1.6696, abs=1e-4)


def test_the_estimate_sits_beside_the_unknown_cost_never_in_it() -> None:
    cost = with_estimate(K015, "gpt-6.2-sol")

    assert cost["cost_usd"] is None
    assert cost["estimated_cost_usd"] == pytest.approx(1.6696, abs=1e-4)
    assert cost["estimate_basis"]["kind"] == "api_list_price"
    assert cost["estimate_basis"]["as_of"]
    assert "estimated_cost_usd" not in K015  # the stored record is untouched


@pytest.mark.parametrize(
    "cost",
    [
        {**K015, "cost_usd": 5.30, "cost_source": None},  # a real bill
        {**K015, "cost_usd": 0.0, "cost_source": "self_hosted"},  # measured free
        {**K015, "cost_source": None},  # unknown for another reason
    ],
)
def test_only_a_subscription_quota_gets_an_estimate(cost: dict) -> None:
    assert "estimated_cost_usd" not in with_estimate(cost, "gpt-6.2-sol")


def test_a_model_without_a_published_price_gets_no_estimate() -> None:
    # Never a guess: gemini, or a codex model the table does not list.
    assert estimate_api_cost("gemini-3-pro", 1, 1, 1) is None
    assert "estimated_cost_usd" not in with_estimate(K015, "codex-auto-review")
    assert "estimated_cost_usd" not in with_estimate(K015, None)


def test_every_priced_model_has_cached_input_cheaper_than_input() -> None:
    for model, (per_input, per_cached, per_output) in OPENAI_PRICES.items():
        assert 0 < per_cached < per_input < per_output, model
