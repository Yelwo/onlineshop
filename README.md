# onlineshop

Two small services wired together through **Graftcode** (no REST/gRPC between them):

- **`pricing/`** — pricing, availability, and the discount rule engine, exposed as a Graftcode module hosted by the gateway (`gg`). In-memory product catalog and order rules (sample project, no DB).
- **`orders/`** — FastAPI service that consumes `pricing` **remotely via the generated graft** (Hypertube over WebSocket), as if it were a local dependency. In-memory order store.

```
orders (FastAPI :8000)  ──reserve/release/calculate_cart/list_products──▶  graft client (graft-pypi-pricing)
        │                                                                          │
        │                                                            Hypertube / WS (ws://pricing/ws)
        ▼                                                                          ▼
   HTTP :8000                                                         pricing gateway (gg, :80 / Vision :81)
   carts (TTL: 2h, sweeper)                                                        │
   orders (in-memory)                                                  in-memory PricingService
                                                                       (catalog + compiled rules)
```

## Running

Prereqs: Docker, and a local venv for the tests (`make install`).

```bash
make up
```

`make up` is orchestrated rather than a plain `docker compose up`, and that distinction
is **build- vs run-time**:

- **Run-time ordering already works** — `orders` waits for the gateway via
  `depends_on: condition: service_healthy`, so both services can run at once.
- **Build-time is the catch** — `orders`' image must be built with the graft index URL,
  whose token is derived from the **gateway's GUID** and changes on every rebuild.
  So the gateway has to be up _before_ `orders` is built. `make up` does exactly that:
  `docker compose up --wait pricing` → `make build-orders` (captures the token) →
  `docker compose up -d` (start `orders`).

Once the `orders` image exists, a plain `docker compose up -d` brings both up together.
`make` lists all targets (`up`, `down`, `build-orders`, `test`, `install`).

Default catalog (seeded in memory at startup):

| `id`     | name     | `price_cents` | `stock` |
| -------- | -------- | ------------- | ------- |
| laptop   | Laptop   | 500000        | 5       |
| mouse    | Mouse    | 15000         | 100     |
| keyboard | Keyboard | 30000         | 50      |

Default rules: `premium` customer → 10%, `cart.total_quantity >= 10` → +5%, capped at 20%.

## Pricing — the graft surface

Everything `orders` (or any other consumer) reaches over the graft. All methods take and
return **primitives only** (str/int/bool); structured payloads are JSON strings for the constraint and the design
that follows from it.

| Method                                  | Purpose                                                                                                                                                                                                                       |
| --------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `list_products() -> str`                | JSON list of `{id, name, price_cents, stock}` from the in-memory catalog.                                                                                                                                                     |
| `is_available(product_id, qty) -> bool` | Read-only stock check; raises on unknown product.                                                                                                                                                                             |
| `reserve(product_id, qty) -> str`       | Decrements stock if possible. Envelope return: `{status: ok\|error, ...}`.                                                                                                                                                    |
| `release(product_id, qty) -> str`       | Inverse of `reserve` — returns previously-held stock to the catalog. Envelope return.                                                                                                                                         |
| `calculate_cart(cart_json) -> str`      | Prices a multi-line cart. Input: `{"customer_type", "items":[{product_id, quantity}]}`. Envelope return: `{"status":"ok", "items":[...], "subtotal_cents", "total_price_cents", ...}` or `{"status":"error", "error":"..."}`. |
| `add_rule(rule_json) -> str`            | Validates + compiles a rule into a callable stored in `_RULES`. Envelope return.                                                                                                                                              |
| `list_rules() -> str`                   | JSON list of currently active rule sources.                                                                                                                                                                                   |
| `clear_rules() -> str`                  | Drops all rules (including defaults).                                                                                                                                                                                         |

### Rule engine

Rules are **data, not code**. A rule is a JSON object:

```jsonc
{
  "name": "tiered_premium",
  "bind": {
    "customer_type": "cart.customer_type",
    "qty": "cart.total_quantity"
  },
  "if": "customer_type == 'premium'",
  "then": {
    "discount_percent": 10,
    "rules": [{ "if": "qty >= 10", "then": { "discount_percent": 5 } }]
  }
}
```

- **`bind`** — maps variable names used in expressions to fields in the evaluation context. Allowed namespaces: `product`, `warehouse`, `order` (per line), `cart` (whole order, including the customer type).
- **`if`** — a boolean expression over the bound variables. Restricted Python: constants, names, `+ - * // %`, comparisons, `and / or / not`. No calls, attributes, subscripts.
- **`then.discount_percent`** — added to the line's discount when `if` matches.
- **`then.rules`** — optional list of **nested sub-rules**, evaluated only when the parent `if` matches; their discounts add to the parent's. Sub-rules share the parent's `bind`.

