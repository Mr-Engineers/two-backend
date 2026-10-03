"""Marketplace business logic (search, offers, merchants, idempotent orders, demo scenarios)."""

from __future__ import annotations

import hashlib
import json
import logging
import secrets
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable, TypeVar

from sqlalchemy import exc as sa_exc
from sqlalchemy import func, select, text, update
from sqlalchemy.orm import Session

from app.db.session import Database
from app.marketplace import MARKETPLACE_HEAD_REVISION, MARKETPLACE_SCHEMA
from app.marketplace.config import MarketplaceSettings
from app.marketplace.errors import MarketplaceError
from app.marketplace.models import MERCHANT_TABLES, IdempotencyRecord, Merchant, Offer, Order, offers_table
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
from app.marketplace.seed import load_scenario

logger = logging.getLogger("marketplace.service")
audit_logger = logging.getLogger("marketplace.audit")

T = TypeVar("T")
_DB_DOWN = (sa_exc.OperationalError, sa_exc.InterfaceError, sa_exc.TimeoutError)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


@dataclass(frozen=True)
class CallerContext:
    """Informational headers from the proxy (logged, never used for authorisation)."""

    request_id: str | None = None
    on_behalf_of: str | None = None


def _like_pattern(term: str) -> str:
    escaped = term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


def _offer_out(offer: Offer, merchant: Merchant) -> OfferOut:
    return OfferOut(
        offer_id=offer.id,
        merchant=MerchantRef(id=merchant.id, name=merchant.name, domain=merchant.domain),
        product=ProductRef(sku=offer.sku, name=offer.product_name),
        unit_price=Money.from_minor(offer.unit_price_minor, offer.currency.strip()),
        available_qty=offer.available_qty,
        ships_from=offer.ships_from,
        delivery_days=offer.delivery_days,
        description=offer.description,
    )


def _order_out(order: Order) -> OrderOut:
    return OrderOut(
        order_id=order.id,
        status=order.status,
        offer_id=order.offer_id,
        merchant_id=order.merchant_id,
        sku=order.sku,
        quantity=order.quantity,
        unit_price=Money.from_minor(order.unit_price_minor, order.currency),
        total=Money.from_minor(order.total_minor, order.currency),
        created_at=order.created_at.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    )


