from datetime import timedelta


def _open_with_stock(client, items):
    res = client.post("/carts", params={"customer_type": "regular"})
    assert res.status_code == 201
    cart_id = res.json()["cart_id"]
    if items:
        res = client.post(f"/carts/{cart_id}/items", json=items)
        assert res.status_code == 200
    return cart_id


def _time_travel(app_module, monkeypatch, delta: timedelta) -> None:
    """Make `app_module._now()` return a moment `delta` in the future."""
    later = app_module._now() + delta
    monkeypatch.setattr(app_module, "_now", lambda: later)


def test_get_expired_cart_returns_404_and_releases_stock(client, app_module, monkeypatch, stock):
    cart_id = _open_with_stock(client, [{"product_id": "mouse", "quantity": 4}])
    assert stock("mouse") == 96

    _time_travel(app_module, monkeypatch, timedelta(hours=3))

    res = client.get(f"/carts/{cart_id}")
    assert res.status_code == 404
    assert "expired" in res.json()["detail"]
    assert stock("mouse") == 100  # released inline by the lazy-expiry check


def test_add_to_expired_cart_returns_404_and_releases(client, app_module, monkeypatch, stock):
    cart_id = _open_with_stock(client, [{"product_id": "mouse", "quantity": 3}])
    _time_travel(app_module, monkeypatch, timedelta(hours=3))

    res = client.post(f"/carts/{cart_id}/items", json=[{"product_id": "mouse", "quantity": 1}])
    assert res.status_code == 404
    assert stock("mouse") == 100


def test_checkout_expired_cart_returns_404_and_releases(client, app_module, monkeypatch, stock):
    cart_id = _open_with_stock(client, [{"product_id": "keyboard", "quantity": 2}])
    assert stock("keyboard") == 48

    _time_travel(app_module, monkeypatch, timedelta(hours=3))

    res = client.post(f"/orders/{cart_id}")
    assert res.status_code == 404
    assert stock("keyboard") == 50


def test_expires_at_is_fixed_and_not_extended_by_add(client):
    res1 = client.post("/carts", params={"customer_type": "regular"})
    cart_id = res1.json()["cart_id"]
    expires_at_initial = res1.json()["expires_at"]

    # adding a line does not bump the deadline
    res2 = client.post(f"/carts/{cart_id}/items", json=[{"product_id": "mouse", "quantity": 1}])
    assert res2.json()["expires_at"] == expires_at_initial

    # neither does subsequent reads
    res3 = client.get(f"/carts/{cart_id}")
    assert res3.json()["expires_at"] == expires_at_initial


def test_active_cart_just_before_expiry_still_works(client, app_module, monkeypatch):
    cart_id = _open_with_stock(client, [{"product_id": "mouse", "quantity": 1}])

    # one second before expiry — still alive
    _time_travel(app_module, monkeypatch, timedelta(hours=2) - timedelta(seconds=1))

    assert client.get(f"/carts/{cart_id}").status_code == 200
