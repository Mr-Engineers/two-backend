"""Loading demo scenarios: replaces merchants and offers with the base catalog + the scenario additions.

Orders and idempotency records are never touched (orders hold their own snapshot of the offer).
"""

from __future__ import annotations

from datetime import date

from sqlalchemy import delete, func, insert, select, text

from app.db.session import Database
from app.marketplace.models import Merchant, Offer
from app.marketplace.scenarios import DEFAULT_SCENARIO_ID, Scenario, build_scenario

_LOAD_LOCK_KEY = 7_100_001  # serialises concurrent scenario loads


def apply_scenario(session, scenario: Scenario) -> dict[str, object]:
    session.execute(text("SELECT pg_advisory_xact_lock(:k)"), {"k": _LOAD_LOCK_KEY})
    session.execute(delete(Offer))
    session.execute(delete(Merchant))
    session.execute(
        insert(Merchant),
        [
            {
                "id": m.id, "name": m.name, "domain": m.domain, "country": m.country,
                "domain_registered_at": m.domain_registered_at, "verified": m.verified,
                "reputation_score": m.reputation_score, "reputation_reviews_count": m.reputation_reviews_count,
            }
            for m in scenario.merchants
        ],
    )
    session.execute(
        insert(Offer),
        [
            {
                "id": o.id, "merchant_id": o.merchant_id, "sku": o.sku, "product_name": o.product_name,
                "unit_price_minor": o.unit_price_minor, "currency": o.currency, "available_qty": o.available_qty,
                "ships_from": o.ships_from, "delivery_days": o.delivery_days, "description": o.description,
            }
            for o in scenario.offers
        ],
    )
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
    """Idempotent bootstrap seed: loads ``scenario_id`` only when there are no merchants yet,
    so re-running ``seed`` never replaces a scenario that was loaded for a demo."""
    with db.begin() as session:
        if session.scalar(select(func.count()).select_from(Merchant)):
            return {"scenario_id": None, "skipped": "merchants already present"}
        return apply_scenario(session, build_scenario(scenario_id, today))
