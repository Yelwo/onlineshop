import json

import pytest

from pricing import PricingService


def _result(product_id, quantity, customer_type):
    return json.loads(PricingService.calculate_price(product_id, quantity, customer_type))


def test_regular_customer_no_discount(make_product):
    make_product(id="laptop", price_cents=500000)
    result = _result("laptop", 1, "regular")
    assert result["discount_percent"] == 0
    assert result["total_price_cents"] == 500000


def test_premium_customer_gets_10_percent(make_product):
    make_product(id="laptop", price_cents=500000)
    result = _result("laptop", 1, "premium")
    assert result["discount_percent"] == 10
    assert result["total_price_cents"] == 450000


def test_bulk_quantity_gets_extra_5_percent(make_product):
    make_product(id="mouse", price_cents=15000)
    result = _result("mouse", 10, "regular")
    assert result["discount_percent"] == 5
    assert result["total_price_cents"] == 142500


def test_premium_and_bulk_are_additive(make_product):
    make_product(id="laptop", price_cents=500000)
    result = _result("laptop", 10, "premium")
    assert result["discount_percent"] == 15
    assert result["total_price_cents"] == 4250000


def test_result_has_all_fields(make_product):
    make_product(id="laptop", price_cents=500000)
    result = _result("laptop", 2, "regular")
    assert result == {
        "product_id": "laptop",
        "unit_price_cents": 500000,
        "quantity": 2,
        "discount_percent": 0,
        "total_price_cents": 1000000,
    }


def test_invalid_quantity_raises(make_product):
    make_product(id="laptop", price_cents=500000)
    with pytest.raises(Exception, match="Quantity must be"):
        PricingService.calculate_price("laptop", 0, "regular")


def test_unknown_customer_type_raises(make_product):
    make_product(id="laptop", price_cents=500000)
    with pytest.raises(Exception, match="Unknown customer type"):
        PricingService.calculate_price("laptop", 1, "vip")


def test_unknown_product_raises(catalog):
    with pytest.raises(Exception, match="Unknown product"):
        PricingService.calculate_price("ghost", 1, "regular")
