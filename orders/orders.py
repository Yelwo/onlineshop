"""Pydantic models + cart lifetime for the orders service."""

import os
import uuid
from datetime import datetime, timedelta, timezone
from typing import Literal

from pydantic import BaseModel, Field


CustomerType = Literal["regular", "premium"]

# A cart has a fixed lifetime set once at creation (`created_at + _CART_TTL`); adds
# do not extend it. When the deadline passes the background sweeper in `app.py`
# releases the cart's reserved items back to stock — the whole cart dies in one moment.
_CART_TTL = timedelta(seconds=int(os.environ.get("CART_TTL_SECONDS", 2 * 60 * 60)))


def _now() -> datetime:
    return datetime.now(timezone.utc)


class Product(BaseModel):
    """A line item in a cart: which product, what it cost per unit at add-time, how many.

    Clients only need to send `product_id` + `quantity` (`name` and `unit_price_cents`
    have defaults). The server enriches `name`/`unit_price_cents` from the pricing
    catalog when the line is first added; subsequent aggregations only bump `quantity`.
    Shape constraints (non-empty product_id, positive quantity) live here — FastAPI
    rejects malformed request bodies with 422 before they reach the handler."""

    product_id: str = Field(min_length=1)
    name: str = ""
    unit_price_cents: int = 0
    quantity: int = Field(gt=0)


class Cart(BaseModel):
    """Whole cart in one model: identity, contents, lifetime, and the live total.

    - `cart_id`, `created_at`, `expires_at` — auto-generated on construction so every
      Cart instance has an identity and a deadline from the moment it exists.
    - `customer_type` — fixed at creation, picked up by pricing rules (`cart.customer_type`).
    - `items` — list of `Product` lines in the cart. Server-side enriched with
      `name` + `unit_price_cents` at add-time from the catalog.
    - `quote` — server-populated total price (after rules) for the cart's current
      contents. Single int; subtotal and per-line discount are derivable from
      `items` + the order's eventual `discount` field. None for empty carts and
      excluded from JSON output via `response_model_exclude_none=True`.
    """

    cart_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    customer_type: CustomerType = "regular"
    items: list[Product] = []
    created_at: datetime = Field(default_factory=_now)
    expires_at: datetime = Field(default_factory=lambda: _now() + _CART_TTL)
    quote: int | None = None  # total_price_cents after rules; subtotal derivable from items


class Order(BaseModel):
    order_id: str
    customer_type: CustomerType
    items: list[Product]
    subtotal_cents: int
    total_price_cents: int
    discount: int | None = None  # uniform percent applied to every line; None if rules diverged
    status: str = "confirmed"
    created_at: datetime
