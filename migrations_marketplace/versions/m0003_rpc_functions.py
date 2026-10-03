"""server-side functions for the Supabase REST (PostgREST) mode

With ``SUPABASE_URL`` + ``SUPABASE_KEY`` the marketplace talks to Supabase over HTTP instead of a SQL connection.
HTTP has no multi-statement transactions, so the two operations that need one live in the database:

* ``shops.create_order(...)``  - idempotent order (advisory lock, idempotency record, price/stock checks, optional
  stock decrement). Raises ``offer_not_found`` / ``price_changed`` / ``insufficient_quantity`` /
  ``idempotency_conflict`` (SQLSTATE P0001, the code is the exception message).
* ``shops.load_scenario(...)`` - atomic replacement of merchants and offers (demo loader).

Both are SECURITY INVOKER and executable only by ``service_role`` (the role behind ``SUPABASE_KEY``); PUBLIC, anon
and authenticated cannot call them. Also grants ``service_role`` USAGE on the two schemas (a no-op when the role does
not exist, e.g. on a plain PostgreSQL).

Revision ID: m0003
Revises: m0002
Create Date: 2026-10-03 22:40:00.000000
"""
from alembic import op

from app.marketplace import MARKETPLACE_SCHEMA, SUPPLIERS_SCHEMA
from app.marketplace.models import MERCHANT_TABLES, OFFER_COLUMNS

revision = "m0003"
down_revision = "m0002"
branch_labels = None
depends_on = None

S, W = MARKETPLACE_SCHEMA, SUPPLIERS_SCHEMA
CREATE_ORDER_SIG = f"{S}.create_order(text, text, text, integer, bigint, text, boolean, text, text)"
LOAD_SCENARIO_SIG = f"{S}.load_scenario(text, jsonb, jsonb)"


def _array(values) -> str:
    return "ARRAY[" + ", ".join("'" + v.replace("'", "''") + "'" for v in values) + "]::text[]"


CREATE_ORDER = """
CREATE OR REPLACE FUNCTION @S@.create_order(
    p_idempotency_key text, p_fingerprint text, p_offer_id text, p_quantity integer,
    p_expected_minor bigint, p_expected_currency text, p_decrement_stock boolean DEFAULT false,
    p_request_id text DEFAULT NULL, p_on_behalf_of text DEFAULT NULL
) RETURNS jsonb
LANGUAGE plpgsql
SET search_path = pg_catalog, pg_temp
AS $fn$
DECLARE
    v_record @S@.idempotency_records%ROWTYPE;
    v_order @S@.orders%ROWTYPE;
    v_offer record;
    v_minor bigint;
    v_table text;
    v_rows integer;
BEGIN
    -- serialise requests with the same key so a retry racing the original cannot create a second order
    PERFORM pg_advisory_xact_lock(hashtextextended(p_idempotency_key, 0));
    SELECT * INTO v_record FROM @S@.idempotency_records WHERE idempotency_key = p_idempotency_key;
    IF FOUND THEN
        IF v_record.request_fingerprint <> p_fingerprint THEN
            RAISE EXCEPTION 'idempotency_conflict' USING ERRCODE = 'P0001';
        END IF;
        SELECT * INTO v_order FROM @S@.orders WHERE id = v_record.order_id;
        RETURN to_jsonb(v_order);
    END IF;

    SELECT * INTO v_offer FROM @S@.offers WHERE offer_id = p_offer_id AND active;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'offer_not_found' USING ERRCODE = 'P0001';
    END IF;
    v_minor := round(v_offer.unit_price * 100)::bigint;
    IF btrim(v_offer.currency) <> p_expected_currency OR v_minor <> p_expected_minor THEN
        RAISE EXCEPTION 'price_changed' USING ERRCODE = 'P0001';
    END IF;
    IF p_quantity > v_offer.available_qty THEN
        RAISE EXCEPTION 'insufficient_quantity' USING ERRCODE = 'P0001';
    END IF;

    IF p_decrement_stock THEN
        SELECT offers_table INTO v_table FROM @W@.suppliers WHERE merchant_id = v_offer.merchant_id;
        IF v_table IS NULL OR NOT (v_table = ANY (@TABLES@)) THEN
            RAISE EXCEPTION 'offer_not_found' USING ERRCODE = 'P0001';
        END IF;
        EXECUTE format(
            'UPDATE @S@.%I SET available_qty = available_qty - $1 WHERE offer_id = $2 AND available_qty >= $1', v_table
        ) USING p_quantity, v_offer.offer_id;
        GET DIAGNOSTICS v_rows = ROW_COUNT;
        IF v_rows = 0 THEN  -- a concurrent order took the stock
            RAISE EXCEPTION 'insufficient_quantity' USING ERRCODE = 'P0001';
        END IF;
    END IF;

    INSERT INTO @S@.orders (
        id, status, offer_id, merchant_id, sku, quantity, unit_price_minor, currency, total_minor,
        created_at, request_id, on_behalf_of
    ) VALUES (
        'ord_' || substr(md5(gen_random_uuid()::text), 1, 12), 'confirmed', v_offer.offer_id, v_offer.merchant_id,
        v_offer.sku, p_quantity, v_minor, btrim(v_offer.currency), v_minor * p_quantity,
        date_trunc('second', now()), p_request_id, p_on_behalf_of
    ) RETURNING * INTO v_order;
    INSERT INTO @S@.idempotency_records (idempotency_key, request_fingerprint, order_id)
    VALUES (p_idempotency_key, p_fingerprint, v_order.id);
    RETURN to_jsonb(v_order);
END
$fn$
"""

