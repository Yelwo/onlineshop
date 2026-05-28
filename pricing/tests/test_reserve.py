import json

from pricing import PricingService


def test_list_products_returns_catalog(make_product):
    make_product(id="laptop", name="Laptop", price_cents=500000, stock=5)
    make_product(id="mouse", name="Mouse", price_cents=15000, stock=100)

    by_id = {p["id"]: p for p in json.loads(PricingService.list_products())}

    assert by_id["laptop"] == {
        "id": "laptop",
        "name": "Laptop",
        "price_cents": 500000,
        "stock": 5,
    }
    assert by_id["mouse"]["stock"] == 100


def test_reserve_decrements_stock(make_product):
    make_product(id="laptop", stock=5)

    first = json.loads(PricingService.reserve("laptop", 3))
    assert first["status"] == "ok"
    assert first["remaining_stock"] == 2

    # the decrement persists for the next reservation
    second = json.loads(PricingService.reserve("laptop", 2))
    assert second["remaining_stock"] == 0


def test_reserve_insufficient_stock_leaves_stock_untouched(make_product):
    product = make_product(id="laptop", stock=2)

    res = json.loads(PricingService.reserve("laptop", 5))

    assert res["status"] == "error"
    assert product.stock == 2


def test_reserve_unknown_product(catalog):
    assert json.loads(PricingService.reserve("ghost", 1))["status"] == "error"


def test_reserve_invalid_quantity(make_product):
    make_product(id="laptop", stock=5)
    assert json.loads(PricingService.reserve("laptop", 0))["status"] == "error"


def test_release_increments_stock(make_product):
    product = make_product(id="laptop", stock=5)

    json.loads(PricingService.reserve("laptop", 3))
    assert product.stock == 2

    res = json.loads(PricingService.release("laptop", 3))
    assert res["status"] == "ok"
    assert res["stock"] == 5
    assert product.stock == 5


def test_release_unknown_product(catalog):
    assert json.loads(PricingService.release("ghost", 1))["status"] == "error"


def test_release_invalid_quantity(make_product):
    make_product(id="laptop", stock=5)
    assert json.loads(PricingService.release("laptop", 0))["status"] == "error"
