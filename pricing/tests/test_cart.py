import json

from pricing import PricingService


def _cart(customer_type, items):
    return json.loads(
        PricingService.calculate_cart(json.dumps({"customer_type": customer_type, "items": items}))
    )


def test_cart_totals_sum_line_items(make_product):
    make_product(id="laptop", price_cents=500000, stock=100)
    make_product(id="mouse", price_cents=15000, stock=100)

    res = _cart("regular", [{"product_id": "laptop", "quantity": 2}, {"product_id": "mouse", "quantity": 3}])

    assert res["status"] == "ok"
    assert res["subtotal_cents"] == 500000 * 2 + 15000 * 3
    # total quantity 5 < 10 -> no bulk discount
    assert all(line["discount_percent"] == 0 for line in res["items"])
    assert res["total_price_cents"] == res["subtotal_cents"]


def test_cart_total_quantity_triggers_bulk_across_products(make_product):
    make_product(id="laptop", price_cents=500000, stock=100)
    make_product(id="mouse", price_cents=15000, stock=100)

    # 6 + 6 = 12 >= 10 -> bulk 5% applies to every line, even though no single line >= 10
    res = _cart("regular", [{"product_id": "laptop", "quantity": 6}, {"product_id": "mouse", "quantity": 6}])

    assert res["status"] == "ok"
    assert all(line["discount_percent"] == 5 for line in res["items"])
    expected = (500000 * 6 * 95 + 50) // 100 + (15000 * 6 * 95 + 50) // 100
    assert res["total_price_cents"] == expected


def test_cart_premium_and_bulk_combine_per_line(make_product):
    make_product(id="laptop", price_cents=500000, stock=100)
    make_product(id="mouse", price_cents=15000, stock=100)

    res = _cart("premium", [{"product_id": "laptop", "quantity": 5}, {"product_id": "mouse", "quantity": 5}])

    # premium (10) + bulk (cart total 10 >= 10 -> 5) = 15 per line
    assert res["status"] == "ok"
    assert all(line["discount_percent"] == 15 for line in res["items"])


def test_cart_rejects_unknown_customer_type(make_product):
    make_product(id="laptop", price_cents=500000, stock=100)
    res = _cart("vip", [{"product_id": "laptop", "quantity": 1}])
    assert res["status"] == "error"
    assert "Unknown customer type" in res["error"]


def test_cart_rejects_malformed_json():
    res = json.loads(PricingService.calculate_cart("{not json"))
    assert res["status"] == "error"
    assert "invalid JSON" in res["error"]


def test_cart_line_shape(make_product):
    make_product(id="laptop", price_cents=500000, stock=100)
    res = _cart("regular", [{"product_id": "laptop", "quantity": 2}])
    assert res["status"] == "ok"
    assert res["items"][0] == {
        "product_id": "laptop",
        "unit_price_cents": 500000,
        "quantity": 2,
        "discount_percent": 0,
        "line_total_cents": 1000000,
    }
    assert res["customer_type"] == "regular"
    assert res["subtotal_cents"] == 1000000
    assert res["total_price_cents"] == 1000000
