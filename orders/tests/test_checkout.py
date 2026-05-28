def _open_with(client, customer_type, items):
    res = client.post("/carts", params={"customer_type": customer_type})
    assert res.status_code == 201
    cart_id = res.json()["cart_id"]
    if items:
        res = client.post(f"/carts/{cart_id}/items", json=items)
        assert res.status_code == 200
    return cart_id


# ---- POST /orders/{cart_id} --------------------------------------------------

def test_checkout_creates_order_and_drops_cart(client):
    cart_id = _open_with(client, "regular", [{"product_id": "mouse", "quantity": 2}])
    res = client.post(f"/orders/{cart_id}")
    assert res.status_code == 201

    order = res.json()
    assert order["customer_type"] == "regular"
    assert order["items"][0]["product_id"] == "mouse"
    assert order["subtotal_cents"] == 30000
    assert order["total_price_cents"] == 30000
    assert order["status"] == "confirmed"
    assert "order_id" in order and "created_at" in order

    # cart is gone after checkout
    assert client.get(f"/carts/{cart_id}").status_code == 404


def test_checkout_does_not_reserve_again(client, stock):
    """Items were already reserved at add-to-cart time — checkout doesn't touch stock."""
    cart_id = _open_with(client, "regular", [{"product_id": "mouse", "quantity": 3}])
    assert stock("mouse") == 97
    client.post(f"/orders/{cart_id}")
    assert stock("mouse") == 97  # no double-decrement


def test_checkout_unknown_cart_404(client):
    assert client.post("/orders/nope").status_code == 404


def test_checkout_empty_cart_400(client):
    cart_id = _open_with(client, "regular", [])
    res = client.post(f"/orders/{cart_id}")
    assert res.status_code == 400
    assert "empty" in res.json()["detail"]


def test_premium_customer_gets_10_percent(client):
    cart_id = _open_with(client, "premium", [{"product_id": "mouse", "quantity": 1}])
    order = client.post(f"/orders/{cart_id}").json()
    assert order["discount"] == 10
    assert order["subtotal_cents"] == 15000
    assert order["total_price_cents"] == 13500


def test_bulk_threshold_triggers_discount(client):
    """cart.total_quantity >= 10 fires the default bulk rule on EVERY line."""
    cart_id = _open_with(
        client,
        "regular",
        [{"product_id": "mouse", "quantity": 6}, {"product_id": "keyboard", "quantity": 6}],
    )
    order = client.post(f"/orders/{cart_id}").json()
    # bulk 5% on every line; subtotal 6*15000 + 6*30000 = 270000;
    # mouse line 90000 -> 85500; keyboard line 180000 -> 171000; total 256500
    assert order["discount"] == 5
    assert order["subtotal_cents"] == 270000
    assert order["total_price_cents"] == 256500


def test_premium_and_bulk_compose(client):
    cart_id = _open_with(
        client,
        "premium",
        [{"product_id": "mouse", "quantity": 5}, {"product_id": "keyboard", "quantity": 5}],
    )
    order = client.post(f"/orders/{cart_id}").json()
    # premium 10 + bulk(total=10) 5 = 15% off each line:
    # mouse 75000 -> 63750; keyboard 150000 -> 127500; total 191250
    assert order["discount"] == 15
    assert order["subtotal_cents"] == 225000
    assert order["total_price_cents"] == 191250


def test_no_discount_uniform_zero(client):
    cart_id = _open_with(client, "regular", [{"product_id": "mouse", "quantity": 1}])
    order = client.post(f"/orders/{cart_id}").json()
    # all lines 0% -> uniform 0 (not None)
    assert order["discount"] == 0
    assert order["subtotal_cents"] == order["total_price_cents"] == 15000


# ---- GET /orders/{order_id} --------------------------------------------------

def test_get_order_returns_stored_order(client):
    cart_id = _open_with(client, "regular", [{"product_id": "mouse", "quantity": 1}])
    order_id = client.post(f"/orders/{cart_id}").json()["order_id"]

    res = client.get(f"/orders/{order_id}")
    assert res.status_code == 200
    assert res.json()["order_id"] == order_id


def test_get_unknown_order_404(client):
    assert client.get("/orders/does-not-exist").status_code == 404
