"""Marketplace business logic over the Supabase REST API (PostgREST) - ``SUPABASE_URL`` + ``SUPABASE_KEY``.

Same public interface and the same results as ``MarketplaceService`` (direct SQL). Differences forced by HTTP:

* no cross-schema JOIN: ``shops.offers`` is read first, then the matching ``warehouse.suppliers`` rows;
* no multi-statement transaction: idempotent ordering and the scenario loader run inside the database as the
  functions ``shops.create_order`` / ``shops.load_scenario`` (migration ``m0003``), so they stay atomic.

``SUPABASE_KEY`` is the ``service_role`` key, a full-access secret: keep it server-side only. The caller's
authorisation is still the marketplace bearer token (``MARKETPLACE_API_TOKEN``).
"""

from __future__ import annotations

import logging
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any, Callable, TypeVar

import httpx
from postgrest import SyncPostgrestClient
from postgrest.exceptions import APIError

from app.marketplace import MARKETPLACE_HEAD_REVISION, MARKETPLACE_SCHEMA, SUPPLIERS_SCHEMA
from app.marketplace.config import MarketplaceSettings
from app.marketplace.errors import MarketplaceError
from app.marketplace.models import MERCHANT_TABLES
from app.marketplace.scenarios import base_merchant_ids, build_scenario
from app.marketplace.schemas import (
    MerchantOut,
    MerchantRef,
    Money,
    OfferOut,
    OrderOut,
    OrderRequest,
    ProductRef,
    Reputation,
    ScenarioLoadOut,
    SearchResponse,
)
from app.marketplace.service import CallerContext, _fingerprint, _like_pattern, _utcnow

logger = logging.getLogger("marketplace.service")
audit_logger = logging.getLogger("marketplace.audit")

T = TypeVar("T")

# Errors raised by shops.create_order (SQLSTATE P0001, the code is the message).
_BUSINESS_ERRORS = frozenset({"offer_not_found", "price_changed", "insufficient_quantity", "idempotency_conflict"})
# PostgREST / gateway codes that mean "the database cannot be reached right now".
_UNAVAILABLE_CODES = frozenset(
    {"502", "503", "504", "520", "521", "522", "523", "524", "PGRST000", "PGRST001", "PGRST002", "PGRST003", "57014"}
)
_HINTS = {
    "PGRST106": "The shops/warehouse schemas are not exposed by the Data API (run: python -m app.cli expose-api).",
    "406": "The shops/warehouse schemas are not exposed by the Data API (run: python -m app.cli expose-api).",
    "401": "SUPABASE_KEY was rejected.",
    "403": "SUPABASE_KEY was rejected.",
    "PGRST202": "Database functions are missing (run the migrations: python -m app.cli migrate).",
    "PGRST301": "SUPABASE_KEY was rejected.",
    "PGRST303": "SUPABASE_KEY was rejected.",
    "42501": "SUPABASE_KEY lacks privileges (it must be the service_role key).",
}


def _minor(value: Any) -> int:
    """``numeric(12,2)`` as sent by PostgREST (a JSON number) -> integer minor units."""
    return int((Decimal(str(value)) * 100).to_integral_value())


def _offer_out(offer: dict[str, Any], merchant: dict[str, Any]) -> OfferOut:
    return OfferOut(
        offer_id=offer["offer_id"],
        merchant=MerchantRef(id=merchant["merchant_id"], name=merchant["name"], domain=merchant["domain"]),
        product=ProductRef(sku=offer["sku"], name=offer["product_name"]),
        unit_price=Money.from_minor(_minor(offer["unit_price"]), str(offer["currency"]).strip()),
        available_qty=offer["available_qty"],
        ships_from=str(offer["ships_from"]).strip(),
        delivery_days=offer["delivery_days"],
        description=offer["description"],
    )


def _order_out(order: dict[str, Any]) -> OrderOut:
    created = datetime.fromisoformat(order["created_at"]).astimezone(timezone.utc)
    return OrderOut(
        order_id=order["id"],
        status=order["status"],
        offer_id=order["offer_id"],
        merchant_id=order["merchant_id"],
        sku=order["sku"],
        quantity=order["quantity"],
        unit_price=Money.from_minor(order["unit_price_minor"], order["currency"]),
        total=Money.from_minor(order["total_minor"], order["currency"]),
        created_at=created.strftime("%Y-%m-%dT%H:%M:%SZ"),
    )