`add_rule` parses each expression once and builds a tree of closures (`lambda env: …`) stored in `_RULES`. `calculate_cart` just calls them — no `ast.parse` per request. Sum of matching rules is capped at 20% per line.

Available context fields (per line, when `calculate_cart` evaluates rules):

| Namespace   | Fields                                                            |
| ----------- | ----------------------------------------------------------------- |
| `product`   | `id`, `name`, `price_cents`                                       |
| `warehouse` | `stock`                                                           |
| `order`     | `quantity` (this line)                                            |
| `cart`      | `customer_type`, `total_quantity`, `item_count`, `subtotal_cents` |

Defaults seeded at import time:

```jsonc
[
  {
    "name": "premium_discount",
    "bind": { "customer_type": "cart.customer_type" },
    "if": "customer_type == 'premium'",
    "then": { "discount_percent": 10 }
  },

  {
    "name": "bulk_discount",
    "bind": { "total_quantity": "cart.total_quantity" },
    "if": "total_quantity >= 10",
    "then": { "discount_percent": 5 }
  }
]
```

## Orders — HTTP API

FastAPI on `:8000`. Flow:

1. `POST /carts` — open a cart for a customer; you get back a `cart_id` and a **fixed** `expires_at = created_at + CART_TTL` (default 2h).
2. `POST /carts/{cart_id}/items` — drop a product in. **This calls `reserve` immediately**, so the item leaves the warehouse the moment it lands in the cart. Adds do **not** extend the expiry — the cart has one lifetime, set at creation.
3. `GET /carts/{cart_id}` — current state + the live `calculate_cart` quote for what's in it.
4. `POST /orders/{cart_id}` — checkout. Items are already reserved, so this only prices, records the order, and removes the cart. No body.
5. **When the cart's deadline passes**, a background sweeper releases every line back to stock **as a whole** and drops the cart — every product in the cart dies together at exactly one moment. A `GET`/add/checkout that lands on an already-expired cart returns 404 (and triggers the release inline). `DELETE /carts/{cart_id}` does the same on demand.

Configurable via env on the `orders` container (compose):

| Var                           | Default | Meaning                                                |
| ----------------------------- | ------- | ------------------------------------------------------ |
| `CART_TTL_SECONDS`            | 7200    | Cart lifetime from creation. Fixed; adds don't extend. |
| `CART_SWEEP_INTERVAL_SECONDS` | 60      | How often the background sweeper checks.               |

For a quick expiry demo: `CART_TTL_SECONDS=10 CART_SWEEP_INTERVAL_SECONDS=2`.

### `GET /health`

Liveness probe.

```bash
curl -s localhost:8000/health
```

### `GET /products`

Proxies `pricing.list_products()` — the full catalog with current stock.

```bash
curl -s localhost:8000/products
```

Every endpoint that takes a body uses the same `Cart` model (`{customer_type, items}`).
The cart's response always includes a `quote` field (the total price in cents after
rules are applied) whenever `items` is non-empty, so there's no separate ad-hoc
pricing endpoint. Per-line subtotals are derivable from `items[*].unit_price_cents *
quantity`; the overall discount shows up on the `Order` once the cart is checked out.

### `POST /carts`

Open a new empty cart. `customer_type` (default `regular`) is a **query parameter** and
is fixed for the cart's lifetime — to change it, create another cart. No request body.
Items are added in subsequent `POST /carts/{cart_id}/items` calls.

```bash
# Default (regular) customer
curl -s -X POST localhost:8000/carts

# Premium customer
curl -s -X POST 'localhost:8000/carts?customer_type=premium'
```

Errors:

```bash
# Unknown customer type -> 400
curl -i -X POST 'localhost:8000/carts?customer_type=vip'
```

### `POST /carts/{cart_id}/items`

Batch-add to an existing cart. **Calls `pricing.reserve` immediately** for each item —
stock drops at this point, not at checkout. Repeat adds of the same product aggregate
into one line. The cart's `customer_type` and `expires_at` are **not** touched (one
fixed lifetime from creation, see flow note above).

Body is a **JSON array** of items; clients only send `product_id` + `quantity`,
the server enriches `name` + `unit_price_cents` from the catalog. Pydantic enforces
the shape (`product_id` non-empty, `quantity > 0`) and rejects malformed bodies with
**422** before the handler runs. Response is the updated cart with a fresh `quote`
(total price after rules).

