"""Test setup for the orders service.

The graft client (`graft_pypi_pricing`) only exists inside the Docker image. On the
host, `orders/app.py` lets that import fail and leaves `PricingService = None`.
The `pricing_service` fixture here injects the **real in-process `PricingService`**
from `pricing/` — effectively LOCAL mode: real pricing logic, no gateway, no stubs.
"""

import json

import pytest
from fastapi.testclient import TestClient

import pricing
import app as orders_app


@pytest.fixture(autouse=True)
def _reset_state(monkeypatch):
    """Fresh catalog, rules, and empty cart/order stores before every test."""
    catalog = {
        "laptop": pricing.Product("laptop", "Laptop", 500000, 5),
        "mouse": pricing.Product("mouse", "Mouse", 15000, 100),
        "keyboard": pricing.Product("keyboard", "Keyboard", 30000, 50),
    }
    monkeypatch.setattr(pricing.PricingService, "_CATALOG", catalog)
    monkeypatch.setattr(pricing.PricingService, "_RULES", [])
    for src in pricing._DEFAULT_RULES_SRC:
        pricing.PricingService.add_rule(json.dumps(src))
    monkeypatch.setattr(orders_app, "_CARTS", {})
    monkeypatch.setattr(orders_app, "_ORDERS", {})
    monkeypatch.setattr(orders_app, "_CATALOG_CACHE", {})


@pytest.fixture
def pricing_service(monkeypatch):
    """Swap orders' `PricingService` (None on host) for the real in-process class."""
    monkeypatch.setattr(orders_app, "PricingService", pricing.PricingService)
    return pricing.PricingService


@pytest.fixture
def client(pricing_service):
    """TestClient with pricing injected. Lifespan is NOT triggered — the background
    sweeper never runs; expiry tests time-travel via `app_module._now`."""
    return TestClient(orders_app.app)


@pytest.fixture
def stock():
    """Read current catalog stock for a product."""

    def _get(product_id: str) -> int:
        return pricing.PricingService._CATALOG[product_id].stock

    return _get


@pytest.fixture
def app_module():
    """The imported orders.app module — useful for monkeypatching `_now`."""
    return orders_app
