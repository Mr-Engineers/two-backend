# Office-supplies shops: PL, DE, RU (REST + MCP)

Three functional shop backends from one code base. Each one is its own process/container with its own configuration,
data, REST API and MCP endpoint (`/mcp`, Streamable HTTP).

| Shop | Name | Country / currency | Dev port | Schema |
| --- | --- | --- | :---: | --- |
| `shop-pl` | Papiernia | PL / PLN | 8001 | `shop_pl` |
| `shop-de` | BüroWerk | DE / EUR | 8002 | `shop_de` |
| `shop-ru` | OfficeMarket | RU / RUB | 8003 | `shop_ru` |

Stack: Python 3.12, FastAPI + Uvicorn, Pydantic, SQLAlchemy 2 + Alembic + psycopg 3, PostgreSQL (target: Supabase),
the official MCP Python SDK, pytest.

* Integrator documentation: [`docs/integration.md`](docs/integration.md) (endpoints, MCP tools, errors, flow).
* Seed catalog (all SKUs, packs, prices, stock, origin): [`docs/seed-data.md`](docs/seed-data.md).
* Shared Supabase setup (Polish): [`docs/supabase-pl.md`](docs/supabase-pl.md).
* Postman collection and shop environments: [`docs/postman/README.md`](docs/postman/README.md).
* This is a demo shop: **payment is a mock**, data is synthetic. There is no gateway, agent, policy engine or UI here.

Every product carries a structured `country_of_origin` (ISO alpha-2); carts, quotes and orders also carry the sorted
unique `origin_countries`. The backend never blocks or warns because of an origin - the RU shop is an ordinary shop.

## Layout

```
app/            FastAPI app, ShopService (shared by REST and MCP), MCP server, SQLAlchemy models, seed, admin CLI
migrations/     Alembic (one revision applied to each shop schema, own alembic_version per schema)
scripts/        shop launcher, REST/MCP demos, local PostgreSQL, catalog import, documentation generators
tests/          pytest on a real PostgreSQL; MCP tests use the official SDK client over real HTTP
docs/           integration.md, seed-data.md, supabase-pl.md, postman/
Dockerfile, docker-compose.yml, .env.example
```

## Quick start without Docker (PowerShell)

Run commands from the repository root with Python 3.12. `scripts/local_postgres.py` starts a real throw-away
PostgreSQL (the `pgserver` wheel) in `.pgdata/` when supported by your platform. If the wheel is unavailable,
use Docker Compose below or an existing PostgreSQL 14+ server, skip the local-server command and use its owner URL.

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements-dev.txt

# 1. real PostgreSQL (prints the owner URL; use that exact URL in the next command)
python scripts/local_postgres.py start

# 2. generate local DB passwords + demo API keys -> .env.local (git-ignored). Use the host/port printed above.
python -m app.cli gen-credentials --db-port 61427 --db-name postgres --migration-url "postgresql://postgres:@127.0.0.1:61427/postgres"

# 3. create schemas + runtime users, run migrations, seed (idempotent)
python -m app.cli bootstrap

# 4. start the three shops (Ctrl+C stops all) - in this terminal, or run it in a second one
python scripts/run_shops.py

# 5. in another terminal: buy a comparable set in every shop (REST) and through MCP
python scripts/demo.py
python scripts/mcp_client.py --buy
```

After startup, Swagger UI is available at `http://127.0.0.1:8001/docs` (PL),
`http://127.0.0.1:8002/docs` (DE) and `http://127.0.0.1:8003/docs` (RU).
Each instance exposes `/openapi.json`, `/health/live`, `/health/ready` and `/mcp`.
Use `/health/ready` to check database readiness; catalog and purchase operations require a shop API key.

Linux/macOS: identical, only activate with `source .venv/bin/activate` and use `\`-continuations instead of PowerShell quoting.

Stop the throw-away database with `python scripts/local_postgres.py stop`.

### Manual single-shop start

```powershell
$env:SHOP_ID = "shop-ru"
$env:DATABASE_URL = "postgresql://shop_ru_rt:<password>@127.0.0.1:5432/postgres?sslmode=prefer"
uvicorn app.main:create_app --factory --host 127.0.0.1 --port 8003
```

```bash
SHOP_ID=shop-ru DATABASE_URL='postgresql://shop_ru_rt:<password>@127.0.0.1:5432/postgres' \
  uvicorn app.main:create_app --factory --host 127.0.0.1 --port 8003