def _fingerprint(body: OrderRequest) -> str:
    canonical = json.dumps(
        {
            "offer_id": body.offer_id,
            "quantity": body.quantity,
            "expected_unit_price": {
                "amount": body.expected_unit_price.amount,
                "currency": body.expected_unit_price.currency,
            },
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class MarketplaceService:
    def __init__(
        self,
        settings: MarketplaceSettings,
        db: Database,
        *,
        clock: Callable[[], datetime] = _utcnow,
    ):
        self.settings = settings
        self.db = db
        self.clock = clock

    # ------------------------------------------------------------------ plumbing
    def _run(self, fn: Callable[[Session], T]) -> T:
        try:
            with self.db.begin() as session:
                return fn(session)
        except MarketplaceError:
            raise
        except _DB_DOWN as exc:
            logger.error("database unavailable", extra={"exc_type": type(exc).__name__})
            raise MarketplaceError("db_unavailable") from None
        except sa_exc.DBAPIError as exc:
            if exc.connection_invalidated:
                raise MarketplaceError("db_unavailable") from None
            raise

    # ------------------------------------------------------------------ health
    def readiness(self) -> dict[str, Any]:
        """Real check: database reachable, schema at the expected revision, a scenario loaded."""

        def fn(session: Session) -> dict[str, Any]:
            session.execute(text("SELECT 1"))
            try:
                revision = session.execute(
                    text(f'SELECT version_num FROM "{MARKETPLACE_SCHEMA}".alembic_version')
                ).scalar()
            except sa_exc.ProgrammingError:
                raise MarketplaceError("not_ready", "Database schema is not migrated.") from None
            if revision != MARKETPLACE_HEAD_REVISION:
                raise MarketplaceError("not_ready", "Database schema revision mismatch.")
            merchants = (
                session.scalar(
                    select(func.count()).select_from(Merchant).where(Merchant.id.in_(list(MERCHANT_TABLES)))
                )
                or 0
            )
            if merchants == 0:
                raise MarketplaceError("not_ready", "No scenario loaded; run the seed or POST /admin/scenarios/happy_path/load.")
            return {"status": "ready", "schema_revision": revision}

        return self._run(fn)

    # ------------------------------------------------------------------ reads
    def search(self, *, sku: str | None, q: str | None, limit: int) -> SearchResponse:
        def fn(session: Session) -> SearchResponse:
            stmt = select(Offer, Merchant).join(Merchant, Offer.merchant_id == Merchant.id).where(Offer.active.is_(True))
            if sku is not None:
                stmt = stmt.where(Offer.sku == sku)
            if q is not None:
                stmt = stmt.where(Offer.product_name.ilike(_like_pattern(q), escape="\\"))
            total = session.scalar(select(func.count()).select_from(stmt.subquery())) or 0
            rows = session.execute(stmt.order_by(Offer.unit_price, Offer.id).limit(limit)).all()
            return SearchResponse(offers=[_offer_out(o, m) for o, m in rows], total=total)

        return self._run(fn)

    def get_offer(self, offer_id: str) -> OfferOut:
        def fn(session: Session) -> OfferOut:
            row = session.execute(
                select(Offer, Merchant)
                .join(Merchant, Offer.merchant_id == Merchant.id)
                .where(Offer.id == offer_id, Offer.active.is_(True))
            ).first()
            if row is None:
                raise MarketplaceError("offer_not_found", f"Offer {offer_id} does not exist")
            return _offer_out(*row)

        return self._run(fn)

    def get_merchant(self, merchant_id: str) -> MerchantOut:
        def fn(session: Session) -> MerchantOut:
            m = session.get(Merchant, merchant_id)
            if m is None:
                raise MarketplaceError("merchant_not_found", f"Merchant {merchant_id} does not exist")
            reputation = None
            if m.reputation_score is not None:
                reputation = Reputation(score=float(m.reputation_score), reviews_count=m.reviews_count)
            return MerchantOut(
                id=m.id, name=m.name, domain=m.domain, country=m.country,
                domain_registered_at=m.domain_registered_at, verified=m.verified, reputation=reputation,
            )

        return self._run(fn)

    # ------------------------------------------------------------------ orders
    def create_order(self, idempotency_key: str, body: OrderRequest, ctx: CallerContext) -> OrderOut:
        fingerprint = _fingerprint(body)

        def fn(session: Session) -> OrderOut:
            # Serialise requests with the same key so a retry racing the original cannot create a second order.
            session.execute(text("SELECT pg_advisory_xact_lock(hashtextextended(:k, 0))"), {"k": idempotency_key})
            record = session.get(IdempotencyRecord, idempotency_key)
            if record is not None:
                if record.request_fingerprint != fingerprint:
                    raise MarketplaceError("idempotency_conflict")
                existing = session.get(Order, record.order_id)
                assert existing is not None  # FK guarantees it
                return _order_out(existing)

            # ``shops.offers`` is a UNION view (no row locks); stock is decremented atomically below.
            offer = session.scalar(select(Offer).where(Offer.id == body.offer_id, Offer.active.is_(True)))
            if offer is None:
                raise MarketplaceError("offer_not_found", f"Offer {body.offer_id} does not exist")
            expected = body.expected_unit_price
            if expected.currency != offer.currency.strip() or expected.minor != offer.unit_price_minor:
                raise MarketplaceError("price_changed")
            if body.quantity > offer.available_qty:
                raise MarketplaceError("insufficient_quantity")
            if self.settings.marketplace_decrement_stock:
                table = offers_table(MERCHANT_TABLES[offer.merchant_id])
                updated = session.execute(
                    update(table)
                    .where(table.c.offer_id == offer.id, table.c.available_qty >= body.quantity)
                    .values(available_qty=table.c.available_qty - body.quantity)
                    .returning(table.c.offer_id)
                ).first()
                if updated is None:  # a concurrent order took the stock
                    raise MarketplaceError("insufficient_quantity")

            created_at = self.clock().astimezone(timezone.utc).replace(microsecond=0)
            order = Order(
                id="ord_" + secrets.token_hex(6),
                status="confirmed",
                offer_id=offer.id,
                merchant_id=offer.merchant_id,
                sku=offer.sku,
                quantity=body.quantity,
                unit_price_minor=offer.unit_price_minor,
                currency=offer.currency,
                total_minor=offer.unit_price_minor * body.quantity,
                created_at=created_at,
                request_id=ctx.request_id,
                on_behalf_of=ctx.on_behalf_of,
            )
            session.add(order)
            session.flush()
            session.add(IdempotencyRecord(idempotency_key=idempotency_key, request_fingerprint=fingerprint, order_id=order.id))
            session.flush()
            audit_logger.info(
                "order_created",
                extra={
                    "order_id": order.id, "offer_id": order.offer_id, "merchant_id": order.merchant_id,
                    "quantity": order.quantity, "on_behalf_of": ctx.on_behalf_of,
                },
            )
            return _order_out(order)

        return self._run(fn)

    # ------------------------------------------------------------------ demo
    def load_scenario(self, scenario_id: str) -> ScenarioLoadOut:
        def run() -> ScenarioLoadOut:
            try:
                result = load_scenario(self.db, scenario_id, self.clock().date())
            except KeyError:
                raise MarketplaceError("scenario_not_found", f"Scenario {scenario_id} does not exist") from None
            audit_logger.info("scenario_loaded", extra=result)
            return ScenarioLoadOut(**result)  # type: ignore[arg-type]

        try:
            return run()
        except MarketplaceError:
            raise
        except _DB_DOWN as exc:
            logger.error("database unavailable", extra={"exc_type": type(exc).__name__})
            raise MarketplaceError("db_unavailable") from None
