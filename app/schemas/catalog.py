from __future__ import annotations

import uuid
from datetime import datetime
from typing import Literal

from pydantic import Field

from app.countries import CountryCode
from app.schemas.common import ResponseModel


class StoreInfo(ResponseModel):
    store_id: str
    name: str
    store_country: str = Field(description="Country where the shop is established (NOT the product origin).")
    currency: str
    locale: str
    catalog_language: str
    quote_ttl_seconds: int
    max_cart_lines: int
    max_line_quantity: int
    price_basis: Literal["gross"] = "gross"
    money_format: str = "Amounts are integers in minor units (fields ending in _minor) next to a currency code."
    payment: Literal["mock"] = "mock"


class CategoryOut(ResponseModel):
    id: uuid.UUID
    slug: str
    name: str
    product_count: int = Field(description="Number of active products in the category.")


class ProductOut(ResponseModel):
    id: uuid.UUID
    sku: str
    canonical_item_code: str
    name: str
    description: str
    category_slug: str
    category_name: str
    manufacturer_name: str
    country_of_origin: CountryCode = Field(
        description="ISO 3166-1 alpha-2 country where THIS product was made. Independent from the shop country."
    )
    unit_label: str = Field(description="Base unit inside one pack, e.g. sheet / piece.")
    units_per_pack: int = Field(description="Number of base units in one sold pack. Quantities are in packs.")
    unit_gross_minor: int = Field(description="Gross price of ONE pack in minor units.")
    unit_gross_decimal: str
    currency: str
    stock_quantity: int = Field(description="Available packs. Carts do not reserve stock.")
    in_stock: bool
    active: bool
    compatible_with_canonical_codes: list[str]
    version: int
    created_at: datetime
    updated_at: datetime


class CategoryList(ResponseModel):
    items: list[CategoryOut]


class ProductPage(ResponseModel):
    items: list[ProductOut]
    total: int
    limit: int
    offset: int


class ShippingMethodOut(ResponseModel):
    code: str
    name: str
    description: str
    price_gross_minor: int
    price_gross_decimal: str
    currency: str
    estimated_delivery_days: int


class ShippingMethodList(ResponseModel):
    items: list[ShippingMethodOut]
