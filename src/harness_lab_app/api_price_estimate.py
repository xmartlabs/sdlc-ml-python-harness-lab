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


# ---------------------------------------------------------------------------
# Gemini API (antigravity_cli): a per-token bill the CLI does not report
# ---------------------------------------------------------------------------

#: Where and when the Gemini table below was read. ai.google.dev/gemini-api/
#: docs/pricing redirects there; "Standard" tab of the paid tier.
GEMINI_PRICE_SOURCE = "https://aistudio.google.com/docs/pricing"
GEMINI_PRICES_AS_OF = "2026-11-22"

#: Prompts above this many tokens bill at gemini-3.1-pro-preview's long-context
#: rate; the other models list no such tier. The short rate is used (a floor).
GEMINI_LONG_CONTEXT_TOKENS = 200_000

#: Paid tier, Standard, USD per 1M tokens: (input, cached input, output),
#: each with the dates it applies on, as the page states them (inclusive,
#: ISO dates; ``None`` = open). Output includes thinking tokens on every row
#: of the page ("Output price (including thinking tokens)"), as agy's
#: ``output_tokens`` does. Keyed by the **Gemini API model** an agy slug calls
#: (native_models.AGY_SLUG_MAP), not by the slug. A run is priced at the row
#: in effect on its date; a date past every row's known validity has no price
#: (``None`` → ``api_price_unknown``), never a stale one. gemini-3.7-flash is
#: not on the page, so it has no row and no estimate.
_PriceRow = tuple[str | None, str | None, tuple[float, float, float]]
GEMINI_PRICE_TABLE: dict[str, tuple[_PriceRow, ...]] = {
    "gemini-4-neon": ((None, None, (3.00, 0.15, 15.00)),),
    "gemini-4-carbon": (
        (None, "2026-11-30", (2.00, 0.10, 10.00)),
        ("2026-12-01", None, (4.00, 0.20, 20.00)),
    ),
    "gemini-4-argon": (
        (None, "2026-11-30", (2.00, 0.10, 10.00)),
        ("2026-12-01", None, (4.00, 0.20, 20.00)),
    ),
    "gemini-4-hydrogen": ((None, None, (1.50, 0.075, 7.50)),),
    "gemini-3.8-flash": (
        (None, "2026-12-31", (0.75, 0.075, 3.75)),
        ("2027-01-01", None, (1.50, 0.15, 7.50)),
    ),
    "gemini-3.6-flash": (
        (None, "2026-12-31", (0.75, 0.075, 3.75)),
        ("2027-01-01", None, (1.50, 0.15, 7.50)),
    ),
    # Prompts <= 200k tokens; $4.00 / $0.40 / $18.00 above.
    "gemini-3.1-pro-preview": ((None, None, (2.00, 0.20, 12.00)),),
}

#: A table read once goes stale: past this date every row is "unknown" until
#: the table is read again, so a price change Google announces later is never
#: silently ignored. Six months after the read.
GEMINI_PRICES_VALID_UNTIL = "2027-05-22"


def gemini_price(
    api_model: str | None, on: str | None = None
) -> tuple[float, float, float] | None:
    """The (input, cached, output) price of ``api_model`` on date ``on``.

    ``on`` is an ISO date (``YYYY-MM-DD``), today when omitted. ``None`` for an
    unlisted model or a date past :data:`GEMINI_PRICES_VALID_UNTIL`.
    """
    import datetime as _dt

    day = (on or _dt.date.today().isoformat())[:10]
    if day > GEMINI_PRICES_VALID_UNTIL:
        return None
    for start, stop, prices in GEMINI_PRICE_TABLE.get(
        (api_model or "").strip().lower(), ()
    ):
        if (start is None or day >= start) and (stop is None or day <= stop):
            return prices
    return None


#: The table as of :data:`GEMINI_PRICES_AS_OF` (kept for callers and tests).
GEMINI_PRICES: dict[str, tuple[float, float, float]] = {
    model: prices
    for model in GEMINI_PRICE_TABLE
    if (prices := gemini_price(model, GEMINI_PRICES_AS_OF)) is not None
}


def estimate_gemini_api_cost(
    api_model: str | None,
    input_tokens: int | None,
    cache_read_tokens: int | None,
    output_tokens: int | None,
    on: str | None = None,
) -> float | None:
    """USD at the Gemini API list price on date ``on``, or ``None``.

    ``None`` for an unknown model, a date with no known price, or no tokens.
    ``input_tokens`` excludes the cached part and ``output_tokens`` includes
    thinking, which is how agy reports them (verified, agy 1.5.14).
    """
    prices = gemini_price(api_model, on)
    if prices is None or (input_tokens is None and output_tokens is None):
        return None
    per_input, per_cached, per_output = prices
    dollars = (
        (input_tokens or 0) * per_input
        + (cache_read_tokens or 0) * per_cached
        + (output_tokens or 0) * per_output
    ) / 1_000_000
    return round(dollars, 6)


def gemini_cost_basis(api_model: str | None, on: str | None = None) -> dict[str, Any]:
    """What a Gemini estimate rests on, for the run record and the UI."""
    import datetime as _dt

    return {
        "kind": "api_list_price",
        "model": (api_model or "").strip().lower() or None,
        "source": GEMINI_PRICE_SOURCE,
        "as_of": GEMINI_PRICES_AS_OF,
        "priced_on": (on or _dt.date.today().isoformat())[:10],
        "context": "short",
        "tier": "paid/standard",
    }


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