def scenario_payload(scenario_id: str, today: date) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """JSON arguments of ``shops.load_scenario`` (raises ``KeyError`` for an unknown scenario)."""
    scenario = build_scenario(scenario_id, today)
    base = base_merchant_ids()

    def scenario_of(merchant_id: str) -> str | None:
        return None if merchant_id in base else scenario.scenario_id

    merchants = [
        {
            "merchant_id": m.id, "name": m.name, "domain": m.domain, "country": m.country,
            "domain_registered_at": m.domain_registered_at.isoformat(), "verified": m.verified,
            "reputation_score": None if m.reputation_score is None else str(m.reputation_score),
            "reviews_count": m.reputation_reviews_count or 0,
            "offers_table": MERCHANT_TABLES[m.id], "scenario_id": scenario_of(m.id),
        }
        for m in scenario.merchants
    ]
    offers = [
        {
            "offer_id": o.id, "merchant_id": o.merchant_id, "sku": o.sku, "product_name": o.product_name,
            "unit_price": f"{o.unit_price_minor // 100}.{o.unit_price_minor % 100:02d}", "currency": o.currency,
            "available_qty": o.available_qty, "ships_from": o.ships_from, "delivery_days": o.delivery_days,
            "description": o.description, "scenario_id": scenario_of(o.merchant_id), "active": True,
            "offers_table": MERCHANT_TABLES[o.merchant_id],
        }
        for o in scenario.offers
    ]
    return merchants, offers


def _error_code(exc: APIError) -> str:
    return str(exc.code or "")


