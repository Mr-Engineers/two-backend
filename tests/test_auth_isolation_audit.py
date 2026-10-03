import json
import logging
import uuid

import psycopg
import pytest
from sqlalchemy.engine import make_url

from app.shops import ALL_SCHEMAS, SHOPS
from tests.helpers import ADDRESS, api_error, buy, create_cart, do_checkout, fill_cart, make_quote, new_key, set_item


# ------------------------------------------------------------------ authentication
def test_missing_or_invalid_api_key_gets_401(pl):
    for headers in ({}, {"Authorization": "Bearer shk_not-a-real-key-000000000000"}, {"Authorization": "Basic abc"},
                    {"Authorization": "Bearer "}):
        r = pl.client.get("/products", headers=headers)
        err = api_error(r, 401, "UNAUTHORIZED")
        assert r.headers["www-authenticate"] == "Bearer"
        assert "shk_" not in json.dumps(err)
    assert pl.client.get("/health/live").status_code == 200  # probes are public
    assert pl.client.get("/docs").status_code == 200


def test_api_key_of_another_shop_is_not_valid_here(shops):
    pl, de = shops["shop-pl"], shops["shop-de"]
    r = pl.client.get("/store", headers=de.headers())
    api_error(r, 401, "UNAUTHORIZED")


def test_keys_are_stored_hashed_only(pl):
    rows = pl.sql("SELECT key_hash FROM api_keys")
    assert rows and all(len(h) == 64 for (h,) in rows)
    assert not any(pl.keys["agent"] in str(r) for r in pl.sql("SELECT * FROM api_keys"))


def test_identity_cannot_be_supplied_by_the_client(pl):
    cart = create_cart(pl)
    assert cart["customer_id"] == str(pl.customer_id("agent"))
    r = pl.client.post("/carts", headers=pl.headers(), params={"customer_id": str(pl.customer_id("other"))})
    assert r.json()["customer_id"] == str(pl.customer_id("agent"))


# ------------------------------------------------------------------ ownership
def test_cart_quote_and_order_ownership_with_known_uuids(pl):
    cart, quote, result = buy(pl, {"PAP-A4-500": 1})
    order_id = result["order"]["order_id"]
    other = pl.headers("other")
    api_error(pl.client.get(f"/carts/{cart['id']}", headers=other), 404, "CART_NOT_FOUND")
    api_error(pl.client.put(f"/carts/{cart['id']}/items/{pl.sku('PEN-BALL-BLUE-10')}", json={"quantity": 1}, headers=other), 404, "CART_NOT_FOUND")
    api_error(pl.client.delete(f"/carts/{cart['id']}/items", headers=other), 404, "CART_NOT_FOUND")
    api_error(pl.client.post(f"/carts/{cart['id']}/quotes", json={"shipping_address": ADDRESS}, headers=other), 404, "CART_NOT_FOUND")
    api_error(pl.client.get(f"/quotes/{quote['quote_id']}", headers=other), 404, "QUOTE_NOT_FOUND")
    api_error(do_checkout(pl, quote["quote_id"], who="other"), 404, "QUOTE_NOT_FOUND")
    api_error(pl.client.get(f"/orders/{order_id}", headers=other), 404, "ORDER_NOT_FOUND")
    assert pl.client.get("/orders", headers=other).json()["total"] == 0
    assert pl.client.get("/orders", headers=pl.headers()).json()["total"] == 1


def test_other_customers_cannot_buy_anothers_pending_quote(pl):
    cart = fill_cart(pl, {"PAP-A4-500": 1})
    quote = make_quote(pl, cart["id"]).json()
    api_error(do_checkout(pl, quote["quote_id"], who="other"), 404, "QUOTE_NOT_FOUND")
    assert pl.sql("SELECT count(*) FROM orders")[0][0] == 0


# ------------------------------------------------------------------ schema isolation with real runtime users
def _connect(shop, url=None):
    u = make_url(url or shop.runtime_url).set(drivername="postgresql")
    return psycopg.connect(u.render_as_string(hide_password=False), autocommit=True)


def test_runtime_user_only_reaches_its_own_schema(shops):
    for sid, shop in shops.items():
        with _connect(shop) as conn:
            assert conn.execute("SELECT current_user").fetchone()[0] == SHOPS[sid].runtime_role
            own = SHOPS[sid].schema
            assert conn.execute(f'SELECT count(*) FROM "{own}".products').fetchone()[0] >= 20
            for other in ALL_SCHEMAS:
                if other == own:
                    continue
                with pytest.raises(psycopg.errors.InsufficientPrivilege):
                    conn.execute(f'SELECT count(*) FROM "{other}".products')
                with pytest.raises(psycopg.errors.InsufficientPrivilege):
                    conn.execute(f'INSERT INTO "{other}".categories (id, slug, name, sort_order) VALUES (gen_random_uuid(), \'x\', \'x\', 0)')


