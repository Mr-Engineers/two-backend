"""initial marketplace schema

Revision ID: m0001
Revises:
Create Date: 2026-10-03 17:50:00.000000
"""
from alembic import op
import sqlalchemy as sa

revision = "m0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "merchants",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("domain", sa.String(length=255), nullable=False),
        sa.Column("country", sa.String(length=2), nullable=False),
        sa.Column("domain_registered_at", sa.Date(), nullable=False),
        sa.Column("verified", sa.Boolean(), nullable=False),
        sa.Column("reputation_score", sa.Numeric(precision=4, scale=3), nullable=True),
        sa.Column("reputation_reviews_count", sa.Integer(), nullable=True),
        sa.CheckConstraint("country ~ '^[A-Z]{2}$'", name=op.f("ck_merchants_country_format")),
        sa.CheckConstraint(
            "(reputation_score IS NULL) = (reputation_reviews_count IS NULL)",
            name=op.f("ck_merchants_reputation_complete"),
        ),
        sa.CheckConstraint(
            "reputation_score IS NULL OR reputation_score BETWEEN 0 AND 1",
            name=op.f("ck_merchants_reputation_range"),
        ),
        sa.CheckConstraint(
            "reputation_reviews_count IS NULL OR reputation_reviews_count >= 0",
            name=op.f("ck_merchants_reviews_non_negative"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_merchants")),
    )
    op.create_table(
        "offers",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("merchant_id", sa.String(length=64), nullable=False),
        sa.Column("sku", sa.String(length=64), nullable=False),
        sa.Column("product_name", sa.String(length=200), nullable=False),
        sa.Column("unit_price_minor", sa.BigInteger(), nullable=False),
        sa.Column("currency", sa.String(length=3), nullable=False),
        sa.Column("available_qty", sa.Integer(), nullable=False),
        sa.Column("ships_from", sa.String(length=2), nullable=False),
        sa.Column("delivery_days", sa.Integer(), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.CheckConstraint("unit_price_minor BETWEEN 0 AND 10000000000", name=op.f("ck_offers_price_range")),
        sa.CheckConstraint("currency ~ '^[A-Z]{3}$'", name=op.f("ck_offers_currency_format")),
        sa.CheckConstraint("available_qty BETWEEN 0 AND 10000000", name=op.f("ck_offers_qty_range")),
        sa.CheckConstraint("ships_from ~ '^[A-Z]{2}$'", name=op.f("ck_offers_ships_from_format")),
        sa.CheckConstraint("delivery_days >= 0", name=op.f("ck_offers_delivery_days_non_negative")),
        sa.ForeignKeyConstraint(["merchant_id"], ["merchants.id"], name=op.f("fk_offers_merchant_id_merchants")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_offers")),
    )
    op.create_index("ix_offers_sku_unit_price_minor", "offers", ["sku", "unit_price_minor"], unique=False)
    op.create_index("ix_offers_merchant_id", "offers", ["merchant_id"], unique=False)
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
    op.drop_table("idempotency_records")
    op.drop_index("ix_orders_created_at", table_name="orders")
    op.drop_table("orders")
    op.drop_index("ix_offers_merchant_id", table_name="offers")
    op.drop_index("ix_offers_sku_unit_price_minor", table_name="offers")
    op.drop_table("offers")
    op.drop_table("merchants")
