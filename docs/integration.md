# Integration guide

Three independent shop backends share one code base. Each is its own process/container with its own database
schema, REST API and MCP endpoint.

| Shop | `store_id` | Name | Store country | Currency | Locale | Dev URL | MCP URL | Schema | Runtime DB user |
| --- | --- | --- | :---: | :---: | --- | --- | --- | --- | --- |
| PL | `shop-pl` | Papiernia | PL | PLN | pl-PL | `http://127.0.0.1:8001` | `http://127.0.0.1:8001/mcp` | `shop_pl` | `shop_pl_rt` |
| DE | `shop-de` | BüroWerk | DE | EUR | de-DE | `http://127.0.0.1:8002` | `http://127.0.0.1:8002/mcp` | `shop_de` | `shop_de_rt` |
| RU | `shop-ru` | OfficeMarket | RU | RUB | en | `http://127.0.0.1:8003` | `http://127.0.0.1:8003/mcp` | `shop_ru` | `shop_ru_rt` |

A process serves exactly one shop (`SHOP_ID`, fixed at start-up). Nothing in a request can select another shop or
schema. All three shops behave the same way - the RU shop is an ordinary shop, it has no special flags or rules.

## What this backend deliberately does not do

* It does **not** block, warn about or refuse anything because of `country_of_origin`. Origin is data. Whatever
  buys or does not buy a product based on origin is the integrator's decision.
* It does not convert currencies. Every amount is in the shop's own currency.
* It has no gateway, agent, company policy or operator UI.

## Authentication

Every endpoint except `/health/*` needs `Authorization: Bearer <API key>` (REST and `/mcp`).

* A key looks like `shk_<random>`. The database stores only its SHA-256 hash.
* A key belongs to one customer of one shop. Carts, quotes and orders are private to that customer: another customer
  of the same shop receives `CART_NOT_FOUND` / `QUOTE_NOT_FOUND` / `ORDER_NOT_FOUND`.
* A key of shop A is useless in shop B (separate schemas): `401 UNAUTHORIZED`.
* Generate keys outside the code: `python -m app.cli gen-credentials` writes local demo keys to `.env.local`
  (git-ignored). For a real deployment create keys with `app.security.generate_api_key()` and insert the hash.

Optional headers: `X-Request-ID` (echoed back) and `X-Correlation-ID` (kept in the audit trail so one integrator
action can be followed across calls; a malformed value is replaced by a generated one).

## Money

* Integer **minor units** next to a currency: `unit_gross_minor`, `line_total_gross_minor`, `subtotal_gross_minor`,
  `shipping_gross_minor`, `total_gross_minor` + `currency`. `*_decimal` strings are for display only.
* Prices are **gross**. `line_total = unit * quantity`, `total = subtotal + shipping`.
* Quantity is a number of **packs** (`units_per_pack` x `unit_label` per pack). Compare shops with
  `canonical_item_code` and pack size, not with the SKU or the name.

## `country_of_origin`

* Every product has `country_of_origin` - ISO 3166-1 alpha-2, upper case (`PL`, `DE`, `CZ`, `RU`, ...).
* It is a structured field in product lists/details, cart lines, quote lines and order lines (the order keeps an
  immutable snapshot) - never only text in a description.
* Carts, quotes and orders also carry `origin_countries`: the **sorted, unique** origins of all lines (e.g.
  `["PL","RU"]`). Read it from the quote before calling `checkout`.
* `store_country` (where the shop is established) is a different thing. A Polish shop can sell a product of
  origin `RU`, a Russian shop can sell a product of origin `DE`.
* `GET /products?origin=PL&origin=CZ` filters by origin (MCP: `search_products.origin_countries`).

## Expected flow

```
search_products / get_product     -> choose SKUs (read country_of_origin)
create_cart
set_cart_item (absolute quantity, expected_version)   (repeat per line)
list_shipping_methods
create_checkout_quote             -> immutable snapshot, read origin_countries and total_gross_minor, TTL 5 min
checkout (quote_id, idempotency_key)  -> the purchase, mock payment
get_order / list_orders
```

* **Carts** are versioned. `set_cart_item` takes the *absolute* quantity (`0` is invalid - use `remove_cart_item`),
  so retrying is safe. Pass `expected_version` to detect concurrent changes (`CART_VERSION_CONFLICT`). A cart
  does not reserve stock.
