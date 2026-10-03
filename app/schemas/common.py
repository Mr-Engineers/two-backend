from __future__ import annotations

from pydantic import BaseModel, ConfigDict

from app.shops import CURRENCY_EXPONENT


def minor_to_decimal(minor: int, currency: str) -> str:
    """Derive an exact decimal string from integer minor units (no float involved)."""
    exp = CURRENCY_EXPONENT[currency]
    sign = "-" if minor < 0 else ""
    whole, frac = divmod(abs(minor), 10**exp)
    return f"{sign}{whole}.{frac:0{exp}d}"


class RequestModel(BaseModel):
    """Input models reject unknown fields, so an agent cannot smuggle in price, currency, origin, ..."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class ResponseModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)
