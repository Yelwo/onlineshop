import asyncio
import json
import os
import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException

from orders import Cart, CustomerType, Order, Product, _CART_TTL, _now

# Consume pricing remotely over the graft (gateway reachable as `pricing` on the
# compose network). pricing returns JSON strings (primitives) which we parse here.
# The generated `graft_pypi_pricing` package only ships inside the Docker image;
# on the host it isn't installed, so the import is allowed to fail and `PricingService`
# is left as `None`. Tests inject a real in-process `PricingService` via fixture.
try:
    from graft_pypi_pricing.graft.pypi.pricing import GraftConfig
    from graft_pypi_pricing.pricingservice import PricingService

    GraftConfig.host = "ws://pricing/ws"
except ImportError:
    PricingService = None  # type: ignore[assignment]


_CART_SWEEP_INTERVAL = float(os.environ.get("CART_SWEEP_INTERVAL_SECONDS", 60))

# In-memory stores (sample project, no DB)
_CARTS: dict[str, Cart] = {}
_ORDERS: dict[str, Order] = {}

# Lazy product-metadata cache used to enrich cart lines with name + unit_price at
# add-time. Populated on first miss via `pricing.list_products()`. Tests reset it.
_CATALOG_CACHE: dict[str, dict] = {}


def _catalog() -> dict[str, dict]:
    if not _CATALOG_CACHE:
        for p in json.loads(PricingService.list_products()):
            _CATALOG_CACHE[p["id"]] = p
    return _CATALOG_CACHE


# ---- helpers ------------------------------------------------------------------

def _price_cart(cart: Cart) -> dict:
    """Call pricing's calculate_cart over the graft and unwrap the success envelope.
    Used for both `cart.quote` (just `total_price_cents`) and checkout (full envelope —
    items / subtotal / per-line discount needed to build the Order)."""
    priced = json.loads(PricingService.calculate_cart(cart.model_dump_json(include={"customer_type", "items"})))
    if priced.get("status") != "ok":
        raise HTTPException(status_code=500, detail=f"pricing failed: {priced.get('error')}")
    return priced


def _release_cart_items(cart: Cart) -> None:
    """Best-effort return of every line's reserved quantity to the catalog."""
    for item in cart.items:
        PricingService.release(item.product_id, item.quantity)


def _get_active_cart(cart_id: str) -> Cart:
    """Fetch a non-expired cart or raise 404. Sweeper-safe: lazy-expires if needed."""
    cart = _CARTS.get(cart_id)
    if cart is None:
        raise HTTPException(status_code=404, detail="cart not found")
    if cart.expires_at < _now():
        # the sweeper hasn't fired yet — release inline and drop
        _release_cart_items(_CARTS.pop(cart_id))
        raise HTTPException(status_code=404, detail="cart expired")
    return cart


def _reserve_and_merge(cart: Cart, items: list[Product]) -> None:
    """Reserve each item via pricing and aggregate it into the cart, enriching new
    lines with `name` + `unit_price_cents` from the catalog. Item-shape validation
    (non-empty product_id, positive quantity) lives in `Product`; here we only handle
    business failures (unknown product / insufficient stock from `reserve`). Raises 400
    on the first failure (earlier successful reserves stay decremented — accepted limitation)."""
    catalog = _catalog()
    for item in items:
        reservation = json.loads(PricingService.reserve(item.product_id, item.quantity))
        if reservation.get("status") != "ok":
            raise HTTPException(status_code=400, detail=f"{item.product_id}: {reservation.get('error', 'reservation failed')}")

        for line in cart.items:
            if line.product_id == item.product_id:
                line.quantity += item.quantity
                break
        else:
            info = catalog.get(item.product_id, {})
            cart.items.append(Product(
                product_id=item.product_id,
                name=info.get("name", item.product_id),
                unit_price_cents=info.get("price_cents", 0),
                quantity=item.quantity,
            ))


async def _expire_carts() -> None:
    """Background sweeper: drop every cart past its `expires_at`, releasing its stock."""
    while True:
        await asyncio.sleep(_CART_SWEEP_INTERVAL)
        now = _now()
        for cart_id in [cid for cid, c in _CARTS.items() if c.expires_at < now]:
            _release_cart_items(_CARTS.pop(cart_id))


@asynccontextmanager
async def lifespan(_app: FastAPI):
    task = asyncio.create_task(_expire_carts())
    yield
    task.cancel()


app = FastAPI(title="Orders Service", lifespan=lifespan)


# ---- catalog -----------------------------------------------------------------

@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


@app.get("/products")
def products() -> list[dict]:
    return json.loads(PricingService.list_products())


# ---- carts (reserve-on-add, fixed TTL from creation) -------------------------

@app.post("/carts", status_code=201, response_model_exclude_none=True)
def create_cart(customer_type: CustomerType = "regular") -> Cart:
    cart = Cart(customer_type=customer_type)
    _CARTS[cart.cart_id] = cart
    return cart


@app.get("/carts/{cart_id}", response_model_exclude_none=True)
def get_cart(cart_id: str) -> Cart:
    cart = _get_active_cart(cart_id)
    cart.quote = _price_cart(cart)["total_price_cents"] if cart.items else None
    return cart


@app.post("/carts/{cart_id}/items", response_model_exclude_none=True)
def add_to_cart(cart_id: str, items: list[Product]) -> Cart:
    """Add one or more items to an existing cart. Body is a JSON array of Product
    (clients only need `product_id` + `quantity`; `name`/`unit_price_cents` are
    enriched from the catalog). The cart's `customer_type` and `expires_at` are
    untouched."""
    cart = _get_active_cart(cart_id)
    if not items:
        raise HTTPException(status_code=400, detail="no items to add")
    _reserve_and_merge(cart, items)
    cart.quote = _price_cart(cart)["total_price_cents"]
    return cart


@app.delete("/carts/{cart_id}", status_code=204)
def abandon_cart(cart_id: str) -> None:
    """Manually drop a cart and return its items to stock."""
    cart = _CARTS.pop(cart_id, None)
    if cart is None:
        raise HTTPException(status_code=404, detail="cart not found")
    _release_cart_items(cart)


# ---- checkout ----------------------------------------------------------------

@app.post("/orders/{cart_id}", status_code=201, response_model_exclude_none=True)
def checkout(cart_id: str) -> Order:
    """Finalize a cart into an order. Items are already reserved — only price and record."""
    cart = _get_active_cart(cart_id)
    if not cart.items:
        raise HTTPException(status_code=400, detail="cart is empty")

    priced = _price_cart(cart)
    discounts = {line["discount_percent"] for line in priced["items"]}
    order = Order(
        order_id=str(uuid.uuid4()),
        customer_type=cart.customer_type,
        items=cart.items,  # already-enriched Products carry over from the cart
        subtotal_cents=priced["subtotal_cents"],
        total_price_cents=priced["total_price_cents"],
        discount=next(iter(discounts)) if len(discounts) == 1 else None,
        created_at=_now(),
    )
    _ORDERS[order.order_id] = order
    _CARTS.pop(cart_id, None)
    return order


@app.get("/orders/{order_id}", response_model_exclude_none=True)
def get_order(order_id: str) -> Order:
    order = _ORDERS.get(order_id)
    if order is None:
        raise HTTPException(status_code=404, detail="order not found")
    return order