* **Quotes** are immutable snapshots with a TTL (`quote_ttl_seconds`, default 300). A quote does not reserve stock and
  charges nothing. Creating a quote never changes the cart.
* **Checkout** takes only `quote_id` and `idempotency_key`; prices, quantities and payment result cannot be sent.
  It runs in one PostgreSQL transaction: re-validate the quote, lock the products in a fixed order, decrement stock
  with a conditional update, create the order (with immutable item snapshots), record the mock payment, mark the cart
  checked out. Any failure rolls everything back. Two buyers racing for the last unit: exactly one wins, the other
  gets `INSUFFICIENT_STOCK`.
* **Idempotency**: repeating `checkout` with the same `quote_id` and `idempotency_key` returns the *same* order with
  `idempotent_replay: true` (even after the quote expired) and never charges or decrements stock twice. The same key
  with a different quote gives `IDEMPOTENCY_CONFLICT`.
* **Mock payment**: no real money. The outcome is a server-side setting (`MOCK_PAYMENT_MODE=approve|decline`), never
  an input. A declined payment returns `PAYMENT_DECLINED_MOCK` and leaves stock untouched.
* **Stale quote** detection: the quote is rejected with `QUOTE_STALE` when the cart changed, or a product's price,
  origin, active flag or the shipping price changed after the quote was created. Stock changes alone do not stale a
  quote, they surface as `INSUFFICIENT_STOCK` at checkout.

## REST endpoints

| Method and path | Purpose | Class |
| --- | --- | --- |
| `GET /store` | name, store country, currency, locale, TTL, limits | catalog_read |
| `GET /categories` | categories with active product counts | catalog_read |
| `GET /products` | search/list: `q`, `category`, `availability` (`in_stock`/`out_of_stock`), `origin` (repeatable), `sort` (`name`,`sku`,`price_asc`,`price_desc`), `limit` (1-100), `offset` | catalog_read |
| `GET /products/{sku}` | product details | catalog_read |
| `GET /shipping-methods` | shipping methods and gross prices | catalog_read |
| `POST /carts` | create an empty cart (201) | cart_write |
| `GET /carts/{cart_id}` | read the cart | cart_read |
| `PUT /carts/{cart_id}/items/{sku}` | body `{"quantity": n, "expected_version": v?}` (absolute quantity) | cart_write |
| `DELETE /carts/{cart_id}/items/{sku}?expected_version=` | remove a line | cart_write |
| `DELETE /carts/{cart_id}/items?expected_version=` | clear the cart | cart_write |
| `POST /carts/{cart_id}/quotes` | create a quote: `shipping_address`, `shipping_method_code?`, `expected_cart_version?` (201) | quote_write |
| `GET /quotes/{quote_id}` | read a quote | quote_read |
| `POST /checkout` | body `{"quote_id", "idempotency_key"}` - **the purchase** | purchase / payment_mock |
| `GET /orders?limit=&offset=` | own orders, newest first | order_read |
| `GET /orders/{order_id}` | one order | order_read |
| `GET /health/live`, `GET /health/ready` | probes (no auth); `ready` checks DB connectivity, the migration revision and that the store is configured | - |

OpenAPI: `GET /docs`, `GET /openapi.json`.

Shipping address: `recipient_name`, `line1`, `line2?`, `postal_code`, `city`, `country` (ISO alpha-2, where the
package goes - not the product origin), `phone?`, `email?`. Addresses are stored with quotes/orders but never written to
logs.

## MCP

* Official Python SDK, **Streamable HTTP**, stateless JSON responses, endpoint `POST /mcp` on every shop.
* Same `Authorization: Bearer` header. Missing or wrong key -> HTTP `401` (no MCP session is created).
* DNS-rebinding protection is on: `Host` must be `127.0.0.1`, `localhost`, `[::1]` or listed in `MCP_ALLOWED_HOSTS`.
* Tools have typed input schemas and structured output schemas, MCP annotations (`readOnlyHint`, `destructiveHint`,
  `idempotentHint`) and a class label in `_meta["io.shop/tool_class"]`.
