# Pure Python only — no Enum, no Pydantic, no Decimal (all break Graftcode codegen).
# Money is in integer minor units (grosze). Graft-exposed methods keep PRIMITIVE
# signatures (str/int/bool -> str/bool); the rule compiler/evaluator lives in nested
# helpers so dicts/AST/callables never reach the graft surface (a method with a
# `dict` parameter or `-> dict` return fails to generate).

import ast
import json


class Product:
    def __init__(self, id: str, name: str, price_cents: int, stock: int):
        self.id = id
        self.name = name
        self.price_cents = price_cents
        self.stock = stock


# AST nodes a rule expression may use (no calls, attributes, subscripts, etc.).
_ALLOWED_NODES = (
    ast.Expression, ast.Load,
    ast.Constant, ast.Name, ast.BoolOp, ast.And, ast.Or,
    ast.UnaryOp, ast.Not, ast.USub,
    ast.BinOp, ast.Add, ast.Sub, ast.Mult, ast.FloorDiv, ast.Mod,
    ast.Compare, ast.Lt, ast.LtE, ast.Gt, ast.GtE, ast.Eq, ast.NotEq,
)
# Field namespaces a rule may bind to. `cart` holds order-wide attributes (customer
# type, aggregates) so rules can react to the whole order; product/order/warehouse
# are per line.
_NAMESPACES = ("product", "warehouse", "order", "cart")

# Default discount rules (sources). They are compiled into PricingService._RULES at
# import time, just like any rule added later via add_rule().
_DEFAULT_RULES_SRC = [
    {
        "name": "premium_discount",
        "bind": {"customer_type": "cart.customer_type"},
        "if": "customer_type == 'premium'",
        "then": {"discount_percent": 10},
    },
    {
        "name": "bulk_discount",
        "bind": {"total_quantity": "cart.total_quantity"},
        "if": "total_quantity >= 10",
        "then": {"discount_percent": 5},
    },
]


