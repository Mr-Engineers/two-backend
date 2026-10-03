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