def test_runtime_user_has_least_privilege_inside_its_schema(pl):
    with _connect(pl) as conn:
        for stmt in (
            'UPDATE shop_pl.products SET unit_gross_minor = 1',
            "UPDATE shop_pl.products SET country_of_origin = 'RU'",
            'DELETE FROM shop_pl.orders',
            'UPDATE shop_pl.orders SET total_gross_minor = 0',
            'DELETE FROM shop_pl.audit_events',
            'INSERT INTO shop_pl.api_keys (id, customer_id, key_hash, label, active) VALUES (gen_random_uuid(), gen_random_uuid(), repeat(\'a\',64), \'x\', true)',
            'CREATE TABLE shop_pl.evil (id int)',
            'CREATE TABLE public.evil (id int)',
            'TRUNCATE shop_pl.products CASCADE',
            'ALTER TABLE shop_pl.products DISABLE TRIGGER ALL',
        ):
            with pytest.raises((psycopg.errors.InsufficientPrivilege, psycopg.errors.ForeignKeyViolation)):
                conn.execute(stmt)
        conn.execute("UPDATE shop_pl.products SET stock_quantity = stock_quantity WHERE false")  # stock is writable


def test_data_api_roles_and_public_have_no_access(pl, admin_url):
    # simulate a Supabase default-privilege leak, then re-run the grant step that must close it
    pl.sql("GRANT USAGE ON SCHEMA shop_pl TO anon, authenticated, service_role, PUBLIC")
    pl.sql("GRANT SELECT ON ALL TABLES IN SCHEMA shop_pl TO anon, authenticated, service_role, PUBLIC")
    from app import dbadmin

    dbadmin.apply_grants(admin_url)
    for role in ("anon", "authenticated", "service_role"):
        rows = pl.sql(
            "SELECT has_schema_privilege(:r, 'shop_pl', 'USAGE'), has_table_privilege(:r, 'shop_pl.products', 'SELECT'), "
            "has_table_privilege(:r, 'shop_pl.api_keys', 'SELECT')", r=role)
        assert rows == [(False, False, False)], role
    assert pl.sql("SELECT has_schema_privilege('public', 'shop_pl', 'USAGE')") == [(False,)]
    assert pl.sql("SELECT has_table_privilege('public', 'shop_pl.products', 'SELECT')") == [(False,)]


def test_triggers_and_constraints_protect_data_even_for_owner(pl):
    import sqlalchemy.exc

    with pytest.raises(sqlalchemy.exc.IntegrityError):
        pl.sql("UPDATE shipping_methods SET currency = 'EUR'")  # currency must match store_config
    with pytest.raises(sqlalchemy.exc.IntegrityError):
        pl.sql("UPDATE store_config SET id = 2")


# ------------------------------------------------------------------ audit + correlation + redaction
def test_audit_events_keep_correlation_id_and_no_address_data(pl, caplog):
    caplog.set_level(logging.INFO)
    corr = "gateway-trace-" + uuid.uuid4().hex[:8]
    h = pl.headers(**{"X-Correlation-ID": corr})
    cart = pl.client.post("/carts", headers=h)
    assert cart.headers["x-correlation-id"] == corr and cart.headers["x-request-id"] != corr
    cart_id = cart.json()["id"]
    pl.client.put(f"/carts/{cart_id}/items/{pl.sku('PAP-A4-500')}", json={"quantity": 1}, headers=h)
    quote = pl.client.post(f"/carts/{cart_id}/quotes", json={"shipping_address": ADDRESS}, headers=h).json()
    pl.client.post("/checkout", json={"quote_id": quote["quote_id"], "idempotency_key": new_key()}, headers=h)
    pl.client.put(f"/carts/{cart_id}/items/{pl.sku('PEN-BALL-BLUE-10')}", json={"quantity": 1}, headers=h)  # error: checked out

    rows = pl.sql("SELECT operation, result, error_code, correlation_id, request_id, customer_id, store_id, cart_id, quote_id, order_id, details::text FROM audit_events ORDER BY id")
    ops = [(r[0], r[1], r[2]) for r in rows]
    assert ops == [
        ("cart.create", "success", None), ("cart.set_item", "success", None), ("quote.create", "success", None),
        ("checkout", "success", None), ("cart.set_item", "error", "CART_ALREADY_CHECKED_OUT"),
    ]
    assert all(r[3] == corr and r[4] and r[5] == pl.customer_id() and r[6] == "shop-pl" for r in rows)
    checkout_row = rows[3]
    assert checkout_row[7] and checkout_row[8] and checkout_row[9]  # cart / quote / order references
    dump = json.dumps([list(map(str, r)) for r in rows])
    for secret in (ADDRESS["line1"], ADDRESS["recipient_name"], ADDRESS["email"], ADDRESS["phone"], pl.keys["agent"]):
        assert secret not in dump

    logged = " ".join(json.dumps({"m": r.getMessage(), **{k: str(v) for k, v in r.__dict__.items()}}) for r in caplog.records)
    for secret in (ADDRESS["line1"], ADDRESS["recipient_name"], ADDRESS["email"], pl.keys["agent"]):
        assert secret not in logged


def test_error_format_has_code_message_request_id_details(pl):
    r = pl.client.get("/products/NOPE", headers=pl.headers(**{"X-Correlation-ID": "abc-123"}))
    err = api_error(r, 404, "PRODUCT_NOT_FOUND")
    assert err["correlation_id"] == "abc-123" and err["request_id"] == r.headers["x-request-id"]
    assert pl.client.get("/no/such/route", headers=pl.headers()).json()["error"]["code"] == "NOT_FOUND"
    # malformed correlation ids are replaced, never reflected
    r = pl.client.get("/store", headers=pl.headers(**{"X-Correlation-ID": "bad id\twith spaces"}))
    assert r.headers["x-correlation-id"] == r.headers["x-request-id"]