class PricingService:
    REGULAR = "regular"
    PREMIUM = "premium"

    _MAX_DISCOUNT = 20

    _CATALOG: dict[str, Product] = {
        "laptop": Product("laptop", "Laptop", 500000, 5),
        "mouse": Product("mouse", "Mouse", 15000, 100),
        "keyboard": Product("keyboard", "Keyboard", 30000, 50),
    }

    # Compiled rules: a list of callables `fn(context) -> int` (the discount this rule
    # contributes, 0 if it doesn't match). Built by add_rule(); seeded below.
    _RULES: list = []

    # ---- graft-exposed methods (primitive signatures only) ----

    @staticmethod
    def calculate_cart(cart_json: str) -> str:
        """Price a cart: {"customer_type": str, "items": [{product_id, quantity}]}.
        Items are trusted (they come from a reserved cart) — pricing only validates
        JSON shape and customer_type. Rules run per line with cart-wide aggregates
        available as `cart.*`. Envelope return."""
        try:
            cart = json.loads(cart_json)
        except (ValueError, TypeError) as exc:
            return json.dumps({"status": "error", "error": f"invalid JSON: {exc}"})

        customer_type = cart.get("customer_type", "regular")
        items = cart.get("items", [])
        if customer_type not in (PricingService.REGULAR, PricingService.PREMIUM):
            return json.dumps({"status": "error", "error": f"Unknown customer type: {customer_type!r}"})

        cart_ns = {
            "customer_type": customer_type,
            "total_quantity": sum(item["quantity"] for item in items),
            "item_count": len(items),
            "subtotal_cents": sum(
                PricingService._CATALOG[item["product_id"]].price_cents * item["quantity"]
                for item in items
            ),
        }

        out_items = []
        order_total = 0
        for item in items:
            product = PricingService._CATALOG[item["product_id"]]
            qty = item["quantity"]
            context = {
                "product": {"id": product.id, "name": product.name, "price_cents": product.price_cents},
                "warehouse": {"stock": product.stock},
                "order": {"quantity": qty},
                "cart": cart_ns,
            }
            discount = min(sum(fn(context) for fn in PricingService._RULES), PricingService._MAX_DISCOUNT)

            base = product.price_cents * qty
            line_total = (base * (100 - discount) + 50) // 100
            out_items.append(
                {
                    "product_id": product.id,
                    "unit_price_cents": product.price_cents,
                    "quantity": qty,
                    "discount_percent": discount,
                    "line_total_cents": line_total,
                }
            )
            order_total += line_total

        return json.dumps(
            {
                "status": "ok",
                "customer_type": customer_type,
                "items": out_items,
                "subtotal_cents": cart_ns["subtotal_cents"],
                "total_price_cents": order_total,
            }
        )

    @staticmethod
    def is_available(product_id: str, quantity: int) -> bool:
        product = PricingService._get_product(product_id)
        if product is None:
            raise Exception(f"Unknown product: {product_id!r}")
        return PricingService._has_stock(product, quantity)

    @staticmethod
    def list_products() -> str:
        return json.dumps(
            [
                {"id": p.id, "name": p.name, "price_cents": p.price_cents, "stock": p.stock}
                for p in PricingService._CATALOG.values()
            ]
        )

    @staticmethod
    def reserve(product_id: str, quantity: int) -> str:
        product = PricingService._get_product(product_id)
        if product is None:
            return json.dumps({"status": "error", "error": f"Unknown product: {product_id!r}"})
        if quantity <= 0:
            return json.dumps({"status": "error", "error": f"Quantity must be positive, got {quantity!r}"})
        if product.stock < quantity:
            return json.dumps({"status": "error", "error": "Insufficient stock", "available": product.stock})
        product.stock -= quantity
        return json.dumps(
            {"status": "ok", "product_id": product.id, "quantity": quantity, "remaining_stock": product.stock}
        )

    @staticmethod
    def release(product_id: str, quantity: int) -> str:
        """Inverse of reserve — return previously-held stock to the catalog."""
        product = PricingService._get_product(product_id)
        if product is None:
            return json.dumps({"status": "error", "error": f"Unknown product: {product_id!r}"})
        if quantity <= 0:
            return json.dumps({"status": "error", "error": f"Quantity must be positive, got {quantity!r}"})
        product.stock += quantity
        return json.dumps(
            {"status": "ok", "product_id": product.id, "released": quantity, "stock": product.stock}
        )

    @staticmethod
    def list_rules() -> str:
        return json.dumps([fn.source for fn in PricingService._RULES])

    @staticmethod
    def clear_rules() -> str:
        PricingService._RULES = []
        return json.dumps({"status": "ok", "rules": 0})

    @staticmethod
    def add_rule(rule_json: str) -> str:
        """Validate a rule (incl. nested if/then trees) and COMPILE it into a callable
        stored in _RULES, so calculate_cart never re-parses anything."""
        try:
            rule = json.loads(rule_json)
        except (ValueError, TypeError) as exc:
            return json.dumps({"status": "error", "error": f"invalid JSON: {exc}"})

        def build(node):
            """nested compiler: AST node -> closure(env) -> value (parsed once)"""
            if isinstance(node, ast.Constant):
                value = node.value
                return lambda env: value
            if isinstance(node, ast.Name):
                name = node.id
                return lambda env: env[name]
            if isinstance(node, ast.UnaryOp):
                operand = build(node.operand)
                if isinstance(node.op, ast.Not):
                    return lambda env: not operand(env)
                if isinstance(node.op, ast.USub):
                    return lambda env: -operand(env)
                raise ValueError("unsupported unary operator")
            if isinstance(node, ast.BoolOp):
                parts = [build(v) for v in node.values]
                if isinstance(node.op, ast.And):
                    return lambda env: all(p(env) for p in parts)
                return lambda env: any(p(env) for p in parts)
            if isinstance(node, ast.BinOp):
                left, right, op = build(node.left), build(node.right), type(node.op)
                if op is ast.Add:
                    return lambda env: left(env) + right(env)
                if op is ast.Sub:
                    return lambda env: left(env) - right(env)
                if op is ast.Mult:
                    return lambda env: left(env) * right(env)
                if op is ast.FloorDiv:
                    return lambda env: left(env) // right(env)
                if op is ast.Mod:
                    return lambda env: left(env) % right(env)
                raise ValueError("unsupported binary operator")
            if isinstance(node, ast.Compare):
                left = build(node.left)
                ops = [type(o) for o in node.ops]
                comps = [build(c) for c in node.comparators]

                def compare(env):
                    cur = left(env)
                    for op, comp in zip(ops, comps):
                        nxt = comp(env)
                        ok = (
                            cur < nxt if op is ast.Lt else
                            cur <= nxt if op is ast.LtE else
                            cur > nxt if op is ast.Gt else
                            cur >= nxt if op is ast.GtE else
                            cur == nxt if op is ast.Eq else
                            cur != nxt
                        )
                        if not ok:
                            return False
                        cur = nxt
                    return True

                return compare
            raise ValueError(f"unsupported expression: {type(node).__name__}")

        def compile_cond(expr):
            if not isinstance(expr, str):
                raise ValueError("'if' must be a string")
            for n in ast.walk(ast.parse(expr, mode="eval")):
                if not isinstance(n, (ast.Name,) + _ALLOWED_NODES):
                    raise ValueError(f"disallowed syntax in {expr!r}: {type(n).__name__}")
            return build(ast.parse(expr, mode="eval").body)

        def names_in(expr):
            return {n.id for n in ast.walk(ast.parse(expr, mode="eval")) if isinstance(n, ast.Name)}

        def compile_node(node, used):
            """rule node dict -> closure(env) -> int ; collects names; validates structure"""
            if not isinstance(node, dict) or "if" not in node or "then" not in node:
                raise ValueError("each node needs 'if' and 'then'")
            then = node["then"]
            if not isinstance(then, dict) or not isinstance(then.get("discount_percent"), int):
                raise ValueError("'then.discount_percent' must be an integer")
            cond = compile_cond(node["if"])
            used |= names_in(node["if"])
            disc = then["discount_percent"]
            subs = then.get("rules", [])
            if not isinstance(subs, list):
                raise ValueError("'then.rules' must be a list")
            sub_fns = [compile_node(s, used) for s in subs]
            return lambda env: (disc + sum(f(env) for f in sub_fns)) if cond(env) else 0

        try:
            if not isinstance(rule, dict):
                raise ValueError("rule must be a JSON object")
            for key in ("name", "if", "then", "bind"):
                if key not in rule:
                    raise ValueError(f"missing key: {key!r}")
            if not isinstance(rule["name"], str):
                raise ValueError("'name' must be a string")
            if not isinstance(rule["bind"], dict):
                raise ValueError("'bind' must be an object")
            for var, path in rule["bind"].items():
                if not isinstance(path, str) or path.split(".", 1)[0] not in _NAMESPACES:
                    raise ValueError(f"bind {var!r} must map to one of {_NAMESPACES}.field")

            used: set = set()
            tree_fn = compile_node(rule, used)
            unbound = used - set(rule["bind"])
            if unbound:
                raise ValueError(f"unbound variables (not in 'bind'): {sorted(unbound)}")
        except ValueError as exc:
            return json.dumps({"status": "error", "error": str(exc)})

        bind = rule["bind"]

        def fn(context):
            env = {}
            for var, path in bind.items():
                ns, field = path.split(".", 1)
                env[var] = context[ns][field]
            return tree_fn(env)

        fn.source = {"name": rule["name"], "if": rule["if"], "then": rule["then"], "bind": bind}
        PricingService._RULES.append(fn)
        return json.dumps({"status": "ok", "name": rule["name"], "rules": len(PricingService._RULES)})

    @staticmethod
    def _get_product(product_id: str) -> Product | None:
        return PricingService._CATALOG.get(product_id)

    @staticmethod
    def _has_stock(product: Product, quantity: int) -> bool:
        if quantity <= 0:
            raise Exception(f"Quantity must be a positive integer, got {quantity!r}")
        return product.stock >= quantity


# Compile the default rules into PricingService._RULES at import time.
for _src in _DEFAULT_RULES_SRC:
    PricingService.add_rule(json.dumps(_src))
