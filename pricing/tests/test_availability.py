import pytest

from pricing import PricingService


def test_available_when_stock_is_enough(make_product):
    make_product(id="laptop", stock=5)
    assert PricingService.is_available("laptop", 5) is True
    assert PricingService.is_available("laptop", 3) is True


def test_unavailable_when_stock_is_too_low(make_product):
    make_product(id="laptop", stock=2)
    assert PricingService.is_available("laptop", 5) is False


def test_invalid_quantity_raises(make_product):
    make_product(id="laptop", stock=5)
    with pytest.raises(Exception, match="Quantity must be"):
        PricingService.is_available("laptop", 0)


def test_unknown_product_raises(catalog):
    with pytest.raises(Exception, match="Unknown product"):
        PricingService.is_available("ghost", 1)
