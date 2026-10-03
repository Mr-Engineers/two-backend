from __future__ import annotations

import uuid
from datetime import datetime
from typing import Literal

from pydantic import Field, StrictInt

from app.countries import CountryCode
from app.schemas.common import RequestModel, ResponseModel


class SetCartItemRequest(RequestModel):
    quantity: StrictInt = Field(description="ABSOLUTE number of packs (not a delta). Retrying is safe.")
    expected_version: int | None = Field(default=None, ge=1, description="Optimistic concurrency check.")


class CartLineOut(ResponseModel):
    sku: str
    product_id: uuid.UUID
    canonical_item_code: str
    name: str
    manufacturer_name: str
    country_of_origin: CountryCode
    unit_label: str
    units_per_pack: int
    quantity: int
    unit_gross_minor: int
    line_total_gross_minor: int
    product_version: int
    active: bool
    available_stock: int


class CartOut(ResponseModel):
    id: uuid.UUID
    customer_id: uuid.UUID
    store_id: str
    status: Literal["open", "checked_out"]
    version: int
    currency: str
    items: list[CartLineOut]
    subtotal_gross_minor: int
    subtotal_gross_decimal: str
    origin_countries: list[CountryCode] = Field(description="Sorted unique origin countries of all lines.")
    created_at: datetime
    updated_at: datetime
    checked_out_at: datetime | None
