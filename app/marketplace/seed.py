"""Loading demo scenarios: replaces the marketplace merchants and offers with the base catalog + the scenario additions.

* merchants  -> ``warehouse.suppliers`` (only the merchants known to the marketplace, ``MERCHANT_TABLES``),
* offers     -> the per-merchant tables in ``shops`` (``shops.offers`` is the view over them).

Rows of the base catalog have ``scenario_id`` NULL; the additions of a scenario carry its id.
Orders and idempotency records are never touched (orders hold their own snapshot of the offer), and merchants that
are referenced by other teams' tables (e.g. ``warehouse.purchase_orders``) are never deleted.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from sqlalchemy import delete, func, insert, select, text
from sqlalchemy.exc import IntegrityError

from app.db.session import Database
from app.marketplace import SUPPLIERS_SCHEMA
from app.marketplace.models import MERCHANT_TABLES, Merchant, offers_table
from app.marketplace.scenarios import (
    DEFAULT_SCENARIO_ID,
    MerchantSeed,
    OfferSeed,
    Scenario,
    base_merchant_ids,
    build_scenario,
)

SUPPLIERS = f"{SUPPLIERS_SCHEMA}.suppliers"  # fixed identifier, never built from input
_LOAD_LOCK_KEY = 7_100_001  # serialises concurrent scenario loads


def merchant_row(m: MerchantSeed, scenario_id: str | None) -> dict:
    return {
        "merchant_id": m.id, "name": m.name, "domain": m.domain, "country": m.country,
        "domain_registered_at": m.domain_registered_at, "verified": m.verified,
        "reputation_score": None if m.reputation_score is None else Decimal(str(m.reputation_score)),
        "reviews_count": m.reputation_reviews_count or 0,
        "offers_table": MERCHANT_TABLES[m.id], "scenario_id": scenario_id,
    }


def offer_row(o: OfferSeed, scenario_id: str | None) -> dict:
    return {
        "offer_id": o.id, "merchant_id": o.merchant_id, "sku": o.sku, "product_name": o.product_name,
        "unit_price": Decimal(o.unit_price_minor) / 100, "currency": o.currency,
        "available_qty": o.available_qty, "ships_from": o.ships_from, "delivery_days": o.delivery_days,
        "description": o.description, "scenario_id": scenario_id, "active": True,
    }


def clear_marketplace(session, keep_merchants: set[str] = frozenset()) -> None:
    """Delete all offers of the known merchants and the known merchants that are not in ``keep_merchants``."""
    for table in MERCHANT_TABLES.values():
        session.execute(delete(offers_table(table)))
    stale = [mid for mid in MERCHANT_TABLES if mid not in keep_merchants]
    for merchant_id in stale:
        try:
            with session.begin_nested():
                session.execute(text(f"DELETE FROM {SUPPLIERS} WHERE merchant_id = :m"), {"m": merchant_id})
        except IntegrityError:  # referenced by another team's table (e.g. purchase orders): keep the profile
            pass


def apply_scenario(session, scenario: Scenario) -> dict[str, object]:
    session.execute(text("SELECT pg_advisory_xact_lock(:k)"), {"k": _LOAD_LOCK_KEY})
    base_ids = base_merchant_ids()

    def scenario_of(merchant_id: str) -> str | None:
        return None if merchant_id in base_ids else scenario.scenario_id

    clear_marketplace(session, keep_merchants={m.id for m in scenario.merchants})
    # upsert: a kept (undeletable) profile is simply overwritten with the scenario's values
    rows = [merchant_row(m, scenario_of(m.id)) for m in scenario.merchants]
    columns = list(rows[0])
    session.execute(
        text(
            f"INSERT INTO {SUPPLIERS} ({', '.join(columns)}) VALUES ({', '.join(':' + c for c in columns)}) "
            "ON CONFLICT (merchant_id) DO UPDATE SET "
            + ", ".join(f"{c} = EXCLUDED.{c}" for c in columns if c != "merchant_id")
        ),
        rows,
    )
    by_table: dict[str, list[dict]] = {}
    for o in scenario.offers:
        by_table.setdefault(MERCHANT_TABLES[o.merchant_id], []).append(offer_row(o, scenario_of(o.merchant_id)))
    for table, offers in by_table.items():
        session.execute(insert(offers_table(table)), offers)
    return {
        "scenario_id": scenario.scenario_id,
        "merchants_loaded": len(scenario.merchants),
        "offers_loaded": len(scenario.offers),
    }


def load_scenario(db: Database, scenario_id: str, today: date) -> dict[str, object]:
    """Raises ``KeyError`` for an unknown scenario id."""
    scenario = build_scenario(scenario_id, today)
    with db.begin() as session:
        return apply_scenario(session, scenario)


def seed_marketplace(db: Database, today: date, scenario_id: str = DEFAULT_SCENARIO_ID) -> dict[str, object]:
    """Idempotent bootstrap seed: loads ``scenario_id`` only when the marketplace has no merchants yet,
    so re-running ``seed`` never replaces a scenario that was loaded for a demo."""
    with db.begin() as session:
        known = session.scalar(
            select(func.count()).select_from(Merchant).where(Merchant.id.in_(list(MERCHANT_TABLES)))
        )
        if known:
            return {"scenario_id": None, "skipped": "merchants already present"}
        return apply_scenario(session, build_scenario(scenario_id, today))
