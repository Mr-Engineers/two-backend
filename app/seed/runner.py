"""Idempotent seed + guarded demo reset. Runs with the MIGRATION (owner) account, never the runtime one."""

from __future__ import annotations

import os
from typing import Mapping

from sqlalchemy import select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.db.models import (
    ApiKey,
    Category,
    Customer,
    Product,
    ShippingMethod,
    StoreConfig,
    TABLE_NAMES,
)
from app.db.session import Database
from app.security import hash_api_key
from app.seed import data
from app.shops import SHOPS


def demo_key_env_name(shop_id: str, customer_key: str) -> str:
    return f"DEMO_API_KEY_{shop_id.upper().replace('-', '_')}_{customer_key.upper()}"


def demo_keys_from_env(shop_id: str, environ: Mapping[str, str] | None = None) -> dict[str, str]:
    environ = environ if environ is not None else os.environ
    keys = {}
    for customer_key in data.DEMO_CUSTOMERS:
        value = environ.get(demo_key_env_name(shop_id, customer_key))
        if value:
            keys[customer_key] = value
    return keys


def seed_shop(
    db: Database, shop_id: str, api_keys: Mapping[str, str] | None = None,
    *, catalog_only: bool = False,
) -> dict[str, int]:
    """Upsert catalog data. Existing stock, orders and carts are never touched or restored."""
    shop = SHOPS[shop_id]
    with db.begin() as session:
        session.execute(
            pg_insert(StoreConfig)
            .values(id=1, store_id=shop.shop_id, name=shop.name, country=shop.country,
                    currency=shop.currency, locale=shop.locale)
            .on_conflict_do_update(
                index_elements=[StoreConfig.id],
                set_={"name": shop.name, "country": shop.country, "locale": shop.locale},
            )
        )
        cats = [
            {"id": cid, "slug": slug, "name": name, "sort_order": order}
            for cid, slug, name, order in data.categories_for(shop_id)
        ]
        cins = pg_insert(Category).values(cats)
        session.execute(
            cins.on_conflict_do_update(
                index_elements=[Category.slug],
                set_={"name": cins.excluded.name, "sort_order": cins.excluded.sort_order},
            )
        )
        category_ids = {c.slug: c.id for c in session.scalars(select(Category))}

        rows = []
        for p in data.products_for(shop_id):
            rows.append(
                {
                    "id": p.id, "sku": p.sku, "canonical_item_code": p.canonical_item_code, "name": p.name,
                    "description": p.description, "category_id": category_ids[p.category_slug],
                    "manufacturer_name": p.manufacturer_name, "country_of_origin": p.country_of_origin,
                    "unit_label": p.unit_label, "units_per_pack": p.units_per_pack,
                    "unit_gross_minor": p.unit_gross_minor, "currency": p.currency,
                    "stock_quantity": p.stock_quantity, "active": p.active, "compatible_with": p.compatible_with,
                }
            )
        ins = pg_insert(Product).values(rows)
        session.execute(
            ins.on_conflict_do_update(
                index_elements=[Product.sku],
                set_={
                    col: getattr(ins.excluded, col)
                    for col in (
                        "canonical_item_code", "name", "description", "category_id", "manufacturer_name",
                        "country_of_origin", "unit_label", "units_per_pack", "unit_gross_minor",
                        "currency", "active", "compatible_with",
                    )
                },  # stock_quantity deliberately omitted: seeding never restores sold stock
            )
        )

        if catalog_only:
            return {"products": len(rows), "categories": len(cats)}

        ship = data.shipping_for(shop_id)
        sins = pg_insert(ShippingMethod).values(ship)
        session.execute(
            sins.on_conflict_do_update(
                index_elements=[ShippingMethod.code],
                set_={c: getattr(sins.excluded, c) for c in
                      ("name", "description", "price_gross_minor", "currency",
                       "estimated_delivery_days", "sort_order", "active")},
            )
        )

        for customer_key, display in data.DEMO_CUSTOMERS.items():
            cid = data.demo_customer_id(shop_id, customer_key)
            session.execute(
                pg_insert(Customer).values(id=cid, display_name=display).on_conflict_do_nothing(index_elements=[Customer.id])
            )
            raw = (api_keys or {}).get(customer_key)
            if raw:
                session.execute(
                    pg_insert(ApiKey)
                    .values(customer_id=cid, key_hash=hash_api_key(raw), label=f"demo-{customer_key}", active=True)
                    .on_conflict_do_nothing(index_elements=[ApiKey.key_hash])
                )
    return {"products": len(rows), "categories": len(cats), "shipping_methods": len(ship)}


class ResetNotAllowed(RuntimeError):
    pass


def reset_demo(db: Database, shop_id: str, api_keys: Mapping[str, str] | None, *, confirm: bool, environ: Mapping[str, str] | None = None) -> dict[str, int]:
    """DESTRUCTIVE: wipes carts, quotes, orders, idempotency records, audit log, api keys and the catalog,
    then reseeds (stock restored). Opt-in only and refused for production-like configuration."""
    environ = environ if environ is not None else os.environ
    if not confirm:
        raise ResetNotAllowed("Reset requires explicit confirmation (--yes).")
    if environ.get("ALLOW_DEMO_RESET", "").lower() not in ("1", "true", "yes"):
        raise ResetNotAllowed("Set ALLOW_DEMO_RESET=true to allow wiping demo data.")
    if environ.get("APP_ENV", "development").lower() == "production":
        raise ResetNotAllowed("Refusing to reset data when APP_ENV=production.")
    schema = SHOPS[shop_id].schema
    with db.begin() as session:
        tables = ", ".join(f'"{schema}"."{t}"' for t in TABLE_NAMES)  # fixed schema + model table names
        session.execute(text(f"TRUNCATE {tables} RESTART IDENTITY CASCADE"))
        session.execute(text(f'ALTER SEQUENCE "{schema}".order_number_seq RESTART WITH 1'))
    return seed_shop(db, shop_id, api_keys)
