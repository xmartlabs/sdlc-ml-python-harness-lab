"""Product catalog. Every price is an integer number of cents."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Product:
    sku: str
    name: str
    price_cents: int


CATALOG: dict[str, Product] = {
    "MUG-01": Product("MUG-01", "Enamel mug", 1250),
    "TEE-02": Product("TEE-02", "Logo tee", 2400),
    "CAP-03": Product("CAP-03", "Trucker cap", 1800),
    "PEN-04": Product("PEN-04", "Gel pen", 199),
}


def get_product(sku: str) -> Product:
    try:
        return CATALOG[sku]
    except KeyError:
        raise KeyError(f"unknown sku: {sku}") from None