OFFER_RECORD = (
    "offer_id text, merchant_id text, sku text, product_name text, unit_price numeric, currency text, "
    "available_qty integer, ships_from text, delivery_days integer, description text, scenario_id text, "
    "active boolean, offers_table text"
)

LOAD_SCENARIO = """
CREATE OR REPLACE FUNCTION @S@.load_scenario(p_scenario_id text, p_merchants jsonb, p_offers jsonb)
RETURNS jsonb
LANGUAGE plpgsql
SET search_path = pg_catalog, pg_temp
AS $fn$
DECLARE
    v_tables constant text[] := @TABLES@;
    v_merchants constant text[] := @MERCHANTS@;
    v_table text;
    v_merchant text;
BEGIN
    PERFORM pg_advisory_xact_lock(7100001);  -- same key as the SQL loader: loads never interleave
    FOREACH v_table IN ARRAY v_tables LOOP
        EXECUTE format('DELETE FROM @S@.%I', v_table);
    END LOOP;
    FOREACH v_merchant IN ARRAY v_merchants LOOP
        IF NOT EXISTS (SELECT 1 FROM jsonb_array_elements(p_merchants) e WHERE e->>'merchant_id' = v_merchant) THEN
            BEGIN
                DELETE FROM @W@.suppliers WHERE merchant_id = v_merchant;
            EXCEPTION WHEN foreign_key_violation THEN
                NULL;  -- referenced by another team's table (e.g. purchase orders): keep the profile
            END;
        END IF;
    END LOOP;
    INSERT INTO @W@.suppliers (
        merchant_id, name, domain, country, domain_registered_at, verified, reputation_score, reviews_count,
        offers_table, scenario_id
    )
    SELECT x.merchant_id, x.name, x.domain, x.country, x.domain_registered_at, x.verified, x.reputation_score,
           x.reviews_count, x.offers_table, x.scenario_id
    FROM jsonb_to_recordset(p_merchants) AS x(
        merchant_id text, name text, domain text, country text, domain_registered_at date, verified boolean,
        reputation_score numeric, reviews_count integer, offers_table text, scenario_id text
    )
    ON CONFLICT (merchant_id) DO UPDATE SET
        name = EXCLUDED.name, domain = EXCLUDED.domain, country = EXCLUDED.country,
        domain_registered_at = EXCLUDED.domain_registered_at, verified = EXCLUDED.verified,
        reputation_score = EXCLUDED.reputation_score, reviews_count = EXCLUDED.reviews_count,
        offers_table = EXCLUDED.offers_table, scenario_id = EXCLUDED.scenario_id;
    FOREACH v_table IN ARRAY v_tables LOOP
        EXECUTE format(
            'INSERT INTO @S@.%I (@COLUMNS@) SELECT @COLUMNS@ FROM jsonb_to_recordset($1) AS x(@RECORD@) '
            'WHERE x.offers_table = $2', v_table
        ) USING p_offers, v_table;
    END LOOP;
    RETURN jsonb_build_object(
        'scenario_id', p_scenario_id,
        'merchants_loaded', jsonb_array_length(p_merchants),
        'offers_loaded', jsonb_array_length(p_offers)
    );
END
$fn$
"""


def _render(sql: str) -> str:
    return (
        sql.replace("@S@", S)
        .replace("@W@", W)
        .replace("@TABLES@", _array(MERCHANT_TABLES.values()))
        .replace("@MERCHANTS@", _array(MERCHANT_TABLES))
        .replace("@COLUMNS@", ", ".join(OFFER_COLUMNS))
        .replace("@RECORD@", OFFER_RECORD)
    )


def upgrade() -> None:
    op.execute(_render(CREATE_ORDER))
    op.execute(_render(LOAD_SCENARIO))
    for signature in (CREATE_ORDER_SIG, LOAD_SCENARIO_SIG):
        op.execute(f"REVOKE ALL ON FUNCTION {signature} FROM PUBLIC")
    op.execute(
        f"""
        DO $do$
        BEGIN
            IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'service_role') THEN
                GRANT USAGE ON SCHEMA {S}, {W} TO service_role;
                GRANT EXECUTE ON FUNCTION {CREATE_ORDER_SIG} TO service_role;
                GRANT EXECUTE ON FUNCTION {LOAD_SCENARIO_SIG} TO service_role;
            END IF;
            IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'anon') THEN
                REVOKE ALL ON FUNCTION {CREATE_ORDER_SIG} FROM anon;
                REVOKE ALL ON FUNCTION {LOAD_SCENARIO_SIG} FROM anon;
            END IF;
            IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'authenticated') THEN
                REVOKE ALL ON FUNCTION {CREATE_ORDER_SIG} FROM authenticated;
                REVOKE ALL ON FUNCTION {LOAD_SCENARIO_SIG} FROM authenticated;
            END IF;
        END
        $do$
        """
    )


def downgrade() -> None:
    op.execute(f"DROP FUNCTION IF EXISTS {LOAD_SCENARIO_SIG}")
    op.execute(f"DROP FUNCTION IF EXISTS {CREATE_ORDER_SIG}")