* Tool failures are MCP error results (`isError: true`). The text is `Error executing tool <name>: {json}` where
  `{json}` is `{"error": {"code", "message", "request_id", "correlation_id", "details"}}` - parse from the first `{`.
* Tools call the same service layer as REST, so results are identical (a test compares them).

| Tool | Class | Annotations | Arguments |
| --- | --- | --- | --- |
| `get_store_info` | catalog_read | read-only | - |
| `list_categories` | catalog_read | read-only | - |
| `search_products` | catalog_read | read-only | `q?`, `category?`, `availability?`, `origin_countries?`, `sort?`, `limit?`, `offset?` |
| `get_product` | catalog_read | read-only | `sku` |
| `list_shipping_methods` | catalog_read | read-only | - |
| `create_cart` | cart_write | additive write | - |
| `get_cart` | cart_read | read-only | `cart_id` |
| `set_cart_item` | cart_write | idempotent write | `cart_id`, `sku`, `quantity`, `expected_version?` |
| `remove_cart_item` | cart_write | destructive, idempotent | `cart_id`, `sku`, `expected_version?` |
| `clear_cart` | cart_write | destructive, idempotent | `cart_id`, `expected_version?` |
| `create_checkout_quote` | quote_write | additive write | `cart_id`, `shipping_address`, `shipping_method_code?`, `expected_cart_version?` |
| `get_checkout_quote` | quote_read | read-only | `quote_id` |
| `checkout` | purchase/payment_mock | destructive, **not** read-only | `quote_id`, `idempotency_key` |
| `list_orders` | order_read | read-only | `limit?`, `offset?` |
| `get_order` | order_read | read-only | `order_id` |

`checkout` is the only tool that buys. There is deliberately no argument for price, total, currency, payment result
or customer id.

Working client: `python scripts/mcp_client.py [--buy]`.

## Errors

REST body (HTTP status in the table): `{"error": {"code", "message", "request_id", "correlation_id", "details"}}`.
Responses never contain stack traces, SQL, connection strings or keys.

| Code | HTTP | Meaning |
| --- | :---: | --- |
| `UNAUTHORIZED` | 401 | missing/invalid API key |
| `VALIDATION_ERROR` | 422 | malformed input (unknown fields are rejected too) |
| `PRODUCT_NOT_FOUND`, `CART_NOT_FOUND`, `QUOTE_NOT_FOUND`, `ORDER_NOT_FOUND`, `SHIPPING_METHOD_NOT_FOUND` | 404 | also returned for objects of another customer |
| `INVALID_QUANTITY` | 422 | quantity must be 1..`max_line_quantity` |
| `CART_LINE_LIMIT` | 422 | more than `max_cart_lines` lines |
| `CART_VERSION_CONFLICT` | 409 | `expected_version` differs from the cart version (details contain the current one) |
| `CART_ALREADY_CHECKED_OUT` | 409 | the cart was already bought |
| `EMPTY_CART` | 409 | quote requested for an empty cart |
| `PRODUCT_INACTIVE` | 409 | product discontinued |
| `INSUFFICIENT_STOCK` | 409 | not enough packs (details: SKU, requested, available). Returned by `set_cart_item`, quote creation and checkout |
| `QUOTE_STALE` | 409 | cart/price/origin/shipping changed since the quote |
| `QUOTE_EXPIRED` | 409 | the quote is older than its TTL - create a new one |
| `IDEMPOTENCY_CONFLICT` | 409 | the key was used with a different quote |
| `PAYMENT_DECLINED_MOCK` | 402 | mock payment declined, nothing was changed |
| `DB_UNAVAILABLE` | 503 | database unreachable |
| `NOT_READY` | 503 | readiness probe failed |
| `INTERNAL_ERROR` | 500 | unexpected failure (details only in the server log) |

## Observability

* Structured JSON logs with `request_id` and `correlation_id`; Bearer tokens, API keys, DB URLs and address fields
  are redacted.
* Audit events (table `audit_events`) for cart changes, quotes, checkout success and every checkout failure, with
  the correlation id, customer, outcome and error code (no addresses).
* `/health/live` is a pure liveness check; `/health/ready` runs real queries (DB reachable, migration revision, store configured) and returns `503 NOT_READY` / `DB_UNAVAILABLE` otherwise.
