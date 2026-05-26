# onlineshop

Two small services wired together through **Graftcode** (no REST/gRPC between them):

- **`pricing/`** — pricing & availability logic, exposed as a Graftcode module hosted by the gateway (`gg`). In-memory product catalog (sample project, no DB).
- **`orders/`** — FastAPI service that consumes `pricing` **remotely via the generated graft** (Hypertube over WebSocket), as if it were a local dependency.

```
orders (FastAPI)  ──calculate_price()──▶  graft client (graft-pypi-pricing)
        │                                         │
        │                              Hypertube / WS (ws://pricing/ws)
        ▼                                         ▼
   HTTP :8000                          pricing gateway (gg, :80 / Vision :81)
                                                  │
                                       in-memory PricingService
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

Then:

```bash
curl "http://localhost:8000/price/laptop?quantity=10&customer_type=premium"
# {"product_id":"laptop","unit_price_cents":500000,"quantity":10,"discount_percent":15,"total_price_cents":4250000}
```

`make` lists all targets (`up`, `down`, `build-orders`, `test`, `install`).

### orders endpoints

| Endpoint                                           | What it does                                              |
| -------------------------------------------------- | --------------------------------------------------------- |
| `GET /health`                                      | liveness                                                  |
| `GET /price/{product_id}?quantity=&customer_type=` | calls `pricing` over the graft, returns the parsed result |
| `POST /orders/total`                               | local order total from a posted `Order`                   |

## Working within Graftcode Alpha constraints

The interesting part of this task was that several requirements collide with Graftcode's
documented [Alpha limitations](https://academy.graftcode.com/documentation/how-graftcode-works/alpha-limitations-and-known-constraints)
and with what the Python runtime actually implements. How each was handled:

| Constraint hit                                                                                                                                                                                                  | What we did                                                                                                                                                                                                  |
| --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| **`Decimal` (and other primitive-wrapper types) aren't supported** — the wheel fails to generate (HTTP 500). The task nudges toward `Decimal` for money.                                                        | Money is modelled as **integer minor units (`*_cents`)** — exact (no float rounding) and a supported primitive.                                                                                              |
| **Returning a custom object** generates, but on the Python consumer it materializes as `None` (the SDK's remote-reference handlers are `not implemented in Python`). A `-> dict` return **500s** at generation. | `calculate_price` returns a **JSON string** (a primitive); the consumer `json.loads` it back into the full breakdown. The rich shape lives inside each service, only primitives cross the graft.             |
| **Exposed classes cannot inherit** (and the analyzer aborts on builtin-subclassing, e.g. `class X(str, Enum)`).                                                                                                 | The exposed module is **plain Python**: a single `PricingService` class, `str` constants instead of an `Enum`, no Pydantic/ORM models, plain `Exception`s.                                                   |
| **Auth plugins are not implemented in the Python runtime** (only Node.js); Alpha docs say JWT must be passed as a method parameter.                                                                             | Auth is intentionally **left out** of the graft surface for now — the only Python-viable option (token-as-argument) leaks the credential into the public interface, so it's deferred rather than done badly. |
| **The free-tier graft index token changes on every gateway rebuild.**                                                                                                                                           | `make build-orders` reads the gateway's `GUID` from its logs, builds `https://grft.dev/simple/<GUID>__free`, and passes it as the `GRAFT_INDEX_URL` build-arg — no hardcoded token.                          |
| **`host=inmemory` (the default) can't load a consumed package** (the impl isn't bundled in the published wheel).                                                                                                | The consumer talks to the gateway **remotely**: `GraftConfig.host = "ws://pricing/ws"` over the compose network.                                                                                             |
| **The gateway needs time to come up / register before it can serve.**                                                                                                                                           | `pricing` has a healthcheck (TCP :80); `orders` uses `depends_on: condition: service_healthy`.                                                                                                               |

## Testing

`pricing` logic is covered by pytest, run fully **in-memory** (no Docker, no gateway):
the catalog is monkeypatched and `factory_boy` builds test products.

```bash
make install   # pip install -e "pricing[test]" into .venv
make test      # pytest pricing/tests
```
