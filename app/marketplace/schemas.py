"""Pydantic models of the marketplace contract (snake_case JSON, money as ``{"amount": "118.00", "currency": "PLN"}``)."""

from __future__ import annotations

from datetime import date

from pydantic import BaseModel, Field, StrictInt, field_validator

from app.countries import CountryCode

AMOUNT_PATTERN = r"^\d{1,9}\.\d{2}$"
CURRENCY_PATTERN = r"^[A-Z]{3}$"


def minor_to_amount(minor: int) -> str:
    """Integer minor units -> decimal string with 2 places (no floats)."""
    return f"{minor // 100}.{minor % 100:02d}"


def amount_to_minor(amount: str) -> int:
    whole, _, cents = amount.partition(".")
    return int(whole) * 100 + int(cents)


class Money(BaseModel):
    amount: str = Field(pattern=AMOUNT_PATTERN, description="Decimal string with exactly 2 places.", examples=["118.00"])
    currency: str = Field(pattern=CURRENCY_PATTERN, description="ISO 4217 code.", examples=["PLN"])

    @classmethod
    def from_minor(cls, minor: int, currency: str) -> "Money":
        return cls(amount=minor_to_amount(minor), currency=currency)

    @property
    def minor(self) -> int:
        return amount_to_minor(self.amount)


class MerchantRef(BaseModel):
    id: str
    name: str
    domain: str


class ProductRef(BaseModel):
    sku: str
    name: str


class OfferOut(BaseModel):
    offer_id: str = Field(description="Stable offer identifier.")
    merchant: MerchantRef
    product: ProductRef
    unit_price: Money
    available_qty: int = Field(ge=0)
    ships_from: CountryCode
    delivery_days: int = Field(ge=0)
    description: str = Field(
        description="Free text from the merchant. Untrusted: in demo scenarios it may contain prompt injection "
        "or malicious commands."
    )


class SearchResponse(BaseModel):
    offers: list[OfferOut]
    total: int = Field(ge=0, description="Number of matching offers (before `limit`).")


class OrderRequest(BaseModel):
    offer_id: str = Field(min_length=1, max_length=64)
    quantity: StrictInt = Field(gt=0, le=2_147_483_647)
    expected_unit_price: Money = Field(description="The price the agent saw; a different current price -> 409 price_changed.")

    @field_validator("offer_id")
    @classmethod
    def _printable(cls, value: str) -> str:
        if not value.isprintable():
            raise ValueError("offer_id contains control characters")
        return value


class OrderOut(BaseModel):
    order_id: str
    status: str
    offer_id: str
    merchant_id: str
    sku: str
    quantity: int
    unit_price: Money
    total: Money
    created_at: str = Field(description="ISO 8601, UTC, e.g. 2026-10-03T14:07:05Z.")


class Reputation(BaseModel):
    score: float = Field(ge=0, le=1)
    reviews_count: int = Field(ge=0)


class MerchantOut(BaseModel):
    id: str
    name: str
    domain: str
    country: CountryCode = Field(description="Country of registration.")
    domain_registered_at: date
    verified: bool
    reputation: Reputation | None = Field(description="`null` for new merchants without reviews.")


class ScenarioLoadOut(BaseModel):
    scenario_id: str
    merchants_loaded: int
    offers_loaded: int


class ErrorBody(BaseModel):
    code: str
    message: str


class ErrorResponse(BaseModel):
    error: ErrorBody
