import json

from pricing import PricingService


def _discount(product_id, quantity, customer_type):
    cart = json.dumps(
        {"customer_type": customer_type, "items": [{"product_id": product_id, "quantity": quantity}]}
    )
    return json.loads(PricingService.calculate_cart(cart))["items"][0]["discount_percent"]


def _add(rule: dict) -> dict:
    return json.loads(PricingService.add_rule(json.dumps(rule)))


def test_default_rules_drive_discounts(make_product):
    make_product(id="laptop", price_cents=500000, stock=100)
    assert _discount("laptop", 1, "premium") == 10
    assert _discount("laptop", 10, "regular") == 5
    assert _discount("laptop", 10, "premium") == 15


def test_discount_is_capped_at_20(make_product):
    make_product(id="laptop", price_cents=500000, stock=100)
    assert _add(
        {
            "name": "promo",
            "if": "quantity >= 1",
            "then": {"discount_percent": 15},
            "bind": {"quantity": "order.quantity"},
        }
    )["status"] == "ok"
    # 10 (premium) + 5 (bulk) + 15 (promo) = 30 -> capped at 20
    assert _discount("laptop", 10, "premium") == 20


def test_add_rule_composed_condition_over_fields(make_product):
    make_product(id="mouse", price_cents=15000, stock=100)
    assert _add(
        {
            "name": "cheap_bulk",
            "if": "price <= 20000 and quantity >= 3",
            "then": {"discount_percent": 3},
            "bind": {"price": "product.price_cents", "quantity": "order.quantity"},
        }
    )["status"] == "ok"
    assert _discount("mouse", 3, "regular") == 3
    assert _discount("mouse", 2, "regular") == 0  # quantity condition fails


def test_nested_if_rules_only_apply_when_parent_matches(make_product):
    make_product(id="laptop", price_cents=500000, stock=100)
    PricingService.clear_rules()
    # premium -> 10; and ONLY for premium, a nested bulk tier adds +5 when total >= 10
    assert _add(
        {
            "name": "tiered_premium",
            "bind": {"customer_type": "cart.customer_type", "qty": "cart.total_quantity"},
            "if": "customer_type == 'premium'",
            "then": {
                "discount_percent": 10,
                "rules": [{"if": "qty >= 10", "then": {"discount_percent": 5}}],
            },
        }
    )["status"] == "ok"

    assert _discount("laptop", 10, "premium") == 15  # outer + nested
    assert _discount("laptop", 1, "premium") == 10   # nested skipped (qty < 10)
    assert _discount("laptop", 10, "regular") == 0   # outer fails -> nested never runs


def test_clear_rules_disables_discounts(make_product):
    make_product(id="laptop", price_cents=500000, stock=100)
    PricingService.clear_rules()
    assert _discount("laptop", 10, "premium") == 0


def test_add_rule_rejects_unsafe_expression():
    res = _add(
        {
            "name": "evil",
            "if": "__import__('os').system('boom')",
            "then": {"discount_percent": 1},
            "bind": {},
        }
    )
    assert res["status"] == "error"


def test_add_rule_rejects_unbound_variable():
    res = _add(
        {
            "name": "bad",
            "if": "a + b <= c",
            "then": {"discount_percent": 1},
            "bind": {"a": "order.quantity"},  # b, c unbound
        }
    )
    assert res["status"] == "error"


def test_add_rule_rejects_unbound_variable_in_nested_node():
    res = _add(
        {
            "name": "bad_nested",
            "if": "premium",
            "then": {
                "discount_percent": 10,
                "rules": [{"if": "qty >= 10", "then": {"discount_percent": 5}}],
            },
            "bind": {"premium": "cart.customer_type"},  # qty unbound
        }
    )
    assert res["status"] == "error"


def test_add_rule_rejects_unknown_namespace():
    res = _add(
        {
            "name": "bad",
            "if": "x > 0",
            "then": {"discount_percent": 1},
            "bind": {"x": "foo.bar"},
        }
    )
    assert res["status"] == "error"


def test_add_rule_rejects_bad_structure():
    assert json.loads(PricingService.add_rule("{not json"))["status"] == "error"
    assert _add({"name": "x", "if": "1 > 0"})["status"] == "error"  # missing keys
