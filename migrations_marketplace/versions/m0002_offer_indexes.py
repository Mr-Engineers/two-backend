"""indexes for the offers search and the orders list

``GET /search`` filters ``shops.offers`` (UNION ALL over the merchant tables) by ``sku`` / ``product_name`` and
sorts by ``unit_price``. Only five of the nine merchant tables had a ``sku`` index (created by the shops team);
this adds a partial index ``(sku, unit_price) WHERE active`` to every table so that all branches of the view
can use it, and an index on ``shops.orders (merchant_id, created_at)``. Idempotent (``IF NOT EXISTS``).

Revision ID: m0002
Revises: m0001
Create Date: 2026-10-03 22:00:00.000000
"""
from alembic import op

from app.marketplace import MARKETPLACE_SCHEMA
from app.marketplace.models import MERCHANT_TABLES

revision = "m0002"
down_revision = "m0001"
branch_labels = None
depends_on = None

S = MARKETPLACE_SCHEMA


def upgrade() -> None:
    for table in MERCHANT_TABLES.values():
        op.execute(
            f"CREATE INDEX IF NOT EXISTS ix_{table}_sku_price ON {S}.{table} (sku, unit_price) WHERE active"
        )
    op.execute(f"CREATE INDEX IF NOT EXISTS ix_orders_merchant_created ON {S}.orders (merchant_id, created_at)")


def downgrade() -> None:
    op.execute(f"DROP INDEX IF EXISTS {S}.ix_orders_merchant_created")
    for table in MERCHANT_TABLES.values():
        op.execute(f"DROP INDEX IF EXISTS {S}.ix_{table}_sku_price")
