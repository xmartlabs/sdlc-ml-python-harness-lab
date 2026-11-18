# PR #214 — Cart orders, promotion codes and cancellation

**Author:** @jlopez · **Base:** `main` · **Diff:** `pr.diff` (already applied to this tree)

## What this does

Until now an order could hold a single product. This PR lets the storefront place one
order for a whole cart and adds the pieces the business asked for along with it.

- **Cart orders.** `OrderService.place_cart_order(customer, items, promo_code=None)`
  places one order for a list of `(sku, qty)` pairs. Repeated SKUs are merged into one
  line. The order is **all or nothing**: if any item is out of stock, the order is
  refused and no stock is taken. `place_order` now delegates to it.
- **Promotion codes** (`shop/promotions.py`), case-insensitive:
  - `WELCOME10` — 10% off any order;
  - `BIG50` — 15% off orders of **50.00 or more**;
  - `FIVEOFF` — 5.00 off, never more than the order itself.
  `calculate_total` now returns `(total, discount)` so the order can record the discount.
- **Cancellation.** `OrderService.cancel_order(order_id)` marks a placed order as
  cancelled and returns its stock. Cancelling twice is a no-op that returns `False`.
  Support agents cancel from several machines at once, and the service handles
  requests on several threads.
- **Admin search.** `OrderStore.search(customer_prefix)` backs the new admin screen's
  "customer starts with" box.

## Testing

New tests in `tests/test_cart.py`; the whole suite is green:

```
python -m pytest -q
10 passed
```
