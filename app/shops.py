"""Fixed list of shop instances.

The schema name is NEVER taken from a request. It comes from this constant list,
selected once per process through the ``SHOP_ID`` setting.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

ShopId = Literal["shop-pl", "shop-de", "shop-ru"]


@dataclass(frozen=True)
class ShopDefinition:
    shop_id: str
    schema: str
    runtime_role: str
    name: str
    country: str
    currency: str
    locale: str
    order_prefix: str
    dev_port: int


SHOPS: dict[str, ShopDefinition] = {
    "shop-pl": ShopDefinition(
        shop_id="shop-pl",
        schema="shop_pl",
        runtime_role="shop_pl_rt",
        name="Papiernia",
        country="PL",
        currency="PLN",
        locale="pl-PL",
        order_prefix="PL",
        dev_port=8001,
    ),
    "shop-de": ShopDefinition(
        shop_id="shop-de",
        schema="shop_de",
        runtime_role="shop_de_rt",
        name="BüroWerk",
        country="DE",
        currency="EUR",
        locale="de-DE",
        order_prefix="DE",
        dev_port=8002,
    ),
    "shop-ru": ShopDefinition(
        shop_id="shop-ru",
        schema="shop_ru",
        runtime_role="shop_ru_rt",
        name="OfficeMarket",
        country="RU",
        currency="RUB",
        locale="en",
        order_prefix="RU",
        dev_port=8003,
    ),
}

SHOP_IDS: tuple[str, ...] = tuple(SHOPS)
ALL_SCHEMAS: tuple[str, ...] = tuple(s.schema for s in SHOPS.values())

# Currencies used by the shops; all have 2 minor digits.
CURRENCY_EXPONENT: dict[str, int] = {"PLN": 2, "EUR": 2, "RUB": 2}


def get_shop(shop_id: str) -> ShopDefinition:
    try:
        return SHOPS[shop_id]
    except KeyError as exc:
        raise ValueError(f"Unknown shop id {shop_id!r}; allowed: {', '.join(SHOP_IDS)}") from exc
