"""Placing and cancelling orders."""

from __future__ import annotations

import itertools

from shop.inventory import Inventory
from shop.pricing import calculate_total
from shop.store import OrderStore


class OrderService:
    def __init__(self, inventory: Inventory, store: OrderStore):
        self.inventory = inventory
        self.store = store
        self._ids = itertools.count(1)

    def place_order(self, customer: str, sku: str, qty: int) -> int:
        """Take ``qty`` units of ``sku`` from stock and record the order."""
        return self.place_cart_order(customer, [(sku, qty)])

    def place_cart_order(
        self,
        customer: str,
        items: list[tuple[str, int]],
        promo_code: str | None = None,
    ) -> int:
        """Take every item from stock and record one order for the whole cart.

        All or nothing: when one item is out of stock, no stock is taken.
        """
        if not items:
            raise ValueError("an order needs at least one item")
        merged: dict[str, int] = {}
        for sku, qty in items:
            if qty <= 0:
                raise ValueError("qty must be positive")
            merged[sku] = merged.get(sku, 0) + qty
        lines = sorted(merged.items())
        total, discount = calculate_total(lines, promo_code)
        with self.inventory.lock:
            for sku, qty in lines:
                self.inventory.take(sku, qty)
        order_id = next(self._ids)
        self.store.save(order_id, customer, lines, total, discount)
        return order_id

    def cancel_order(self, order_id: int) -> bool:
        """Cancel a placed order and return its stock.

        Returns False for an unknown order or one that is already cancelled.
        """
        order = self.store.get(order_id)
        if order is None or order["status"] == "cancelled":
            return False
        for sku, qty in order["lines"]:
            self.inventory.put_back(sku, qty)
        self.store.mark_cancelled(order_id)
        return True
