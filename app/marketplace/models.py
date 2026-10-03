"""Marketplace tables. They carry no schema: ``schema_translate_map`` binds them to ``marketplace``.

Money is stored as integer minor units (2 decimal places, as in the contract) plus an ISO 4217 code.
Orders keep a snapshot of the offer and do not reference the offer/merchant tables, so loading another demo
scenario (which replaces merchants and offers) never breaks existing orders.
"""

from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    MetaData,
    Numeric,
    String,
    Text,
    func,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

NAMING = {
    "ix": "ix_%(table_name)s_%(column_0_N_name)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_N_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}

# Upper bounds keep unit_price_minor * quantity far below the BIGINT limit.
MAX_UNIT_PRICE_MINOR = 10**10
MAX_AVAILABLE_QTY = 10**7


class MarketplaceBase(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING)


class Merchant(MarketplaceBase):
    __tablename__ = "merchants"
    __table_args__ = (
        CheckConstraint("country ~ '^[A-Z]{2}$'", name="country_format"),
        CheckConstraint(
            "(reputation_score IS NULL) = (reputation_reviews_count IS NULL)", name="reputation_complete"
        ),
        CheckConstraint("reputation_score IS NULL OR reputation_score BETWEEN 0 AND 1", name="reputation_range"),
        CheckConstraint(
            "reputation_reviews_count IS NULL OR reputation_reviews_count >= 0", name="reviews_non_negative"
        ),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    domain: Mapped[str] = mapped_column(String(255), nullable=False)
    country: Mapped[str] = mapped_column(String(2), nullable=False)
    domain_registered_at: Mapped[date] = mapped_column(Date, nullable=False)
    verified: Mapped[bool] = mapped_column(Boolean, nullable=False)
    # NULL for new merchants without reviews (the API returns ``"reputation": null``).
    reputation_score: Mapped[float | None] = mapped_column(Numeric(4, 3), nullable=True)
    reputation_reviews_count: Mapped[int | None] = mapped_column(Integer, nullable=True)


class Offer(MarketplaceBase):
    __tablename__ = "offers"
    __table_args__ = (
        CheckConstraint(f"unit_price_minor BETWEEN 0 AND {MAX_UNIT_PRICE_MINOR}", name="price_range"),
        CheckConstraint("currency ~ '^[A-Z]{3}$'", name="currency_format"),
        CheckConstraint(f"available_qty BETWEEN 0 AND {MAX_AVAILABLE_QTY}", name="qty_range"),
        CheckConstraint("ships_from ~ '^[A-Z]{2}$'", name="ships_from_format"),
        CheckConstraint("delivery_days >= 0", name="delivery_days_non_negative"),
        Index("ix_offers_sku_unit_price_minor", "sku", "unit_price_minor"),
        Index("ix_offers_merchant_id", "merchant_id"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    merchant_id: Mapped[str] = mapped_column(ForeignKey("merchants.id"), nullable=False)
    sku: Mapped[str] = mapped_column(String(64), nullable=False)
    product_name: Mapped[str] = mapped_column(String(200), nullable=False)
    unit_price_minor: Mapped[int] = mapped_column(BigInteger, nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    available_qty: Mapped[int] = mapped_column(Integer, nullable=False)
    ships_from: Mapped[str] = mapped_column(String(2), nullable=False)
    delivery_days: Mapped[int] = mapped_column(Integer, nullable=False)
    # Free text from the merchant. In the demo scenarios it may contain prompt injection or malicious commands.
    description: Mapped[str] = mapped_column(Text, nullable=False)


class Order(MarketplaceBase):
    __tablename__ = "orders"
    __table_args__ = (
        CheckConstraint("status IN ('confirmed')", name="status_valid"),
        CheckConstraint("quantity > 0", name="quantity_positive"),
        CheckConstraint("unit_price_minor >= 0", name="price_non_negative"),
        CheckConstraint("total_minor = unit_price_minor * quantity", name="total_consistent"),
        Index("ix_orders_created_at", "created_at"),
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


MARKETPLACE_TABLES = tuple(MarketplaceBase.metadata.tables)
