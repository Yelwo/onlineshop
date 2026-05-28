def _open_cart(client, customer_type="regular", items=None):
    """Open a cart and optionally fill it via a follow-up POST /items."""
    res = client.post("/carts", params={"customer_type": customer_type})
    assert res.status_code == 201
    cart = res.json()
    if items:
        res = client.post(f"/carts/{cart['cart_id']}/items", json=items)
        assert res.status_code == 200
        cart = res.json()
    return cart


# ---- POST /carts -------------------------------------------------------------

def test_create_empty_cart_returns_id_and_expiry(client, stock):
    cart = _open_cart(client)
    assert "cart_id" in cart
    assert cart["items"] == []
    assert cart["customer_type"] == "regular"
    assert "expires_at" in cart and "created_at" in cart
    assert "quote" not in cart  # no quote for empty cart
    # stock untouched
    assert stock("mouse") == 100


def test_create_cart_rejects_unknown_customer_type(client):
    # Pydantic `Literal["regular", "premium"]` on the query param -> FastAPI 422
    res = client.post("/carts", params={"customer_type": "vip"})
    assert res.status_code == 422


# ---- GET /carts/{cart_id} ----------------------------------------------------

def test_get_cart_returns_state_and_quote(client):
    cart_id = _open_cart(client, items=[{"product_id": "mouse", "quantity": 2}])["cart_id"]
    res = client.get(f"/carts/{cart_id}")
    assert res.status_code == 200
    body = res.json()
    assert body["cart_id"] == cart_id
    assert body["items"] == [
        {"product_id": "mouse", "name": "Mouse", "unit_price_cents": 15000, "quantity": 2},
    ]
    # quote is just the total; no discount (qty < 10, regular) -> equals subtotal
    assert body["quote"] == 30000


def test_get_cart_omits_quote_when_empty(client):
    cart_id = _open_cart(client)["cart_id"]
    res = client.get(f"/carts/{cart_id}")
    assert res.status_code == 200
    assert "quote" not in res.json()


def test_get_unknown_cart_404(client):
    assert client.get("/carts/does-not-exist").status_code == 404


# ---- POST /carts/{cart_id}/items ---------------------------------------------

def test_add_to_cart_reserves_appends_and_enriches(client, stock):
    cart_id = _open_cart(client)["cart_id"]
    res = client.post(
        f"/carts/{cart_id}/items",
        json=[{"product_id": "mouse", "quantity": 2}],
    )
    assert res.status_code == 200
    # the line was enriched with catalog name + unit price
    assert res.json()["items"] == [
        {"product_id": "mouse", "name": "Mouse", "unit_price_cents": 15000, "quantity": 2},
    ]
    assert stock("mouse") == 98


def test_add_to_cart_aggregates_duplicate_product(client, stock):
    cart_id = _open_cart(client)["cart_id"]
    client.post(f"/carts/{cart_id}/items", json=[{"product_id": "mouse", "quantity": 2}])
    res = client.post(f"/carts/{cart_id}/items", json=[{"product_id": "mouse", "quantity": 3}])
    assert res.status_code == 200
    # one line, summed quantity, both reservations applied
    assert res.json()["items"] == [
        {"product_id": "mouse", "name": "Mouse", "unit_price_cents": 15000, "quantity": 5},
    ]
    assert stock("mouse") == 95


def test_add_to_cart_batch(client, stock):
    cart_id = _open_cart(client)["cart_id"]
    res = client.post(
        f"/carts/{cart_id}/items",
        json=[
            {"product_id": "mouse", "quantity": 3},
            {"product_id": "keyboard", "quantity": 4},
        ],
    )
    assert res.status_code == 200
    assert {i["product_id"] for i in res.json()["items"]} == {"mouse", "keyboard"}
    assert stock("mouse") == 97
    assert stock("keyboard") == 46
    # batch 3+4 = 7 total, no bulk; subtotal = total
    # subtotal: 3*15000 + 4*30000 = 165000
    assert res.json()["quote"] == 165000


def test_add_batch_total_quantity_triggers_bulk(client):
    cart_id = _open_cart(client)["cart_id"]
    res = client.post(
        f"/carts/{cart_id}/items",
        json=[{"product_id": "mouse", "quantity": 4}, {"product_id": "keyboard", "quantity": 6}],
    )
    assert res.status_code == 200
    # total qty 10 -> bulk 5% on each line:
    # mouse 4*15000=60000 -> 57000; keyboard 6*30000=180000 -> 171000; total 228000
    assert res.json()["quote"] == 228000


def test_add_to_premium_cart_applies_premium_discount(client):
    cart_id = _open_cart(client, customer_type="premium")["cart_id"]
    res = client.post(f"/carts/{cart_id}/items", json=[{"product_id": "mouse", "quantity": 1}])
    assert res.status_code == 200
    assert res.json()["customer_type"] == "premium"
    # premium 10% off 15000 -> 13500
    assert res.json()["quote"] == 13500


def test_add_empty_items_list_400(client):
    cart_id = _open_cart(client)["cart_id"]
    res = client.post(f"/carts/{cart_id}/items", json=[])
    assert res.status_code == 400


def test_add_insufficient_stock_400(client, stock):
    cart_id = _open_cart(client)["cart_id"]
    res = client.post(
        f"/carts/{cart_id}/items",
        json=[{"product_id": "laptop", "quantity": 100}],
    )
    assert res.status_code == 400
    assert stock("laptop") == 5


def test_add_unknown_product_400(client):
    cart_id = _open_cart(client)["cart_id"]
    res = client.post(
        f"/carts/{cart_id}/items",
        json=[{"product_id": "ghost", "quantity": 1}],
    )
    assert res.status_code == 400


def test_add_batch_partial_failure_leaves_earlier_reserved(client, stock):
    """Documented limitation — no rollback in this sample."""
    cart_id = _open_cart(client)["cart_id"]
    res = client.post(
        f"/carts/{cart_id}/items",
        json=[
            {"product_id": "mouse", "quantity": 3},  # succeeds
            {"product_id": "laptop", "quantity": 100},  # fails
        ],
    )
    assert res.status_code == 400
    assert stock("mouse") == 97  # earlier reserve sticks
    assert stock("laptop") == 5


def test_add_to_unknown_cart_404(client):
    res = client.post("/carts/nope/items", json=[{"product_id": "mouse", "quantity": 1}])
    assert res.status_code == 404


def test_add_rejects_non_positive_quantity(client):
    cart_id = _open_cart(client)["cart_id"]
    res = client.post(
        f"/carts/{cart_id}/items",
        json=[{"product_id": "mouse", "quantity": 0}],
    )
    # Pydantic `Field(gt=0)` on Product.quantity -> FastAPI 422 validation error
    assert res.status_code == 422


def test_add_rejects_empty_product_id(client):
    cart_id = _open_cart(client)["cart_id"]
    res = client.post(
        f"/carts/{cart_id}/items",
        json=[{"product_id": "", "quantity": 1}],
    )
    # Pydantic `Field(min_length=1)` on Product.product_id -> 422
    assert res.status_code == 422


# ---- DELETE /carts/{cart_id} -------------------------------------------------

def test_abandon_cart_releases_stock_and_drops(client, stock):
    cart = _open_cart(client, items=[{"product_id": "mouse", "quantity": 7}])
    assert stock("mouse") == 93

    assert client.delete(f"/carts/{cart['cart_id']}").status_code == 204
    assert stock("mouse") == 100  # returned to catalog
    assert client.get(f"/carts/{cart['cart_id']}").status_code == 404


def test_abandon_unknown_cart_404(client):
    assert client.delete("/carts/nope").status_code == 404
