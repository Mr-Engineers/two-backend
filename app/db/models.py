"""SQLAlchemy models. Tables carry NO schema: the schema (shop_pl / shop_de / shop_ru) is applied
per engine through ``schema_translate_map`` from the fixed shop list, never from request data."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    ARRAY,
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Identity,
    Index,
    Integer,
    MetaData,
    Sequence,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

from app.countries import ISO_3166_1_ALPHA2

NAMING = {
    "ix": "ix_%(table_name)s_%(column_0_N_name)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_N_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING)


order_number_seq = Sequence("order_number_seq", metadata=Base.metadata, start=1)

CART_STATUSES = ("open", "checked_out")
ORDER_STATUSES = ("placed",)
PAYMENT_STATUSES = ("paid_mock",)

COUNTRY_LIST_SQL = ", ".join(f"'{c}'" for c in sorted(ISO_3166_1_ALPHA2))


def _now() -> Any:
    return func.now()


def _uuid_pk() -> Mapped[uuid.UUID]:
    return mapped_column(Uuid, primary_key=True, default=uuid.uuid4)


def _ts(**kw: Any) -> Mapped[datetime]:
    return mapped_column(DateTime(timezone=True), server_default=_now(), nullable=False, **kw)


class StoreConfig(Base):
    __tablename__ = "store_config"
    __table_args__ = (
        CheckConstraint("id = 1", name="single_row"),
        CheckConstraint("country ~ '^[A-Z]{2}$'", name="country_format"),
        CheckConstraint("currency ~ '^[A-Z]{3}$'", name="currency_format"),
        UniqueConstraint("currency"),
        UniqueConstraint("store_id"),
    )

    id: Mapped[int] = mapped_column(SmallInteger, primary_key=True, default=1)
    store_id: Mapped[str] = mapped_column(String(32), nullable=False)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    country: Mapped[str] = mapped_column(String(2), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    locale: Mapped[str] = mapped_column(String(16), nullable=False)
    created_at: Mapped[datetime] = _ts()


class Category(Base):
    __tablename__ = "categories"
    __table_args__ = (UniqueConstraint("slug"),)

    id: Mapped[uuid.UUID] = _uuid_pk()
    slug: Mapped[str] = mapped_column(String(64), nullable=False)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


class Product(Base):
    __tablename__ = "products"
    __table_args__ = (
        UniqueConstraint("sku"),
        CheckConstraint("unit_gross_minor >= 0", name="price_non_negative"),
        CheckConstraint("stock_quantity >= 0", name="stock_non_negative"),
        CheckConstraint("units_per_pack > 0", name="units_per_pack_positive"),
        CheckConstraint("version >= 1", name="version_positive"),
        CheckConstraint(f"country_of_origin IN ({COUNTRY_LIST_SQL})", name="country_of_origin_iso"),
        CheckConstraint("char_length(sku) BETWEEN 1 AND 64", name="sku_length"),
        Index("ix_products_category_id", "category_id"),
        Index("ix_products_canonical_item_code", "canonical_item_code"),
        Index("ix_products_country_of_origin", "country_of_origin"),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    sku: Mapped[str] = mapped_column(String(64), nullable=False)
    canonical_item_code: Mapped[str] = mapped_column(String(64), nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    category_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("categories.id"), nullable=False)
    manufacturer_name: Mapped[str] = mapped_column(String(120), nullable=False)
    country_of_origin: Mapped[str] = mapped_column(String(2), nullable=False)
    unit_label: Mapped[str] = mapped_column(String(32), nullable=False)
    units_per_pack: Mapped[int] = mapped_column(Integer, nullable=False)
    unit_gross_minor: Mapped[int] = mapped_column(BigInteger, nullable=False)
    currency: Mapped[str] = mapped_column(ForeignKey("store_config.currency"), nullable=False)
    stock_quantity: Mapped[int] = mapped_column(Integer, nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    compatible_with: Mapped[list[str]] = mapped_column(
        ARRAY(Text), nullable=False, server_default=text("'{}'::text[]"), default=list
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default="1")
    created_at: Mapped[datetime] = _ts()
    updated_at: Mapped[datetime] = _ts()

    category: Mapped[Category] = relationship(lazy="joined", innerjoin=True)


class Customer(Base):
    __tablename__ = "customers"

    id: Mapped[uuid.UUID] = _uuid_pk()
    display_name: Mapped[str] = mapped_column(String(120), nullable=False)
    created_at: Mapped[datetime] = _ts()


class ApiKey(Base):
    __tablename__ = "api_keys"
    __table_args__ = (
        UniqueConstraint("key_hash"),
        CheckConstraint("key_hash ~ '^[0-9a-f]{64}$'", name="key_hash_sha256_hex"),
        Index("ix_api_keys_customer_id", "customer_id"),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    customer_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("customers.id"), nullable=False)
    key_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    label: Mapped[str] = mapped_column(String(64), nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = _ts()


class ShippingMethod(Base):
    __tablename__ = "shipping_methods"
    __table_args__ = (
        UniqueConstraint("code"),
        CheckConstraint("price_gross_minor >= 0", name="price_non_negative"),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    code: Mapped[str] = mapped_column(String(32), nullable=False)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    price_gross_minor: Mapped[int] = mapped_column(BigInteger, nullable=False)
    currency: Mapped[str] = mapped_column(ForeignKey("store_config.currency"), nullable=False)
    estimated_delivery_days: Mapped[int] = mapped_column(Integer, nullable=False)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    updated_at: Mapped[datetime] = _ts()


class Cart(Base):
    __tablename__ = "carts"
    __table_args__ = (
        CheckConstraint(f"status IN ({', '.join(repr(s) for s in CART_STATUSES)})", name="status_valid"),
        CheckConstraint("version >= 1", name="version_positive"),
        Index("ix_carts_customer_id_status", "customer_id", "status"),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    customer_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("customers.id"), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="open")
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    currency: Mapped[str] = mapped_column(ForeignKey("store_config.currency"), nullable=False)
    created_at: Mapped[datetime] = _ts()
    updated_at: Mapped[datetime] = _ts()
    checked_out_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class CartItem(Base):
    __tablename__ = "cart_items"
    __table_args__ = (
        CheckConstraint("quantity > 0", name="quantity_positive"),
        Index("ix_cart_items_product_id", "product_id"),
    )

    cart_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("carts.id", ondelete="CASCADE"), primary_key=True
    )
    product_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("products.id"), primary_key=True)
    quantity: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = _ts()
    updated_at: Mapped[datetime] = _ts()


class CheckoutQuote(Base):
    __tablename__ = "checkout_quotes"
    __table_args__ = (
        CheckConstraint("subtotal_gross_minor >= 0 AND shipping_gross_minor >= 0", name="amounts_non_negative"),
        CheckConstraint("total_gross_minor = subtotal_gross_minor + shipping_gross_minor", name="total_consistent"),
        CheckConstraint("expires_at > created_at", name="expiry_after_creation"),
        Index("ix_checkout_quotes_cart_id", "cart_id"),
        Index("ix_checkout_quotes_customer_id", "customer_id"),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    cart_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("carts.id"), nullable=False)
    cart_version: Mapped[int] = mapped_column(Integer, nullable=False)
    customer_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("customers.id"), nullable=False)
    store_id: Mapped[str] = mapped_column(String(32), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    shipping_method_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("shipping_methods.id"), nullable=False)
    shipping_method_code: Mapped[str] = mapped_column(String(32), nullable=False)
    shipping_method_name: Mapped[str] = mapped_column(String(120), nullable=False)
    shipping_address: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    currency: Mapped[str] = mapped_column(ForeignKey("store_config.currency"), nullable=False)
    subtotal_gross_minor: Mapped[int] = mapped_column(BigInteger, nullable=False)
    shipping_gross_minor: Mapped[int] = mapped_column(BigInteger, nullable=False)
    total_gross_minor: Mapped[int] = mapped_column(BigInteger, nullable=False)
    origin_countries: Mapped[list[str]] = mapped_column(ARRAY(String(2)), nullable=False)

    items: Mapped[list["CheckoutQuoteItem"]] = relationship(
        order_by="CheckoutQuoteItem.line_no", lazy="selectin"
    )


class CheckoutQuoteItem(Base):
    __tablename__ = "checkout_quote_items"
    __table_args__ = (
        UniqueConstraint("quote_id", "line_no"),
        UniqueConstraint("quote_id", "product_id"),
        CheckConstraint("quantity > 0", name="quantity_positive"),
        CheckConstraint("unit_gross_minor >= 0", name="price_non_negative"),
        CheckConstraint("line_total_gross_minor = unit_gross_minor * quantity", name="line_total_consistent"),
        CheckConstraint(f"country_of_origin IN ({COUNTRY_LIST_SQL})", name="country_of_origin_iso"),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    quote_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("checkout_quotes.id", ondelete="CASCADE"), nullable=False
    )
    line_no: Mapped[int] = mapped_column(Integer, nullable=False)
    product_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("products.id"), nullable=False)
    sku: Mapped[str] = mapped_column(String(64), nullable=False)
    canonical_item_code: Mapped[str] = mapped_column(String(64), nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    manufacturer_name: Mapped[str] = mapped_column(String(120), nullable=False)
    unit_label: Mapped[str] = mapped_column(String(32), nullable=False)
    units_per_pack: Mapped[int] = mapped_column(Integer, nullable=False)
    quantity: Mapped[int] = mapped_column(Integer, nullable=False)
    unit_gross_minor: Mapped[int] = mapped_column(BigInteger, nullable=False)
    line_total_gross_minor: Mapped[int] = mapped_column(BigInteger, nullable=False)
    product_version: Mapped[int] = mapped_column(Integer, nullable=False)
    country_of_origin: Mapped[str] = mapped_column(String(2), nullable=False)


class Order(Base):
    __tablename__ = "orders"
    __table_args__ = (
        UniqueConstraint("order_number"),
        UniqueConstraint("quote_id"),
        UniqueConstraint("cart_id"),
        CheckConstraint(f"status IN ({', '.join(repr(s) for s in ORDER_STATUSES)})", name="status_valid"),
        CheckConstraint(
            f"payment_status IN ({', '.join(repr(s) for s in PAYMENT_STATUSES)})", name="payment_status_valid"
        ),
        CheckConstraint("total_gross_minor = subtotal_gross_minor + shipping_gross_minor", name="total_consistent"),
        Index("ix_orders_customer_id_created_at", "customer_id", "created_at"),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    order_number: Mapped[str] = mapped_column(String(32), nullable=False)
    customer_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("customers.id"), nullable=False)
    quote_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("checkout_quotes.id"), nullable=False)
    cart_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("carts.id"), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="placed")
    payment_status: Mapped[str] = mapped_column(String(16), nullable=False, default="paid_mock")
    currency: Mapped[str] = mapped_column(ForeignKey("store_config.currency"), nullable=False)
    subtotal_gross_minor: Mapped[int] = mapped_column(BigInteger, nullable=False)
    shipping_gross_minor: Mapped[int] = mapped_column(BigInteger, nullable=False)
    total_gross_minor: Mapped[int] = mapped_column(BigInteger, nullable=False)
    shipping_method_code: Mapped[str] = mapped_column(String(32), nullable=False)
    shipping_method_name: Mapped[str] = mapped_column(String(120), nullable=False)
    shipping_address: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    origin_countries: Mapped[list[str]] = mapped_column(ARRAY(String(2)), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    items: Mapped[list["OrderItem"]] = relationship(order_by="OrderItem.line_no", lazy="selectin")


class OrderItem(Base):
    __tablename__ = "order_items"
    __table_args__ = (
        UniqueConstraint("order_id", "line_no"),
        CheckConstraint("quantity > 0", name="quantity_positive"),
        CheckConstraint("line_total_gross_minor = unit_gross_minor * quantity", name="line_total_consistent"),
        CheckConstraint(f"country_of_origin IN ({COUNTRY_LIST_SQL})", name="country_of_origin_iso"),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    order_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("orders.id", ondelete="CASCADE"), nullable=False)
    line_no: Mapped[int] = mapped_column(Integer, nullable=False)
    product_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("products.id"), nullable=False)
    sku: Mapped[str] = mapped_column(String(64), nullable=False)
    canonical_item_code: Mapped[str] = mapped_column(String(64), nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    manufacturer_name: Mapped[str] = mapped_column(String(120), nullable=False)
    unit_label: Mapped[str] = mapped_column(String(32), nullable=False)
    units_per_pack: Mapped[int] = mapped_column(Integer, nullable=False)
    quantity: Mapped[int] = mapped_column(Integer, nullable=False)
    unit_gross_minor: Mapped[int] = mapped_column(BigInteger, nullable=False)
    line_total_gross_minor: Mapped[int] = mapped_column(BigInteger, nullable=False)
    product_version: Mapped[int] = mapped_column(Integer, nullable=False)
    country_of_origin: Mapped[str] = mapped_column(String(2), nullable=False)


class MockPayment(Base):
    __tablename__ = "mock_payments"
    __table_args__ = (
        UniqueConstraint("order_id"),
        CheckConstraint("amount_gross_minor >= 0", name="amount_non_negative"),
        CheckConstraint("status IN ('succeeded')", name="status_valid"),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    order_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("orders.id"), nullable=False)
    customer_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("customers.id"), nullable=False)
    provider: Mapped[str] = mapped_column(String(16), nullable=False, default="mock")
    provider_reference: Mapped[str] = mapped_column(String(64), nullable=False)
    amount_gross_minor: Mapped[int] = mapped_column(BigInteger, nullable=False)
    currency: Mapped[str] = mapped_column(ForeignKey("store_config.currency"), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="succeeded")
    created_at: Mapped[datetime] = _ts()


class IdempotencyRecord(Base):
    __tablename__ = "idempotency_records"
    __table_args__ = (
        UniqueConstraint("customer_id", "operation", "idempotency_key"),
        CheckConstraint("char_length(idempotency_key) BETWEEN 8 AND 128", name="key_length"),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    customer_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("customers.id"), nullable=False)
    operation: Mapped[str] = mapped_column(String(32), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(128), nullable=False)
    request_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    order_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("orders.id"), nullable=False)
    result: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = _ts()


class AuditEvent(Base):
    __tablename__ = "audit_events"
    __table_args__ = (
        CheckConstraint("result IN ('success', 'error')", name="result_valid"),
        Index("ix_audit_events_customer_id_created_at", "customer_id", "created_at"),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    created_at: Mapped[datetime] = _ts()
    store_id: Mapped[str] = mapped_column(String(32), nullable=False)
    customer_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    operation: Mapped[str] = mapped_column(String(48), nullable=False)
    request_id: Mapped[str] = mapped_column(String(64), nullable=False)
    correlation_id: Mapped[str] = mapped_column(String(128), nullable=False)
    cart_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    quote_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    order_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    result: Mapped[str] = mapped_column(String(16), nullable=False)
    error_code: Mapped[str | None] = mapped_column(String(48), nullable=True)
    details: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, server_default=text("'{}'::jsonb"))


# Tables in dependency-safe order for TRUNCATE / grants.
TABLE_NAMES = tuple(Base.metadata.tables)
