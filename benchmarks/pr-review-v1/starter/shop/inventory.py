"""Stock levels, shared by every request thread of the service."""

from __future__ import annotations

import threading


class OutOfStock(Exception):
    """Raised when an order asks for more units than are in stock."""


class Inventory:
    def __init__(self, stock: dict[str, int]):
        self._stock = dict(stock)
        # One lock for the whole inventory. Every check-and-take must hold it:
        # the service handles requests on several threads at once.
        self.lock = threading.Lock()

    def available(self, sku: str) -> int:
        return self._stock.get(sku, 0)

    def take(self, sku: str, qty: int) -> None:
        """Remove ``qty`` units of ``sku``. The caller must hold ``self.lock``."""
        if self._stock.get(sku, 0) < qty:
            raise OutOfStock(sku)
        self._stock[sku] -= qty

    def put_back(self, sku: str, qty: int) -> None:
        """Return ``qty`` units of ``sku``. The caller must hold ``self.lock``."""
        self._stock[sku] = self._stock.get(sku, 0) + qty