```bash
# Single item
curl -s -X POST localhost:8000/carts/<CART_ID>/items -H 'Content-Type: application/json' \
  -d '[{"product_id":"mouse","quantity":2}]'

# Several items in one call
curl -s -X POST localhost:8000/carts/<CART_ID>/items -H 'Content-Type: application/json' \
  -d '[{"product_id":"mouse","quantity":3},{"product_id":"keyboard","quantity":2}]'
```

Errors:

```bash
# Insufficient stock (laptop has stock 5) -> 400 (from reserve envelope)
curl -i -X POST localhost:8000/carts/<CART_ID>/items -H 'Content-Type: application/json' \
  -d '[{"product_id":"laptop","quantity":100}]'

# Unknown product -> 400
curl -i -X POST localhost:8000/carts/<CART_ID>/items -H 'Content-Type: application/json' \
  -d '[{"product_id":"ghost","quantity":1}]'

# Empty items list -> 400
curl -i -X POST localhost:8000/carts/<CART_ID>/items -H 'Content-Type: application/json' \
  -d '[]'

# Cart already expired -> 404 (sweeper or lazy-check releases & drops it)
curl -i -X POST localhost:8000/carts/expired-id/items -H 'Content-Type: application/json' \
  -d '[{"product_id":"mouse","quantity":1}]'
```

### `GET /carts/{cart_id}`

Cart state + live quote (omitted for empty carts).

```bash
curl -s localhost:8000/carts/<CART_ID>
curl -i localhost:8000/carts/does-not-exist             # 404
```

### `DELETE /carts/{cart_id}`

Manually abandon a cart: every line's reserved quantity is returned to stock and the
cart is dropped. `204 No Content` on success.

```bash
curl -i -X DELETE localhost:8000/carts/<CART_ID>
```

### `POST /orders/{cart_id}`

Checkout. Items are already reserved (they were taken out of stock when added to the
cart), so this only **prices** the cart via `calculate_cart`, records the order, and
removes the cart. No body — `cart_id` is the path parameter.

```bash
curl -s -X POST localhost:8000/orders/<CART_ID>

# Catalog after the order — stock stays at the level set by add-to-cart (not changed again here)
curl -s localhost:8000/products
```

End-to-end with `jq` (open cart → batch-add → checkout → fetch order):

```bash
CID=$(curl -s -X POST 'localhost:8000/carts?customer_type=premium' | jq -r .cart_id)
curl -s -X POST localhost:8000/carts/$CID/items -H 'Content-Type: application/json' \
        -d '[{"product_id":"mouse","quantity":4},{"product_id":"keyboard","quantity":6}]' > /dev/null
OID=$(curl -s -X POST localhost:8000/orders/$CID | jq -r .order_id)
curl -s localhost:8000/orders/$OID | jq .
```

Errors:

```bash
# Unknown cart -> 404
curl -i -X POST localhost:8000/orders/does-not-exist

# Cart expired between adding items and checkout -> 404 (items already released)
curl -i -X POST localhost:8000/orders/<EXPIRED_CART_ID>

# Empty cart -> 400
curl -i -X POST localhost:8000/orders/<EMPTY_CART_ID>
```

### `GET /orders/{order_id}`

Retrieves a previously placed order from the in-memory store.

```bash
curl -s localhost:8000/orders/<ORDER_ID>
curl -i localhost:8000/orders/does-not-exist            # 404
```

### Demo: watch stock bounce on cart expiry

With a short TTL (set `CART_TTL_SECONDS=5 CART_SWEEP_INTERVAL_SECONDS=2` in compose):

```bash
curl -s localhost:8000/products | jq '.[] | select(.id=="mouse") | .stock'   # 100
CID=$(curl -s -X POST localhost:8000/carts | jq -r .cart_id)
curl -s -X POST localhost:8000/carts/$CID/items -H 'Content-Type: application/json' \
  -d '[{"product_id":"mouse","quantity":3}]' > /dev/null
curl -s localhost:8000/products | jq '.[] | select(.id=="mouse") | .stock'   # 97 (reserved)
sleep 8                                                                       # past TTL + one sweep cycle
curl -s localhost:8000/products | jq '.[] | select(.id=="mouse") | .stock'   # 100 (released)
curl -i localhost:8000/carts/$CID                                             # 404
```

## Working within Graftcode Alpha constraints

