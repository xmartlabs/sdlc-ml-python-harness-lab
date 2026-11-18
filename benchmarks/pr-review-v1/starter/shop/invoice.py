"""Plain-text invoices, printed by the warehouse when an order ships."""

from __future__ import annotations

from shop.catalog import get_product
from shop.pricing import calculate_total


def render_invoice(order_id: int, customer: str, items: list[tuple[str, int]]) -> str:
    lines = [f"Invoice #{order_id} for {customer}"]
    for sku, qty in items:
        product = get_product(sku)
        lines.append(f"{qty} x {product.name} @ {product.price_cents / 100:.2f}")
    total = calculate_total(items)
    lines.append(f"Total: {total / 100:.2f}")
    return "\n".join(lines)
