"""What a subscription run would have cost at the provider's API list price.

A codex run spends a ChatGPT plan's quota: there is no per-token bill, so its
``cost_usd`` stays ``None`` (ADR-0094, "a subscription is not $0"). To compare
it in dollars with a gateway run anyway, this module prices the run's own token
counts at the provider's published API rates and reports the figure *beside*
``cost_usd`` as ``estimated_cost_usd``. It never replaces ``cost_usd``, so
rankings, statistics and cost deltas keep treating the real cost as unknown.

Prices are a dated snapshot of the provider's page, not a live lookup: the
gateway's ``/model/info`` is not readable with the lab's key and the codex
catalog carries no prices. Update :data:`PRICES_AS_OF` with the table.
"""

from __future__ import annotations

from typing import Any

#: Where and when the table below was read.
PRICE_SOURCE = "https://developers.openai.com/api/docs/pricing"
PRICES_AS_OF = "2026-11-20"

#: Above this many input tokens in one request the provider bills its
#: long-context rates. A run's usage is a sum over many requests, so the
#: per-request size is unknown: the short-context rate is used, and the
#: estimate is a floor for runs whose requests crossed the threshold.
LONG_CONTEXT_TOKENS = 272_000

#: Standard tier, short context, USD per 1M tokens:
#: (input, cached input, output). Reasoning tokens are part of codex's
#: ``output_tokens`` already, so they are priced as output once.
OPENAI_PRICES: dict[str, tuple[float, float, float]] = {
    "gpt-6.2-sol": (2.00, 0.10, 10.00),
    "gpt-6.2-astra": (8.00, 0.40, 40.00),
    "gpt-6.1-sol": (2.00, 0.10, 10.00),
    "gpt-6.1-terra": (1.00, 0.05, 5.00),
    "gpt-6-sol": (2.00, 0.20, 10.00),
    "gpt-6-astra": (10.00, 1.00, 50.00),
    "gpt-6-luna": (0.10, 0.01, 0.50),
    # Promotional until 2026-11-30 per the provider's page.
    "gpt-5.6-sol": (4.00, 0.40, 20.00),
    "gpt-5.6-terra": (2.00, 0.20, 12.00),
    "gpt-5.6-luna": (0.20, 0.02, 1.20),
    "gpt-5.5": (5.00, 0.50, 30.00),
}


def estimate_api_cost(
    model: str | None,
    input_tokens: int | None,
    cache_read_tokens: int | None,
    output_tokens: int | None,
) -> float | None:
    """USD at list price, or ``None`` when the model or the tokens are unknown.

    ``input_tokens`` must exclude the cached ones (the lab's convention:
    ``harness_events`` moves codex's cached input to ``cache_read_tokens``).
    """
    prices = OPENAI_PRICES.get((model or "").strip().lower())
    if prices is None or (input_tokens is None and output_tokens is None):
        return None
    per_input, per_cached, per_output = prices
    dollars = (
        (input_tokens or 0) * per_input
        + (cache_read_tokens or 0) * per_cached
        + (output_tokens or 0) * per_output
    ) / 1_000_000
    return round(dollars, 6)


def with_estimate(cost: dict[str, Any] | None, model: str | None) -> dict[str, Any]:
    """``cost`` plus an API-price estimate when it is a subscription's quota.

    Added only where the real cost is unknown because a plan paid for it
    (``cost_source == "subscription_quota"``); a measured or self-hosted cost
    is returned untouched. The estimate carries its basis so the UI can say
    what it is: list price, short-context rate, and the date of the table.
    """
    if not cost or cost.get("cost_usd") is not None:
        return dict(cost or {})
    if cost.get("cost_source") != "subscription_quota":
        return dict(cost)
    estimate = estimate_api_cost(
        model,
        cost.get("input_tokens"),
        cost.get("cache_read_tokens"),
        cost.get("output_tokens"),
    )
    if estimate is None:
        return dict(cost)
    return {
        **cost,
        "estimated_cost_usd": estimate,
        "estimate_basis": {
            "kind": "api_list_price",
            "model": (model or "").strip().lower(),
            "source": PRICE_SOURCE,
            "as_of": PRICES_AS_OF,
            "context": "short",
        },
    }