The interesting part of this task was that several requirements collide with Graftcode's
documented [Alpha limitations](https://academy.graftcode.com/documentation/how-graftcode-works/alpha-limitations-and-known-constraints)
and with what the Python runtime actually implements. How each was handled:

| Constraint hit                                                                                                                                                                                                  | What we did                                                                                                                                                                                                                    |
| --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| **`Decimal` (and other primitive-wrapper types) aren't supported** — the wheel fails to generate (HTTP 500). The task nudges toward `Decimal` for money.                                                        | Money is modelled as **integer minor units (`*_cents`)** — exact (no float rounding) and a supported primitive.                                                                                                                |
| **Returning a custom object** generates, but on the Python consumer it materializes as `None` (the SDK's remote-reference handlers are `not implemented in Python`). A `-> dict` return **500s** at generation. | All structured outputs (`calculate_cart`, `list_products`, `reserve`, `add_rule`, `list_rules`) return a **JSON string**; the consumer `json.loads` it. Rich shapes live inside each service; only primitives cross the graft. |
| **Exposed classes cannot inherit** (and the analyzer aborts on builtin-subclassing, e.g. `class X(str, Enum)`).                                                                                                 | The exposed module is **plain Python**: a single `PricingService` class, `str` constants instead of an `Enum`, no Pydantic/ORM models, plain `Exception`s.                                                                     |
| **Compiler/AST helpers can't appear on the graft surface** (exotic params/returns 500 the codegen).                                                                                                             | The rule compiler (`build`, `compile_node`, `compile_cond`) lives in **nested functions inside `add_rule`** and `_RULES` holds compiled callables — neither is grafted (gg ignores nested defs and variables).                 |
| **Auth plugins are not implemented in the Python runtime** (only Node.js); Alpha docs say JWT must be passed as a method parameter.                                                                             | Auth is intentionally **left out** of the graft surface for now — the only Python-viable option (token-as-argument) leaks the credential into the public interface, so it's deferred rather than done badly.                   |
| **The free-tier graft index token changes on every gateway rebuild.**                                                                                                                                           | `make build-orders` reads the gateway's `GUID` from its logs, builds `https://grft.dev/simple/<GUID>__free`, and passes it as the `GRAFT_INDEX_URL` build-arg — no hardcoded token.                                            |
| **`host=inmemory` (the default) can't load a consumed package** (the impl isn't bundled in the published wheel).                                                                                                | The consumer talks to the gateway **remotely**: `GraftConfig.host = "ws://pricing/ws"` over the compose network.                                                                                                               |
| **Exceptions don't cross the graft** (`ExceptionHandler` not implemented in Python).                                                                                                                            | Every graft-exposed method that can fail on bad input (`reserve`, `add_rule`, `calculate_cart`) returns an envelope (`{"status":"ok"\|"error", ...}`); the consumer (`orders`) maps `error` envelopes to HTTP 400.             |
| **The gateway needs time to come up / register before it can serve.**                                                                                                                                           | `pricing` has a healthcheck (TCP :80); `orders` uses `depends_on: condition: service_healthy`.                                                                                                                                 |

## Testing

Both services are covered by pytest, run fully **in-memory** (no Docker, no gateway):

```bash
make install   # pip install -e "pricing[test]" -e "orders[test]" into .venv
make test      # 28 pricing + 36 orders, each suite invoked from its own dir
```

### Pricing (`pricing/tests/`)

The catalog is monkeypatched per-test, `factory_boy` builds test `Product`s, and rules
are reset to the defaults before each test by recompiling them through `add_rule`. Pure
unit-style — exercises `calculate_cart`, the rule engine, `reserve`/`release`,
catalog management.

### Orders (`orders/tests/`)

FastAPI's `TestClient` hits the actual HTTP endpoints, but **without a running graft
gateway**. The trick is in `conftest.py`:

- `orders/app.py` wraps `from graft_pypi_pricing...` in `try/except ImportError`. On the
  host the package isn't installed (it only ships in the Docker image), so
  `PricingService` is left as `None`.
- A `pricing_service` fixture injects the **real in-process** `PricingService` from
  `pricing/` via `monkeypatch.setattr(orders_app, "PricingService", pricing.PricingService)`.
- `_reset_state` (autouse) wipes `_CARTS`, `_ORDERS`, `_CATALOG_CACHE`, and reseeds
  the pricing catalog + default rules before every test.
- The cart-expiry sweeper isn't started (TestClient isn't used as a context manager,
  so `lifespan` doesn't fire); time-based tests instead monkeypatch `orders_app._now`
  to time-travel deterministically.

Effectively LOCAL mode — real pricing logic, real HTTP, zero infra. The same code path
also documents how a future `PRICING_MODE=local|remote` toggle would work.

`make test` runs each suite from inside its own directory to avoid `python -m`'s CWD
insertion shadowing the `pricing` editable install with the `pricing/` source dir of
the same name.
