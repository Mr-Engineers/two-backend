"""marketplace on the shared ``shops`` schema

Idempotent on purpose: the ``shops`` schema, ``warehouse.suppliers`` and the first five merchant offers tables
(biuromax, ofistorg, cheapdeals, officehub, printworks) already exist in the shared Supabase project and are owned
by the shops/warehouse teams, so everything below is ``IF NOT EXISTS``. On a fresh database the same layout is created.

* creates the missing merchant offers tables (papiernik, promocje, papierhurt, tonerfix + the three demo shops),
* replaces the ``shops.offers`` view with the UNION ALL over all merchant tables (same columns as before),
* creates ``shops.orders`` and ``shops.idempotency_records``,
* adds row level security policies for the marketplace runtime role (the tables have RLS enabled, no policies).

Revision ID: m0001
Revises:
Create Date: 2026-10-03 20:30:00.000000
"""
from alembic import op
import sqlalchemy as sa

from app.marketplace import MARKETPLACE_ROLE, MARKETPLACE_SCHEMA, SUPPLIERS_SCHEMA
from app.marketplace.models import MERCHANT_TABLES, OFFER_COLUMNS

revision = "m0001"
down_revision = None
branch_labels = None
depends_on = None

S, W, R = MARKETPLACE_SCHEMA, SUPPLIERS_SCHEMA, MARKETPLACE_ROLE


def _create_suppliers() -> str:
    return f"""
    CREATE TABLE IF NOT EXISTS {W}.suppliers (
        merchant_id text PRIMARY KEY,
        name text NOT NULL,
        domain text NOT NULL UNIQUE,
        country char(2) NOT NULL CHECK (country ~ '^[A-Z]{{2}}$'),
        domain_registered_at date NOT NULL,
        verified boolean NOT NULL DEFAULT false,
        reputation_score numeric CHECK (reputation_score >= 0 AND reputation_score <= 1),
        reviews_count integer NOT NULL DEFAULT 0 CHECK (reviews_count >= 0),
        offers_table text NOT NULL UNIQUE,
        scenario_id text,
        created_at timestamptz NOT NULL DEFAULT now(),
        CHECK ((reputation_score IS NULL) = (reviews_count = 0))
    )"""


def _create_offers_table(table: str, merchant_id: str) -> str:
    return f"""
    CREATE TABLE IF NOT EXISTS {S}.{table} (
        offer_id text PRIMARY KEY,
        merchant_id text NOT NULL DEFAULT '{merchant_id}' CHECK (merchant_id = '{merchant_id}')
            REFERENCES {W}.suppliers (merchant_id),
        sku text NOT NULL,
        product_name text NOT NULL,
        unit_price numeric(12, 2) NOT NULL CHECK (unit_price >= 0),
        currency char(3) NOT NULL DEFAULT 'PLN' CHECK (currency ~ '^[A-Z]{{3}}$'),
        available_qty integer NOT NULL CHECK (available_qty >= 0),
        ships_from char(2) NOT NULL CHECK (ships_from ~ '^[A-Z]{{2}}$'),
        delivery_days integer NOT NULL CHECK (delivery_days >= 0),
        description text NOT NULL DEFAULT '',
        scenario_id text,
        active boolean NOT NULL DEFAULT true,
        updated_at timestamptz NOT NULL DEFAULT now()
    )"""


def _policy(schema: str, table: str) -> list[str]:
    return [
        f"ALTER TABLE {schema}.{table} ENABLE ROW LEVEL SECURITY",
        f"DROP POLICY IF EXISTS marketplace_rt_all ON {schema}.{table}",
        f"CREATE POLICY marketplace_rt_all ON {schema}.{table} FOR ALL TO \"{R}\" USING (true) WITH CHECK (true)",
    ]


def upgrade() -> None:
    op.execute(f"CREATE SCHEMA IF NOT EXISTS {W}")
    op.execute(_create_suppliers())
    for merchant_id, table in MERCHANT_TABLES.items():
        op.execute(_create_offers_table(table, merchant_id))

    columns = ", ".join(OFFER_COLUMNS)
    union = "\nUNION ALL\n".join(f"SELECT {columns} FROM {S}.{t}" for t in MERCHANT_TABLES.values())
    op.execute(f"CREATE OR REPLACE VIEW {S}.offers AS\n{union}")

    for table in MERCHANT_TABLES.values():
        for statement in _policy(S, table):
            op.execute(statement)
    for statement in _policy(W, "suppliers"):
        op.execute(statement)

    op.create_table(
        "orders",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("offer_id", sa.String(length=64), nullable=False),
        sa.Column("merchant_id", sa.String(length=64), nullable=False),
        sa.Column("sku", sa.String(length=64), nullable=False),
        sa.Column("quantity", sa.Integer(), nullable=False),
        sa.Column("unit_price_minor", sa.BigInteger(), nullable=False),
        sa.Column("currency", sa.String(length=3), nullable=False),
        sa.Column("total_minor", sa.BigInteger(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("request_id", sa.String(length=128), nullable=True),
        sa.Column("on_behalf_of", sa.String(length=128), nullable=True),
        sa.CheckConstraint("status IN ('confirmed')", name=op.f("ck_orders_status_valid")),
        sa.CheckConstraint("quantity > 0", name=op.f("ck_orders_quantity_positive")),
        sa.CheckConstraint("unit_price_minor >= 0", name=op.f("ck_orders_price_non_negative")),
        sa.CheckConstraint("total_minor = unit_price_minor * quantity", name=op.f("ck_orders_total_consistent")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_orders")),
    )
    op.create_index("ix_orders_created_at", "orders", ["created_at"], unique=False)
    op.create_table(
        "idempotency_records",
        sa.Column("idempotency_key", sa.String(length=128), nullable=False),
        sa.Column("request_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("order_id", sa.String(length=32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint(
            "char_length(idempotency_key) BETWEEN 8 AND 128", name=op.f("ck_idempotency_records_key_length")
        ),
        sa.CheckConstraint(
            "request_fingerprint ~ '^[0-9a-f]{64}$'", name=op.f("ck_idempotency_records_fingerprint_sha256_hex")
        ),
        sa.ForeignKeyConstraint(["order_id"], ["orders.id"], name=op.f("fk_idempotency_records_order_id_orders")),
        sa.PrimaryKeyConstraint("idempotency_key", name=op.f("pk_idempotency_records")),
    )


def downgrade() -> None:
    """Only removes what this service owns. The shops/warehouse tables and the offers view stay."""
    op.drop_table("idempotency_records")
    op.drop_index("ix_orders_created_at", table_name="orders")
    op.drop_table("orders")
