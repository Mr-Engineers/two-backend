from __future__ import annotations

import uuid
from datetime import datetime
from typing import Literal

from pydantic import Field

from app.countries import CountryCode
from app.schemas.common import RequestModel, ResponseModel


class ShippingAddress(RequestModel):
    recipient_name: str = Field(min_length=1, max_length=120)
    line1: str = Field(min_length=1, max_length=120)
    line2: str | None = Field(default=None, max_length=120)
    postal_code: str = Field(min_length=1, max_length=20)
    city: str = Field(min_length=1, max_length=80)
    country: CountryCode = Field(description="Shipping destination country (not the product origin).")
    phone: str | None = Field(default=None, pattern=r"^\+?[0-9 ()\-]{5,24}$")
    email: str | None = Field(default=None, max_length=254, pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


class QuoteRequest(RequestModel):
    shipping_address: ShippingAddress
    shipping_method_code: str | None = Field(
        default=None, pattern=r"^[a-z0-9_-]{1,32}$", description="Defaults to the shop's first shipping method."
    )
    expected_cart_version: int | None = Field(default=None, ge=1)


class CheckoutRequest(RequestModel):
    quote_id: uuid.UUID
    idempotency_key: str = Field(min_length=8, max_length=128, pattern=r"^[A-Za-z0-9._:\-]+$")


class LineSnapshot(ResponseModel):
    line_no: int
    sku: str
    product_id: uuid.UUID
    canonical_item_code: str
    name: str
    manufacturer_name: str
    unit_label: str
    units_per_pack: int
    quantity: int
    unit_gross_minor: int
    line_total_gross_minor: int
    product_version: int
    country_of_origin: CountryCode


class ShippingSnapshot(ResponseModel):
    method_code: str
    method_name: str
    price_gross_minor: int
    price_gross_decimal: str
    address: ShippingAddress


class QuoteOut(ResponseModel):
    quote_id: uuid.UUID
    cart_id: uuid.UUID
    cart_version: int
    customer_id: uuid.UUID
    store_id: str
    created_at: datetime
    expires_at: datetime
    is_expired: bool
    currency: str
    items: list[LineSnapshot]
    shipping: ShippingSnapshot
    subtotal_gross_minor: int
    subtotal_gross_decimal: str
    shipping_gross_minor: int
    total_gross_minor: int
    total_gross_decimal: str
    origin_countries: list[CountryCode] = Field(
        description="Sorted unique origin countries of ALL lines. Read this before calling checkout."
    )
    note: str = "A quote is a price snapshot with a TTL. It does not reserve stock and does not charge anything."


class OrderOut(ResponseModel):
    order_id: uuid.UUID
    order_number: str
    customer_id: uuid.UUID
    store_id: str
    quote_id: uuid.UUID
    cart_id: uuid.UUID
    status: Literal["placed"]
    payment_status: Literal["paid_mock"]
    currency: str
    items: list[LineSnapshot]
    shipping: ShippingSnapshot
    subtotal_gross_minor: int
    subtotal_gross_decimal: str
    shipping_gross_minor: int
    total_gross_minor: int
    total_gross_decimal: str
    origin_countries: list[CountryCode]
    created_at: datetime
    updated_at: datetime


class CheckoutOut(ResponseModel):
    order: OrderOut
    idempotent_replay: bool = Field(description="True when this response replays an earlier successful checkout.")


class OrderPage(ResponseModel):
    items: list[OrderOut]
    total: int
    limit: int
    offset: int
