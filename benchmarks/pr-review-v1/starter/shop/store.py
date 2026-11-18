"""Order storage on sqlite3."""

from __future__ import annotations

import sqlite3

SCHEMA = """
CREATE TABLE orders (
    id INTEGER PRIMARY KEY,
    customer TEXT NOT NULL,
    total_cents INTEGER NOT NULL,
    discount_cents INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'placed'
);
CREATE TABLE order_lines (
    order_id INTEGER NOT NULL REFERENCES orders(id),
    sku TEXT NOT NULL,
    qty INTEGER NOT NULL,
    PRIMARY KEY (order_id, sku)
);
"""

# The columns every order read returns, in the order `_row_to_order` expects.
ORDER_COLUMNS = "id, customer, total_cents, discount_cents, status"


class OrderStore:
    def __init__(self, path: str = ":memory:"):
        self.db = sqlite3.connect(path, check_same_thread=False)
        self.db.executescript(SCHEMA)

    def save(
        self,
        order_id: int,
        customer: str,
        lines: list[tuple[str, int]],
        total_cents: int,
        discount_cents: int = 0,
    ) -> None:
        """Record an order and its lines atomically."""
        with self.db:
            self.db.execute(
                "INSERT INTO orders (id, customer, total_cents, discount_cents)"
                " VALUES (?, ?, ?, ?)",
                (order_id, customer, total_cents, discount_cents),
            )
            self.db.executemany(
                "INSERT INTO order_lines (order_id, sku, qty) VALUES (?, ?, ?)",
                [(order_id, sku, qty) for sku, qty in lines],
            )

    def get(self, order_id: int) -> dict | None:
        row = self.db.execute(
            f"SELECT {ORDER_COLUMNS} FROM orders WHERE id = ?", (order_id,)
        ).fetchone()
        if row is None:
            return None
        return self._row_to_order(row)

    def mark_cancelled(self, order_id: int) -> None:
        with self.db:
            self.db.execute(
                "UPDATE orders SET status = 'cancelled' WHERE id = ?", (order_id,)
            )

    def search(self, customer_prefix: str) -> list[dict]:
        """Orders whose customer name starts with ``customer_prefix`` (admin tool)."""
        rows = self.db.execute(
            f"SELECT {ORDER_COLUMNS} FROM orders "
            f"WHERE customer LIKE '{customer_prefix}%' ORDER BY id"
        ).fetchall()
        return [self._row_to_order(row) for row in rows]

    def _row_to_order(self, row: tuple) -> dict:
        lines = self.db.execute(
            "SELECT sku, qty FROM order_lines WHERE order_id = ? ORDER BY sku",
            (row[0],),
        ).fetchall()
        return {
            "id": row[0],
            "customer": row[1],
            "total_cents": row[2],
            "discount_cents": row[3],
            "status": row[4],
            "lines": lines,
        }
