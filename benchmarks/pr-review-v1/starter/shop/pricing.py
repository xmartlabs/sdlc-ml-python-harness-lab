"""Order pricing. Every amount is an integer number of cents."""

from __future__ import annotations

from shop.catalog import get_product
from shop.promotions import discount_for, find_promo


def line_total(sku: str, qty: int) -> int:
    return get_product(sku).price_cents * qty


def calculate_total(
    items: list[tuple[str, int]], promo_code: str | None = None
) -> tuple[int, int]:
    """``(total, discount)`` in cents for ``items``, a list of ``(sku, qty)`` pairs.

    The total is after the discount; the discount is reported separately so the
    order can record it.
    """
    subtotal = sum(line_total(sku, qty) for sku, qty in items)
    discount = 0
    if promo_code:
        discount = discount_for(find_promo(promo_code), subtotal)
    return subtotal - discount, discount
