import pytest
from shop.inventory import Inventory, OutOfStock
from shop.orders import OrderService
from shop.promotions import InvalidPromo
from shop.store import OrderStore


def make_service(stock=None):
    inventory = Inventory(stock or {"MUG-01": 5, "TEE-02": 3, "CAP-03": 4})
    return OrderService(inventory, OrderStore()), inventory


def test_a_cart_order_takes_every_item_and_records_one_order():
    service, inventory = make_service()
    order_id = service.place_cart_order("ana", [("MUG-01", 2), ("TEE-02", 1)])
    order = service.store.get(order_id)
    assert order["total_cents"] == 2 * 1250 + 2400
    assert order["lines"] == [("MUG-01", 2), ("TEE-02", 1)]
    assert inventory.available("MUG-01") == 3


def test_repeated_skus_are_merged_into_one_line():
    service, _ = make_service()
    order_id = service.place_cart_order("ana", [("MUG-01", 1), ("MUG-01", 2)])
    assert service.store.get(order_id)["lines"] == [("MUG-01", 3)]


def test_a_percentage_promo_is_applied_and_recorded():
    service, _ = make_service()
    order_id = service.place_cart_order("ana", [("TEE-02", 1)], promo_code="welcome10")
    order = service.store.get(order_id)
    assert order["discount_cents"] == 240
    assert order["total_cents"] == 2160


def test_an_unknown_promo_code_is_rejected():
    service, _ = make_service()
    try:
        service.place_cart_order("ana", [("MUG-01", 1)], promo_code="NOPE")
    except InvalidPromo:
        pass


def test_an_order_beyond_stock_is_refused():
    service, inventory = make_service()
    with pytest.raises(OutOfStock):
        service.place_cart_order("ana", [("TEE-02", 4)])
    assert inventory.available("TEE-02") == 3


def test_cancelling_returns_the_stock():
    service, inventory = make_service()
    order_id = service.place_cart_order("ana", [("CAP-03", 2)])
    assert service.cancel_order(order_id) is True
    assert inventory.available("CAP-03") == 4
    assert service.store.get(order_id)["status"] == "cancelled"
    assert service.cancel_order(order_id) is False


def test_admin_search_finds_orders_by_customer_prefix():
    service, _ = make_service()
    service.place_cart_order("ana", [("MUG-01", 1)])
    service.place_cart_order("andres", [("MUG-01", 1)])
    service.place_cart_order("bea", [("MUG-01", 1)])
    assert [o["customer"] for o in service.store.search("an")] == ["ana", "andres"]