```

The process refuses to start without `SHOP_ID` and `DATABASE_URL` (no default fake data). Production
(`APP_ENV=production`) additionally requires `sslmode=require|verify-ca|verify-full`.

## Docker Compose

```powershell
$pw = python -c "import secrets; print(secrets.token_urlsafe(24))"
python -m app.cli --env-file .env.docker gen-credentials --db-host db --db-port 5432 --db-name shops --migration-url "postgresql://postgres:$pw@db:5432/shops"
Add-Content .env.docker "POSTGRES_PASSWORD=$pw"
docker compose --env-file .env.docker up --build
```

The credential-generation command selects `.env.docker`: the CLI otherwise reads `.env.local`, which may contain
credentials for a different database. Compose injects the generated values into the bootstrap container.

`db` (PostgreSQL 16) -> `bootstrap` (one-shot: roles, migrations, seed; the only service that sees the owner account) ->
`shop-pl` / `shop-de` / `shop-ru` (one image, published on `127.0.0.1:8001/8002/8003`; each container receives only
its own runtime `DATABASE_URL`). Then run `python scripts/demo.py` from the host (put the keys from `.env.docker` in
your environment or pass `--env-file .env.docker`).

## Complete endpoint reference

All paths below are relative to the selected shop's base URL: `http://127.0.0.1:8001` (PL),
`http://127.0.0.1:8002` (DE), or `http://127.0.0.1:8003` (RU). Each shop exposes the same 17 REST
operations, documentation routes and MCP endpoint. The shop is selected by the server configuration, not a request.

Business endpoints and `/mcp` require `Authorization: Bearer <API key>` for that shop.
Health probes, Swagger UI and the OpenAPI document are public. Carts, quotes and orders belong to the
authenticated customer; identifiers belonging to another customer return a not-found error.
Optional `X-Request-ID` and `X-Correlation-ID` headers help trace requests.

### Health and API documentation

* `GET /health/live` — process liveness; returns `{"status":"ok"}` without querying the database.
* `GET /health/ready` — checks database connectivity, migration revision and store configuration;
  returns HTTP 503 when the instance is not ready.
* `GET /docs` — interactive Swagger UI; use **Authorize** to enter a Bearer API key for business requests.
* `GET /docs/oauth2-redirect` — Swagger UI's generated OAuth2 redirect helper; the shop itself uses API keys.
* `GET /openapi.json` — machine-readable REST schemas, parameters and responses.

### Catalog

* `GET /store` — shop identity, country, currency, locale, quote TTL and cart limits; returns `StoreInfo`.
* `GET /categories` — categories with active product counts; returns `{"items": [...]}`.
* `GET /products` — browse or search active products; returns `items`, `total`, `limit` and `offset`.
  Optional query parameters: `q` (name, SKU or canonical item code, up to 100 characters), `category`
  (slug, up to 64 characters), `availability` (`in_stock` or `out_of_stock`), repeatable `origin`
  (up to 30 ISO alpha-2 country codes), `sort` (`name`, `sku`, `price_asc`, `price_desc`; default `name`),
  `limit` (1–100; default 20) and `offset` (0–100000; default 0).
  Example: `/products?q=PAPER-A4&origin=PL&origin=CZ&sort=price_asc&limit=20&offset=0`.
* `GET /products/{sku}` — one product, including packaging, gross price, stock and `country_of_origin`;
  returns `ProductOut`.
* `GET /shipping-methods` — shipping methods, gross prices and estimated delivery days;
  returns `{"items": [...]}`.

Prices ending in `_minor` are integers in the shop's currency; quantities count packs, not individual units
inside a pack. `store_country` and product `country_of_origin` are independent fields.

### Carts