class SupabaseMarketplaceService:
    def __init__(
        self,
        settings: MarketplaceSettings,
        *,
        clock: Callable[[], datetime] = _utcnow,
        transport: httpx.BaseTransport | None = None,
    ):
        assert settings.supabase_url is not None and settings.supabase_key is not None
        self.settings = settings
        self.clock = clock
        key = settings.supabase_key.get_secret_value()
        headers = {"apikey": key, "Accept": "application/json", "Content-Type": "application/json"}
        if key.count(".") == 2:  # legacy JWT keys are also sent as a bearer token; sb_secret_* keys only as apikey
            headers["Authorization"] = f"Bearer {key}"
        self._http = httpx.Client(
            timeout=settings.supabase_timeout, follow_redirects=True, transport=transport
        )
        base = f"{settings.supabase_url}/rest/v1"
        self._shops = SyncPostgrestClient(base, schema=MARKETPLACE_SCHEMA, headers=headers, http_client=self._http)
        self._warehouse = SyncPostgrestClient(base, schema=SUPPLIERS_SCHEMA, headers=headers, http_client=self._http)

    def close(self) -> None:
        self._http.close()

    # ------------------------------------------------------------------ plumbing
    def _run(self, fn: Callable[[], T]) -> T:
        try:
            return fn()
        except MarketplaceError:
            raise
        except APIError as exc:
            code = _error_code(exc)
            if code == "P0001" and exc.message in _BUSINESS_ERRORS:
                raise MarketplaceError(exc.message) from None  # type: ignore[arg-type]
            if code in _UNAVAILABLE_CODES:
                logger.error("supabase unavailable", extra={"api_code": code})
                raise MarketplaceError("db_unavailable") from None
            logger.error("supabase request failed", extra={"api_code": code, "api_message": exc.message})
            raise MarketplaceError("internal_error") from None
        except httpx.HTTPError as exc:
            logger.error("supabase unreachable", extra={"exc_type": type(exc).__name__})
            raise MarketplaceError("db_unavailable") from None

    # ------------------------------------------------------------------ health
    def readiness(self) -> dict[str, Any]:
        """Real check: REST API reachable, schemas exposed, schema at the expected revision, a scenario loaded."""

        def fn() -> dict[str, Any]:
            try:
                rows = self._shops.from_("alembic_version").select("version_num").execute().data
                merchants = (
                    self._warehouse.from_("suppliers")
                    .select("merchant_id", count="exact")
                    .in_("merchant_id", list(MERCHANT_TABLES))
                    .limit(1)
                    .execute()
                    .count
                    or 0
                )
            except APIError as exc:
                code = _error_code(exc)
                if code in _HINTS:
                    raise MarketplaceError("not_ready", _HINTS[code]) from None
                raise
            revision = rows[0]["version_num"] if rows else None
            if revision != MARKETPLACE_HEAD_REVISION:
                raise MarketplaceError("not_ready", "Database schema revision mismatch.")
            if merchants == 0:
                raise MarketplaceError(
                    "not_ready", "No scenario loaded; run the seed or POST /admin/scenarios/happy_path/load."
                )
            return {"status": "ready", "schema_revision": revision}

        return self._run(fn)

    # ------------------------------------------------------------------ reads
    def _merchants(self, ids: list[str]) -> dict[str, dict[str, Any]]:
        if not ids:
            return {}
        rows = self._warehouse.from_("suppliers").select("*").in_("merchant_id", ids).execute().data
        return {row["merchant_id"]: row for row in rows}

    def search(self, *, sku: str | None, q: str | None, limit: int) -> SearchResponse:
        def fn() -> SearchResponse:
            query = self._shops.from_("offers").select("*", count="exact").eq("active", True)
            if sku is not None:
                query = query.eq("sku", sku)
            if q is not None:
                query = query.ilike("product_name", _like_pattern(q))
            response = query.order("unit_price").order("offer_id").limit(limit).execute()
            merchants = self._merchants(sorted({row["merchant_id"] for row in response.data}))
            offers = [_offer_out(row, merchants[row["merchant_id"]]) for row in response.data if row["merchant_id"] in merchants]
            return SearchResponse(offers=offers, total=response.count or 0)

        return self._run(fn)

    def get_offer(self, offer_id: str) -> OfferOut:
        def fn() -> OfferOut:
            rows = self._shops.from_("offers").select("*").eq("offer_id", offer_id).eq("active", True).limit(1).execute().data
            merchants = self._merchants([rows[0]["merchant_id"]]) if rows else {}
            if not rows or rows[0]["merchant_id"] not in merchants:
                raise MarketplaceError("offer_not_found", f"Offer {offer_id} does not exist")
            return _offer_out(rows[0], merchants[rows[0]["merchant_id"]])

        return self._run(fn)

    def get_merchant(self, merchant_id: str) -> MerchantOut:
        def fn() -> MerchantOut:
            m = self._merchants([merchant_id]).get(merchant_id)
            if m is None:
                raise MarketplaceError("merchant_not_found", f"Merchant {merchant_id} does not exist")
            reputation = None
            if m["reputation_score"] is not None:
                reputation = Reputation(score=float(m["reputation_score"]), reviews_count=m["reviews_count"])
            return MerchantOut(
                id=m["merchant_id"], name=m["name"], domain=m["domain"], country=str(m["country"]).strip(),
                domain_registered_at=m["domain_registered_at"], verified=m["verified"], reputation=reputation,
            )

        return self._run(fn)

    # ------------------------------------------------------------------ orders
    def create_order(self, idempotency_key: str, body: OrderRequest, ctx: CallerContext) -> OrderOut:
        params = {
            "p_idempotency_key": idempotency_key,
            "p_fingerprint": _fingerprint(body),
            "p_offer_id": body.offer_id,
            "p_quantity": body.quantity,
            "p_expected_minor": body.expected_unit_price.minor,
            "p_expected_currency": body.expected_unit_price.currency,
            "p_decrement_stock": self.settings.marketplace_decrement_stock,
            "p_request_id": ctx.request_id,
            "p_on_behalf_of": ctx.on_behalf_of,
        }

        def fn() -> OrderOut:
            order = self._shops.rpc("create_order", params).execute().data
            result = _order_out(order)
            audit_logger.info(
                "order_created",
                extra={
                    "order_id": result.order_id, "offer_id": result.offer_id, "merchant_id": result.merchant_id,
                    "quantity": result.quantity, "on_behalf_of": ctx.on_behalf_of,
                },
            )
            return result

        return self._run(fn)

    # ------------------------------------------------------------------ demo
    def load_scenario(self, scenario_id: str) -> ScenarioLoadOut:
        try:
            merchants, offers = scenario_payload(scenario_id, self.clock().date())
        except KeyError:
            raise MarketplaceError("scenario_not_found", f"Scenario {scenario_id} does not exist") from None

        def fn() -> ScenarioLoadOut:
            result = self._shops.rpc(
                "load_scenario", {"p_scenario_id": scenario_id, "p_merchants": merchants, "p_offers": offers}
            ).execute().data
            audit_logger.info("scenario_loaded", extra=result)
            return ScenarioLoadOut(**result)

        return self._run(fn)
