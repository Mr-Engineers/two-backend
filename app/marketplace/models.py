"""Marketplace tables.

Layout (shared Supabase project):

* ``warehouse.suppliers``       - merchant profiles (the ORM class ``Merchant``),
* ``shops.<merchant>``          - one offers table per merchant (``offers_table(name)``; written by the demo loader),
* ``shops.offers``              - read-only UNION ALL view over all merchant tables (the ORM class ``Offer``),
* ``shops.orders`` / ``shops.idempotency_records`` - created by this service (Alembic ``migrations_marketplace``).

Tables without an explicit schema are bound to ``shops`` by ``schema_translate_map``.
Prices in the merchant tables are decimal ``numeric`` (2 places); orders keep integer minor units.
Orders keep a snapshot of the offer and do not reference offers/merchants, so reloading a demo scenario
(which replaces merchants and offers) never breaks existing orders.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Column,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    MetaData,
    Numeric,
    String,
    Table,
    Text,
    func,
    text,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from app.marketplace import SUPPLIERS_SCHEMA

NAMING = {
    "ix": "ix_%(table_name)s_%(column_0_N_name)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_N_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}

# merchant_id -> its offers table in the ``shops`` schema (stored in ``warehouse.suppliers.offers_table``).
# The first five tables were created by the shops team; the others are created by the migration.
MERCHANT_TABLES: dict[str, str] = {
    "mer_biuromax": "biuromax",
    "mer_ofistorg": "ofistorg",
    "mer_cheapdeals": "cheapdeals",
    "mer_officehub": "officehub",
    "mer_printworks": "printworks",
    "mer_papiernik": "papiernik",
    "mer_promocje": "promocje",
    "mer_papierhurt": "papierhurt",
    "mer_tonerfix": "tonerfix",
}
OFFER_COLUMNS = (
    "offer_id", "merchant_id", "sku", "product_name", "unit_price", "currency", "available_qty",
    "ships_from", "delivery_days", "description", "scenario_id", "active",
)
MAX_UNIT_PRICE = Decimal("99999999.99")
MAX_AVAILABLE_QTY = 10**7

_write_metadata = MetaData()  # not part of MarketplaceBase: only used for INSERT/DELETE/UPDATE statements


def offers_table(name: str) -> Table:
    """Core definition of one merchant offers table (the name must be one of ``MERCHANT_TABLES.values()``)."""
    if name not in MERCHANT_TABLES.values():
        raise ValueError(f"Unknown offers table {name!r}")
    existing = _write_metadata.tables.get(name)
    if existing is not None:
        return existing
    return Table(
        name,
        _write_metadata,
        Column("offer_id", Text, primary_key=True),
        Column("merchant_id", Text, nullable=False),
        Column("sku", Text, nullable=False),
        Column("product_name", Text, nullable=False),
        Column("unit_price", Numeric(12, 2), nullable=False),
        Column("currency", String(3), nullable=False),
        Column("available_qty", Integer, nullable=False),
        Column("ships_from", String(2), nullable=False),
        Column("delivery_days", Integer, nullable=False),
        Column("description", Text, nullable=False),
        Column("scenario_id", Text, nullable=True),
        Column("active", Boolean, nullable=False, server_default=text("true")),
    )


class MarketplaceBase(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING)


class Merchant(MarketplaceBase):
    """``warehouse.suppliers``."""

    __tablename__ = "suppliers"
    __table_args__ = {"schema": SUPPLIERS_SCHEMA}

    id: Mapped[str] = mapped_column("merchant_id", String, primary_key=True)
    name: Mapped[str] = mapped_column(String, nullable=False)
    domain: Mapped[str] = mapped_column(String, nullable=False)
    country: Mapped[str] = mapped_column(String(2), nullable=False)
    domain_registered_at: Mapped[date] = mapped_column(Date, nullable=False)
    verified: Mapped[bool] = mapped_column(Boolean, nullable=False)
    # NULL for new merchants without reviews (the API returns ``"reputation": null``).
    reputation_score: Mapped[Decimal | None] = mapped_column(Numeric, nullable=True)
    reviews_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    offers_table: Mapped[str] = mapped_column(String, nullable=False)
    scenario_id: Mapped[str | None] = mapped_column(String, nullable=True)


class Offer(MarketplaceBase):
    """The read-only view ``shops.offers`` (UNION ALL of the merchant tables). Writes go to ``offers_table()``."""

    __tablename__ = "offers"

    id: Mapped[str] = mapped_column("offer_id", String, primary_key=True)
    merchant_id: Mapped[str] = mapped_column(String, nullable=False)
    sku: Mapped[str] = mapped_column(String, nullable=False)
    product_name: Mapped[str] = mapped_column(String, nullable=False)
    unit_price: Mapped[Decimal] = mapped_column(Numeric, nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    available_qty: Mapped[int] = mapped_column(Integer, nullable=False)
    ships_from: Mapped[str] = mapped_column(String(2), nullable=False)
    delivery_days: Mapped[int] = mapped_column(Integer, nullable=False)
    # Free text from the merchant. In the demo scenarios it may contain prompt injection or malicious commands.
    description: Mapped[str] = mapped_column(Text, nullable=False)
    scenario_id: Mapped[str | None] = mapped_column(String, nullable=True)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False)

    @property
    def unit_price_minor(self) -> int:
        return int((self.unit_price * 100).to_integral_value())


class Order(MarketplaceBase):
    __tablename__ = "orders"
    __table_args__ = (
        CheckConstraint("status IN ('confirmed')", name="status_valid"),
        CheckConstraint("quantity > 0", name="quantity_positive"),
        CheckConstraint("unit_price_minor >= 0", name="price_non_negative"),
        CheckConstraint("total_minor = unit_price_minor * quantity", name="total_consistent"),
        Index("ix_orders_created_at", "created_at"),
        Index("ix_orders_merchant_created", "merchant_id", "created_at"),
    )

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    offer_id: Mapped[str] = mapped_column(String(64), nullable=False)
    merchant_id: Mapped[str] = mapped_column(String(64), nullable=False)
    sku: Mapped[str] = mapped_column(String(64), nullable=False)
    quantity: Mapped[int] = mapped_column(Integer, nullable=False)
    unit_price_minor: Mapped[int] = mapped_column(BigInteger, nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    total_minor: Mapped[int] = mapped_column(BigInteger, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    # Informational headers from the proxy (X-Request-Id, X-On-Behalf-Of), for tracing only.
    request_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    on_behalf_of: Mapped[str | None] = mapped_column(String(128), nullable=True)


class IdempotencyRecord(MarketplaceBase):
    __tablename__ = "idempotency_records"
    __table_args__ = (
        CheckConstraint("char_length(idempotency_key) BETWEEN 8 AND 128", name="key_length"),
        CheckConstraint("request_fingerprint ~ '^[0-9a-f]{64}$'", name="fingerprint_sha256_hex"),
    )

    idempotency_key: Mapped[str] = mapped_column(String(128), primary_key=True)
    request_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    order_id: Mapped[str] = mapped_column(ForeignKey("orders.id"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


# Tables created by this service's migration (the rest belongs to the shops / warehouse teams' DDL).
MARKETPLACE_TABLES = ("orders", "idempotency_records")