* `POST /carts` — create an empty cart; no request body; returns `CartOut` with HTTP 201.
* `GET /carts/{cart_id}` — read the cart, lines, subtotal, origins, status and version; returns `CartOut`.
* `PUT /carts/{cart_id}/items/{sku}` — add a product or replace its absolute pack quantity.
  JSON body: `{"quantity": 2, "expected_version": 1}`; `expected_version` is optional.
  Quantity must be an integer from 1 to the shop's `max_line_quantity`; use DELETE to remove a line.
  Returns the updated `CartOut`.
* `DELETE /carts/{cart_id}/items/{sku}` — remove one line; an absent line is a no-op.
  Optional query parameter: `expected_version` (integer, at least 1). Returns the updated `CartOut`.
* `DELETE /carts/{cart_id}/items` — clear all lines. Optional query parameter:
  `expected_version` (integer, at least 1). Returns the updated `CartOut`.

Cart IDs are UUIDs. Mutations increment the cart version; supplying a mismatched `expected_version`
returns `CART_VERSION_CONFLICT` (409). Carts do not reserve stock.

### Quotes and checkout

* `POST /carts/{cart_id}/quotes` — create an immutable quote; returns `QuoteOut` with HTTP 201.
  Required JSON field: `shipping_address`. Optional fields: `shipping_method_code` (1–32 lowercase
  letters, digits, underscores or hyphens; defaults to the shop's first shipping method) and
  `expected_cart_version` (integer, at least 1).
* `GET /quotes/{quote_id}` — read a quote's line snapshots, shipping, totals, origins and expiry;
  returns `QuoteOut`. The quote ID is a UUID.
* `POST /checkout` — purchase the quoted cart using mock payment; returns `CheckoutOut` with
  HTTP 200: `{"order": {...}, "idempotent_replay": false}`. Required JSON body:
  `{"quote_id": "<UUID>", "idempotency_key": "my-key-0001"}`. The key must contain 8–128 characters
  drawn from letters, digits, `.`, `_`, `:`, and `-`.

`shipping_address` requires `recipient_name`, `line1`, `postal_code`, `city` and `country`
(ISO alpha-2 destination code); `line2`, `phone` and `email` are optional. For example:

```json
{
  "shipping_address": {
    "recipient_name": "Jan Demo",
    "line1": "Przykladowa 1",
    "postal_code": "00-001",
    "city": "Warszawa",
    "country": "PL"
  },
  "shipping_method_code": "standard",
  "expected_cart_version": 2
}
```

Quotes expire after 300 seconds by default and do not buy or reserve stock. Checkout validates the quote,
decrements stock and creates the order in one transaction. Repeating the same quote and idempotency key
returns the original order with `idempotent_replay: true`; reusing the key for a different quote returns
`IDEMPOTENCY_CONFLICT` (409). Prices, currency, customer identity and payment outcome are server-controlled.

### Orders

* `GET /orders` — list the authenticated customer's orders, newest first; returns `items`, `total`,
  `limit` and `offset`. Optional query parameters: `limit` (1–100; default 20),
  `offset` (0–100000; default 0).
* `GET /orders/{order_id}` — read one order, including immutable product and shipping snapshots,
  totals and mock payment status; returns `OrderOut`. The order ID is a UUID.

### MCP endpoint and all tools

`POST /mcp` accepts MCP JSON-RPC requests over Streamable HTTP, with stateless JSON responses.
Use an MCP client to initialize the connection, discover tools with `tools/list`, and invoke them with
`tools/call`. Every call requires the same shop Bearer key as REST. Tool results use the same service
and response models as the corresponding REST operation.

All 15 tools are listed below; arguments marked `?` are optional:

* `get_store_info()` — `GET /store`.
* `list_categories()` — `GET /categories`.
* `search_products(q?, category?, availability?, origin_countries?, sort?, limit?, offset?)` —
  `GET /products`; use an array of country codes in `origin_countries` instead of repeatable REST `origin` parameters.
* `get_product(sku)` — `GET /products/{sku}`.
* `list_shipping_methods()` — `GET /shipping-methods`.
* `create_cart()` — `POST /carts`.
* `get_cart(cart_id)` — `GET /carts/{cart_id}`.
* `set_cart_item(cart_id, sku, quantity, expected_version?)` — `PUT /carts/{cart_id}/items/{sku}`.
* `remove_cart_item(cart_id, sku, expected_version?)` — `DELETE /carts/{cart_id}/items/{sku}`.
* `clear_cart(cart_id, expected_version?)` — `DELETE /carts/{cart_id}/items`.
* `create_checkout_quote(cart_id, shipping_address, shipping_method_code?, expected_cart_version?)` —
  `POST /carts/{cart_id}/quotes`.
* `get_checkout_quote(quote_id)` — `GET /quotes/{quote_id}`.
* `checkout(quote_id, idempotency_key)` — `POST /checkout`; creates an order and reduces stock.
* `list_orders(limit?, offset?)` — `GET /orders`.
* `get_order(order_id)` — `GET /orders/{order_id}`.

Health and documentation routes have no MCP tools. See the MCP client example below and
[`docs/integration.md`](docs/integration.md) for transport details and tool annotations.

### Error responses

REST failures return `{"error": {"code": "...", "message": "...", "request_id": "...",
"correlation_id": "...", "details": {}}}`. Common statuses are 401 for missing or invalid keys,
404 for missing or inaccessible objects, 422 for invalid input, 409 for cart conflicts or stale/expired quotes,
402 for declined mock payment and 503 for database unavailability. Unknown request body fields are rejected.
MCP tool failures use `isError: true` and include the same error payload in the result text;
authentication failures at `/mcp` return HTTP 401. The complete error-code reference is in
[`docs/integration.md`](docs/integration.md#errors).

## Request examples

Keys are in `.env.local` as `DEMO_API_KEY_SHOP_RU_AGENT` etc. PowerShell:

```powershell
Get-Content .env.local | ForEach-Object { if ($_ -match '^(DEMO_API_KEY_[A-Z_]+)=(.*)$') { Set-Item "env:$($Matches[1])" $Matches[2] } }
$h = @{ Authorization = "Bearer $env:DEMO_API_KEY_SHOP_RU_AGENT" }
$b = "http://127.0.0.1:8003"

Invoke-RestMethod "$b/store" -Headers $h
(Invoke-RestMethod "$b/products?q=PAPER-A4&origin=RU&sort=price_asc" -Headers $h).items | Select sku, unit_gross_minor, country_of_origin, stock_quantity

$cart = Invoke-RestMethod -Method Post "$b/carts" -Headers $h
$cart = Invoke-RestMethod -Method Put "$b/carts/$($cart.id)/items/RU-PAP-A4-500" -Headers $h -ContentType application/json -Body '{"quantity": 2}'
$quoteBody = '{"shipping_address":{"recipient_name":"Ivan Demo","line1":"Primernaya 1","postal_code":"101000","city":"Moskva","country":"RU"},"shipping_method_code":"standard"}'
$quote = Invoke-RestMethod -Method Post "$b/carts/$($cart.id)/quotes" -Headers $h -ContentType application/json -Body $quoteBody
$quote.origin_countries, $quote.total_gross_minor
Invoke-RestMethod -Method Post "$b/checkout" -Headers $h -ContentType application/json `
  -Body (@{ quote_id = $quote.quote_id; idempotency_key = "my-key-0001" } | ConvertTo-Json)
```

curl (Linux/macOS/Git Bash):

```bash
B=http://127.0.0.1:8003; K="$DEMO_API_KEY_SHOP_RU_AGENT"
curl -s -H "Authorization: Bearer $K" "$B/products?q=PAPER-A4&origin=RU"
CART=$(curl -s -X POST -H "Authorization: Bearer $K" $B/carts | python -c "import sys,json;print(json.load(sys.stdin)['id'])")
curl -s -X PUT -H "Authorization: Bearer $K" -H 'Content-Type: application/json' -d '{"quantity":2}' $B/carts/$CART/items/RU-PAP-A4-500
curl -s -X POST -H "Authorization: Bearer $K" -H 'Content-Type: application/json' \
  -d '{"shipping_address":{"recipient_name":"Ivan Demo","line1":"Primernaya 1","postal_code":"101000","city":"Moskva","country":"RU"}}' \
  $B/carts/$CART/quotes
curl -s -X POST -H "Authorization: Bearer $K" -H 'Content-Type: application/json' \
  -d '{"quote_id":"<quote_id>","idempotency_key":"my-key-0001"}' $B/checkout
```

MCP (official Python SDK): see [`scripts/mcp_client.py`](scripts/mcp_client.py) - minimal form:

```python
from mcp import Client
from mcp.client.streamable_http import streamable_http_client
from mcp.shared._httpx_utils import create_mcp_http_client

http = create_mcp_http_client(headers={"Authorization": f"Bearer {key}"})
async with http, Client(streamable_http_client("http://127.0.0.1:8003/mcp", http_client=http)) as client:
    result = await client.call_tool("search_products", {"q": "PAPER-A4"})
    print(result.structured_content["items"][0]["country_of_origin"])
```

## Admin CLI

Uses the owner/migration account (`MIGRATION_DATABASE_URL`), never the runtime users.

| Command | What it does |
| --- | --- |
| `python -m app.cli gen-credentials` | generates runtime-user passwords and demo API keys into `.env.local` (kept if already present) |
| `python -m app.cli setup-db` | creates schemas `shop_*` and the runtime users |
| `python -m app.cli migrate` | `alembic upgrade head` for every schema + least-privilege grants |
| `python -m app.cli seed [--shop shop-pl]` | idempotent seed; **never deletes orders and never restores sold stock** |
| `python -m app.cli bootstrap` | `setup-db` + `migrate` + `seed` |
| `python -m app.cli print-setup-sql` | SQL for the Supabase SQL editor (passwords as placeholders) |
| `python -m app.cli reset-demo --shop shop-pl --yes` | **destructive** demo reset (needs `ALLOW_DEMO_RESET=true`, refused when `APP_ENV=production`) |

Demo API keys are hashed (SHA-256) in the database and printed nowhere except in the git-ignored `.env.local`.
To create a key for a real integrator use `app.security.generate_api_key()` and store only the hash.

## Connecting Supabase

Instrukcja po polsku dla jednego projektu i osobnych tabel sklepów:
[docs/supabase-pl.md](docs/supabase-pl.md). Generator `gen-credentials` wyprowadza
adresy wszystkich sklepów z jednego `MIGRATION_DATABASE_URL` i obsługuje nazwy
użytkowników shared poolera. Użyj `--env-file .env` przed nazwą polecenia CLI,
aby konfiguracja i launcher korzystały z tego samego pliku.

The code never guesses a Supabase host; you paste the exact values from the dashboard. Nothing below was executed
against a real Supabase project in this repository (no credentials were available) - the same steps were verified on a
local PostgreSQL.

1. **Create a project** and copy the connection strings from *Project -> Connect*. For the owner/migration account use
   the **direct** connection or the **session pooler** (IPv4) string. Do not invent the pooler hostname; use the one
   shown. For poolers the user name may be `<role>.<project-ref>` - for the runtime users it is the same pattern with
   `shop_pl_rt` etc. Prefer the session pooler or direct connection for shops; the transaction pooler also works
   because prepared statements are disabled.
2. **TLS**: use `sslmode=verify-full` and the Supabase CA certificate (`&sslrootcert=/path/to/prod-ca-2021.crt`).
   Certificate verification is never switched off by the code; `APP_ENV=production` refuses weak `sslmode` values.
3. **Passwords + keys**: `python -m app.cli gen-credentials --db-host <host> --db-port <port> --db-name postgres --sslmode verify-full --migration-url "<owner URL>"`
   Alternatively, store the owner URL in `.env` and run `python -m app.cli --env-file .env gen-credentials`.
   The generator derives shop URLs from the owner URL, preserves its TLS parameters and adds the project suffix
   to runtime user names for a shared Supabase pooler. When an owner URL is present, it takes precedence over
   `--db-host`, `--db-port`, `--db-name` and `--sslmode`.
4. **Create schemas and runtime users** - either `python -m app.cli setup-db`, or the manual variant: run
   `python -m app.cli print-setup-sql`, replace the `<PASSWORD_FOR_SHOP_xx_RT>` placeholders with the values of
   `SHOP_PL_DB_PASSWORD` / `SHOP_DE_DB_PASSWORD` / `SHOP_RU_DB_PASSWORD` from `.env.local` and run it in the
   Supabase **SQL editor** as `postgres`.
5. **Migrate and seed**: `python -m app.cli migrate` then `python -m app.cli seed`.
6. **Keep the Data API closed**: in *Project settings -> API (Data API)* make sure `shop_pl`, `shop_de` and `shop_ru`
   are **not** in *Exposed schemas* (only `public` is exposed by default; these tables are not in `public`). The grants
   revoke everything from `PUBLIC`, `anon`, `authenticated`, `service_role` and `authenticator`, so the tables are not
   reachable through PostgREST even if a schema were exposed by mistake.
7. **Run the shops** with `DATABASE_URL_SHOP_*` (each process gets only its own URL) and the demo scripts.

Design decisions: separate schemas per shop (one project), one runtime user per shop restricted to its schema (it can
only change `products.stock_quantity`, never prices), no RLS because the Data API roles have no access at all and the
backend is the only client (RLS is not a substitute for the grants), and no `service_role` key anywhere in this code.

### Import only the product catalog

After migrations, import the synthetic catalog without creating customers, API keys or shipping methods:

```powershell
python scripts/import_products_supabase.py --env-file .env
python scripts/import_products_supabase.py --dry-run
```

The import adds or updates 24 products and six categories per shop. Use `--shop shop-pl` to select one shop.
It reads `MIGRATION_DATABASE_URL` from the selected file, preserves existing stock and orders, and does not delete
products outside the seed catalog. Price or origin changes can invalidate existing quotes. Use `bootstrap` or
`seed` when you need the full demo, including customers, API keys and shipping methods.

## Postman

Import [`docs/postman/shops.postman_collection.json`](docs/postman/shops.postman_collection.json) and the desired
`shop-*.postman_environment.json` from the same directory. Set `api_key` to that shop's `DEMO_API_KEY_SHOP_*_AGENT`
from your chosen env file, then run the numbered purchase requests in order. Response scripts save the cart,
quote and order identifiers in the active environment. Checkout reduces demo stock.

See [`docs/postman/README.md`](docs/postman/README.md) for the complete workflow.
Regenerate the collection after endpoint changes with `python scripts/generate_postman.py`.

## Tests

```powershell
python -m pytest -q
```

* They need a real PostgreSQL. If `TEST_DATABASE_ADMIN_URL` (a superuser/owner URL) is set it is used, otherwise a
  temporary server is started with `pgserver`. No mocks of the database.
* MCP tests start a real uvicorn server and talk to it with the official SDK client over HTTP.
* Covered: seed and idempotency, catalog/search/origin filters, versioned carts, quotes (TTL, stale), checkout
  (rollback, mock payment approve/decline, idempotency, last-unit concurrency with threads), auth and cross-customer
  isolation, schema isolation and grants, audit and redaction, REST/MCP parity, unavailable DB, migrations vs. models.

Regenerate the SKU documentation after changing the seed: `python scripts/generate_seed_docs.py` (a test fails when it is stale).

## Security notes

* Secrets (DB URLs, passwords, API keys) exist only in git-ignored env files / your secret store; `.env.example` has none.
* API keys are 256-bit random tokens stored only as SHA-256 hashes (the request token is hashed and looked up); logs redact Bearer tokens, `shk_` keys, DB URLs and
  address fields; error responses never contain stack traces, SQL or connection strings.
* The shop and the schema are fixed per process (`SHOP_ID`), never derived from a request.
* Mock payment outcome is server configuration (`MOCK_PAYMENT_MODE`), not an input of any endpoint or tool.
* Dev ports listen on `127.0.0.1`; put a TLS-terminating proxy in front for anything else and list its host in
  `MCP_ALLOWED_HOSTS`.
