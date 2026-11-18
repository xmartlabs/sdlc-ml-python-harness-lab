import pytest
from shop.inventory import Inventory, OutOfStock
from shop.orders import OrderService
from shop.store import OrderStore


def make_service(stock=None):
    inventory = Inventory(stock or {"MUG-01": 5, "TEE-02": 3})
    return OrderService(inventory, OrderStore()), inventory


def test_an_order_takes_stock_and_is_recorded():
    service, inventory = make_service()
    order_id = service.place_order("ana", "MUG-01", 2)
    assert inventory.available("MUG-01") == 3
    assert service.store.get(order_id)["total_cents"] == 2500


def test_an_order_beyond_stock_is_refused():
    service, inventory = make_service()
    with pytest.raises(OutOfStock):
        service.place_order("ana", "TEE-02", 4)
    assert inventory.available("TEE-02") == 3


def test_a_non_positive_quantity_is_refused():
    service, _ = make_service()
    with pytest.raises(ValueError):
        service.place_order("ana", "MUG-01", 0)
