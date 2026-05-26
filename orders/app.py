import json

from fastapi import FastAPI

from orders import Order
from graft_pypi_pricing.graft.pypi.pricing import GraftConfig
from graft_pypi_pricing.pricingservice import PricingService

# Consume the pricing service remotely over the graft. On the compose network the
# gateway is reachable as the `pricing` host on its WebSocket port.
GraftConfig.host = "ws://pricing/ws"

app = FastAPI(title="Orders Service")


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


@app.get("/price/{product_id}")
def price(product_id: str, quantity: int = 1, customer_type: str = "regular") -> dict:
    # pricing returns a JSON string over the graft (a primitive); parse it here.
    raw = PricingService.calculate_price(product_id, quantity, customer_type)
    return json.loads(raw)


@app.post("/orders/total")
def order_total(order: Order) -> dict:
    return {"items": len(order.items), "total": order.total()}
