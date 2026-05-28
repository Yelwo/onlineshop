def test_health(client):
    res = client.get("/health")
    assert res.status_code == 200
    assert res.json() == {"status": "ok"}


def test_products_lists_catalog(client):
    res = client.get("/products")
    assert res.status_code == 200
    by_id = {p["id"]: p for p in res.json()}
    assert by_id["laptop"] == {"id": "laptop", "name": "Laptop", "price_cents": 500000, "stock": 5}
    assert by_id["mouse"]["stock"] == 100
    assert by_id["keyboard"]["stock"] == 50
