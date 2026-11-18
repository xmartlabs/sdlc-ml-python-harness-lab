"""Promotion codes."""

from __future__ import annotations

from dataclasses import dataclass


class InvalidPromo(Exception):
    """Raised for a promotion code that does not exist or does not apply."""


@dataclass(frozen=True)
class Promo:
    code: str
    percent_off: int = 0
    amount_off_cents: int = 0
    min_total_cents: int = 0


PROMOS: dict[str, Promo] = {
    "WELCOME10": Promo("WELCOME10", percent_off=10),
    "BIG50": Promo("BIG50", percent_off=15, min_total_cents=5000),
    "FIVEOFF": Promo("FIVEOFF", amount_off_cents=500),
}


def find_promo(code: str) -> Promo:
    try:
        return PROMOS[code.strip().upper()]
    except KeyError:
        raise InvalidPromo(code) from None


def discount_for(promo: Promo, total_cents: int) -> int:
    """The discount ``promo`` gives on an order of ``total_cents``."""
    if promo.min_total_cents and not total_cents > promo.min_total_cents:
        raise InvalidPromo(promo.code)
    if promo.percent_off:
        return total_cents * promo.percent_off // 100
    return min(promo.amount_off_cents, total_cents)
