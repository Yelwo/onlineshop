import pytest

import pricing
from factories import ProductFactory


@pytest.fixture
def catalog(monkeypatch):
    """Replace the in-memory product catalog with an empty one for a single test."""
    store: dict[str, pricing.Product] = {}
    monkeypatch.setattr(pricing.PricingService, "_CATALOG", store)
    return store


@pytest.fixture
def make_product(catalog):
    """Add a factory-built Product to the in-memory catalog and return it."""

    def _make(**kwargs) -> pricing.Product:
        product = ProductFactory(**kwargs)
        catalog[product.id] = product
        return product

    return _make
