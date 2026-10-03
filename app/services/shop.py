"""The single business-logic layer used by BOTH the REST routers and the MCP tools.

Every public method takes the authenticated ``customer_id`` (never a client-supplied identity) and runs in
its own Postgres transaction. There are no internal HTTP calls and no in-memory state.
"""

from __future__ import annotations

import hashlib
import json
import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Literal, Sequence, TypeVar

from sqlalchemy import delete, func, select, text, update
from sqlalchemy import exc as sa_exc
from sqlalchemy.orm import Session

from app.config import Settings
from app.db.models import (
    ApiKey,
    AuditEvent,
    Cart,
    CartItem,
    CheckoutQuote,
    CheckoutQuoteItem,
    IdempotencyRecord,
    MockPayment,
    Order,
    OrderItem,
    Product,
    ShippingMethod,
    order_number_seq,
)
from app.db.revision import HEAD_REVISION
from app.db.session import Database
from app.errors import AppError
from app.repositories import carts as cart_repo
from app.repositories import catalog as catalog_repo
from app.repositories import orders as order_repo
from app.request_context import correlation_id_var, request_id_var
from app.schemas.cart import CartOut
from app.schemas.catalog import CategoryOut, ProductOut, ProductPage, ShippingMethodOut, StoreInfo
from app.schemas.checkout import CheckoutOut, OrderOut, OrderPage, QuoteOut, QuoteRequest
from app.security import hash_api_key
from app.services.payment import MockPaymentProvider, PaymentProvider

logger = logging.getLogger("shop.service")
audit_logger = logging.getLogger("shop.audit")

T = TypeVar("T")
_DB_DOWN = (sa_exc.OperationalError, sa_exc.InterfaceError, sa_exc.TimeoutError)


@dataclass
class AuditRefs:
    cart_id: uuid.UUID | None = None
    quote_id: uuid.UUID | None = None
    order_id: uuid.UUID | None = None
    details: dict[str, Any] = field(default_factory=dict)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class ShopService:
    def __init__(
        self,
        settings: Settings,
        db: Database,
        *,
        payment: PaymentProvider | None = None,
        clock: Callable[[], datetime] = _utcnow,
    ):
        self.settings = settings
        self.shop = settings.shop
        self.db = db
        self.payment: PaymentProvider = payment or MockPaymentProvider(settings.mock_payment_mode)
        self.clock = clock

    # ------------------------------------------------------------------ plumbing
    def _guard(self, fn: Callable[[], T]) -> T:
        try:
            return fn()
        except AppError:
            raise
        except _DB_DOWN as exc:
            logger.error("database unavailable", extra={"exc_type": type(exc).__name__})
            raise AppError("DB_UNAVAILABLE") from None
        except sa_exc.DBAPIError as exc:
            if exc.connection_invalidated:
                raise AppError("DB_UNAVAILABLE") from None
            raise

    def _read(self, fn: Callable[[Session], T]) -> T:
        def run() -> T:
            with self.db.begin() as session:
                return fn(session)

        return self._guard(run)

    def _write(
        self,
        operation: str,
        customer_id: uuid.UUID | None,
        fn: Callable[[Session, AuditRefs], T],
        refs: AuditRefs | None = None,
    ) -> T:
        refs = refs or AuditRefs()

        def run() -> T:
            with self.db.begin() as session:
                result = fn(session, refs)
                self._audit(session, operation, customer_id, refs, "success", None)
                return result

        try:
            return self._guard(run)
        except AppError as exc:
            if exc.code != "DB_UNAVAILABLE":
                self._audit_failure(operation, customer_id, refs, exc.code)
            raise

    def _audit(
        self,
        session: Session,
        operation: str,
        customer_id: uuid.UUID | None,
        refs: AuditRefs,
        result: Literal["success", "error"],
        error_code: str | None,
    ) -> None:
        session.add(
            AuditEvent(
                store_id=self.shop.shop_id,
                customer_id=customer_id,
                operation=operation,
                request_id=request_id_var.get(),
                correlation_id=correlation_id_var.get(),
                cart_id=refs.cart_id,
                quote_id=refs.quote_id,
                order_id=refs.order_id,
                result=result,
                error_code=error_code,
                details=refs.details,
            )
        )
        audit_logger.info(
            "mutation",
            extra={
                "store_id": self.shop.shop_id,
                "customer_id": str(customer_id) if customer_id else None,
                "operation": operation,
                "cart_id": str(refs.cart_id) if refs.cart_id else None,
                "quote_id": str(refs.quote_id) if refs.quote_id else None,
                "order_id": str(refs.order_id) if refs.order_id else None,
                "result": result,
                "error_code": error_code,
            },
        )

    def _audit_failure(self, operation: str, customer_id: uuid.UUID | None, refs: AuditRefs, code: str) -> None:
        """Failures roll the business transaction back, so they are audited in a separate one."""
        refs.order_id = None
        try:
            with self.db.begin() as session:
                self._audit(session, operation, customer_id, refs, "error", code)
        except Exception as exc:  # noqa: BLE001 - auditing must never mask the original error
            logger.error("could not persist failure audit event", extra={"exc_type": type(exc).__name__})

    def _check_quantity(self, quantity: int) -> None:
        if isinstance(quantity, bool) or not isinstance(quantity, int) or not 1 <= quantity <= self.settings.max_line_quantity:
            raise AppError(
                "INVALID_QUANTITY",
                details={"quantity": quantity if isinstance(quantity, int) else None,
                         "min": 1, "max": self.settings.max_line_quantity},
            )

    # ------------------------------------------------------------------ auth / health
    def authenticate(self, token: str | None) -> uuid.UUID:
        if not token or len(token) > 256:
            raise AppError("UNAUTHORIZED")
        key_hash = hash_api_key(token)

        def fn(session: Session) -> uuid.UUID | None:
            return session.scalar(select(ApiKey.customer_id).where(ApiKey.key_hash == key_hash, ApiKey.active.is_(True)))

        customer_id = self._read(fn)
        if customer_id is None:
            raise AppError("UNAUTHORIZED")
        return customer_id

    def readiness(self) -> dict[str, Any]:
        """Real check: database reachable, schema at the expected migration revision, store configured."""

        def fn(session: Session) -> dict[str, Any]:
            session.execute(text("SELECT 1"))
            try:
                revision = session.execute(text(f'SELECT version_num FROM "{self.shop.schema}".alembic_version')).scalar()
            except sa_exc.ProgrammingError:
                raise AppError("NOT_READY", "Database schema is not migrated.") from None
            if revision != HEAD_REVISION:
                raise AppError("NOT_READY", "Database schema revision mismatch.",
                               details={"expected": HEAD_REVISION, "actual": revision})
            config = catalog_repo.get_store_config(session)
            if config is None or config.store_id != self.shop.shop_id:
                raise AppError("NOT_READY", "Store configuration is missing; run the seed.")
            return {"status": "ready", "store_id": config.store_id, "schema_revision": revision}

        return self._read(fn)

    # ------------------------------------------------------------------ catalog
    def get_store_info(self) -> StoreInfo:
        def fn(session: Session) -> StoreInfo:
            config = catalog_repo.get_store_config(session)
            if config is None:
                raise AppError("NOT_READY", "Store configuration is missing; run the seed.")
            return StoreInfo(
                store_id=config.store_id,
                name=config.name,
                store_country=config.country,
                currency=config.currency,
                locale=config.locale,
                catalog_language=config.locale.split("-")[0],
                quote_ttl_seconds=self.settings.quote_ttl_seconds,
                max_cart_lines=self.settings.max_cart_lines,
                max_line_quantity=self.settings.max_line_quantity,
            )

        return self._read(fn)

    def list_categories(self) -> list[CategoryOut]:
        return self._read(catalog_repo.list_categories)

    def search_products(
        self,
        *,
        q: str | None = None,
        category: str | None = None,
        availability: Literal["in_stock", "out_of_stock"] | None = None,
        origin_countries: Sequence[str] | None = None,
        sort: catalog_repo.SortKey = "name",
        limit: int = 20,
        offset: int = 0,
    ) -> ProductPage:
        limit = max(1, min(limit, 100))
        offset = max(0, min(offset, 100_000))
        return self._read(
            lambda s: catalog_repo.search_products(
                s, q=q, category=category, availability=availability, origin_countries=origin_countries,
                sort=sort, limit=limit, offset=offset,
            )
        )

    def get_product(self, sku: str) -> ProductOut:
        def fn(session: Session) -> ProductOut:
            product = catalog_repo.get_product_by_sku(session, sku)
            if product is None:
                raise AppError("PRODUCT_NOT_FOUND", details={"sku": sku})
            return catalog_repo.product_to_out(product)

        return self._read(fn)

    def list_shipping_methods(self) -> list[ShippingMethodOut]:
        return self._read(lambda s: [catalog_repo.shipping_to_out(m) for m in catalog_repo.list_shipping_methods(s)])

    # ------------------------------------------------------------------ carts
    def create_cart(self, customer_id: uuid.UUID) -> CartOut:
        def fn(session: Session, refs: AuditRefs) -> CartOut:
            now = self.clock()
            cart = Cart(customer_id=customer_id, currency=self.shop.currency, status="open", version=1,
                        created_at=now, updated_at=now)
            session.add(cart)
            session.flush()
            refs.cart_id = cart.id
            return cart_repo.cart_to_out(session, cart, self.shop.shop_id)

        return self._write("cart.create", customer_id, fn)

    def get_cart(self, customer_id: uuid.UUID, cart_id: uuid.UUID) -> CartOut:
        def fn(session: Session) -> CartOut:
            cart = cart_repo.get_cart(session, cart_id, customer_id)
            return cart_repo.cart_to_out(session, cart, self.shop.shop_id)

        return self._read(fn)

    @staticmethod
    def _require_open_cart(cart: Cart, expected_version: int | None) -> None:
        if cart.status != "open":
            raise AppError("CART_ALREADY_CHECKED_OUT", details={"cart_id": str(cart.id)})
        if expected_version is not None and expected_version != cart.version:
            raise AppError("CART_VERSION_CONFLICT",
                           details={"expected_version": expected_version, "current_version": cart.version})

    def set_cart_item(
        self, customer_id: uuid.UUID, cart_id: uuid.UUID, sku: str, quantity: int, expected_version: int | None = None
    ) -> CartOut:
        """Set the ABSOLUTE quantity of a line (creates it when missing). Retrying is idempotent."""

        def fn(session: Session, refs: AuditRefs) -> CartOut:
            refs.cart_id = cart_id
            refs.details = {"sku": sku, "quantity": quantity if isinstance(quantity, int) else None}
            self._check_quantity(quantity)
            cart = cart_repo.get_cart(session, cart_id, customer_id, lock=True)
            self._require_open_cart(cart, expected_version)
            product = catalog_repo.get_product_by_sku(session, sku)
            if product is None:
                raise AppError("PRODUCT_NOT_FOUND", details={"sku": sku})
            if not product.active:
                raise AppError("PRODUCT_INACTIVE", details={"sku": sku})
            if product.stock_quantity < quantity:
                raise AppError("INSUFFICIENT_STOCK", details={
                    "sku": sku, "requested": quantity, "available": product.stock_quantity})
            now = self.clock()
            item = session.get(CartItem, (cart.id, product.id))
            changed = False
            if item is None:
                lines = session.scalar(select(func.count()).select_from(CartItem).where(CartItem.cart_id == cart.id)) or 0
                if lines >= self.settings.max_cart_lines:
                    raise AppError("CART_LINE_LIMIT", details={"max_lines": self.settings.max_cart_lines})
                session.add(CartItem(cart_id=cart.id, product_id=product.id, quantity=quantity,
                                     created_at=now, updated_at=now))
                changed = True
            elif item.quantity != quantity:
                item.quantity = quantity
                item.updated_at = now
                changed = True
            if changed:
                cart.version += 1
                cart.updated_at = now
            session.flush()
            return cart_repo.cart_to_out(session, cart, self.shop.shop_id)

        return self._write("cart.set_item", customer_id, fn)

    def remove_cart_item(
        self, customer_id: uuid.UUID, cart_id: uuid.UUID, sku: str, expected_version: int | None = None
    ) -> CartOut:
        def fn(session: Session, refs: AuditRefs) -> CartOut:
            refs.cart_id = cart_id
            refs.details = {"sku": sku}
            cart = cart_repo.get_cart(session, cart_id, customer_id, lock=True)
            self._require_open_cart(cart, expected_version)
            product = catalog_repo.get_product_by_sku(session, sku)
            if product is None:
                raise AppError("PRODUCT_NOT_FOUND", details={"sku": sku})
            result = session.execute(
                delete(CartItem).where(CartItem.cart_id == cart.id, CartItem.product_id == product.id)
            )
            if result.rowcount:
                cart.version += 1
                cart.updated_at = self.clock()
            session.flush()
            return cart_repo.cart_to_out(session, cart, self.shop.shop_id)

        return self._write("cart.remove_item", customer_id, fn)

    def clear_cart(self, customer_id: uuid.UUID, cart_id: uuid.UUID, expected_version: int | None = None) -> CartOut:
        def fn(session: Session, refs: AuditRefs) -> CartOut:
            refs.cart_id = cart_id
            cart = cart_repo.get_cart(session, cart_id, customer_id, lock=True)
            self._require_open_cart(cart, expected_version)
            result = session.execute(delete(CartItem).where(CartItem.cart_id == cart.id))
            if result.rowcount:
                cart.version += 1
                cart.updated_at = self.clock()
            session.flush()
            return cart_repo.cart_to_out(session, cart, self.shop.shop_id)

        return self._write("cart.clear", customer_id, fn)

    # ------------------------------------------------------------------ quotes
    def create_checkout_quote(self, customer_id: uuid.UUID, cart_id: uuid.UUID, request: QuoteRequest) -> QuoteOut:
        """Persist an immutable priced snapshot with a TTL. Does not buy, pay or touch stock."""

        def fn(session: Session, refs: AuditRefs) -> QuoteOut:
            refs.cart_id = cart_id
            cart = cart_repo.get_cart(session, cart_id, customer_id, lock=True)
            self._require_open_cart(cart, request.expected_cart_version)
            rows = session.execute(
                select(CartItem, Product)
                .join(Product, Product.id == CartItem.product_id)
                .where(CartItem.cart_id == cart.id)
                .order_by(Product.sku)
            ).unique().all()
            if not rows:
                raise AppError("EMPTY_CART", details={"cart_id": str(cart.id)})
            for item, product in rows:
                if not product.active:
                    raise AppError("PRODUCT_INACTIVE", details={"sku": product.sku})
                if product.stock_quantity < item.quantity:
                    raise AppError("INSUFFICIENT_STOCK", details={
                        "sku": product.sku, "requested": item.quantity, "available": product.stock_quantity})

            methods = {m.code: m for m in catalog_repo.list_shipping_methods(session)}
            if request.shipping_method_code is None:
                if not methods:
                    raise AppError("SHIPPING_METHOD_NOT_FOUND")
                method = next(iter(methods.values()))
            else:
                method = methods.get(request.shipping_method_code)
                if method is None:
                    raise AppError("SHIPPING_METHOD_NOT_FOUND",
                                   details={"code": request.shipping_method_code, "available": list(methods)})

            now = self.clock()
            subtotal = sum(p.unit_gross_minor * i.quantity for i, p in rows)
            shipping = method.price_gross_minor
            quote = CheckoutQuote(
                cart_id=cart.id,
                cart_version=cart.version,
                customer_id=customer_id,
                store_id=self.shop.shop_id,
                created_at=now,
                expires_at=now + timedelta(seconds=self.settings.quote_ttl_seconds),
                shipping_method_id=method.id,
                shipping_method_code=method.code,
                shipping_method_name=method.name,
                shipping_address=request.shipping_address.model_dump(mode="json"),
                currency=cart.currency,
                subtotal_gross_minor=subtotal,
                shipping_gross_minor=shipping,
                total_gross_minor=subtotal + shipping,
                origin_countries=sorted({p.country_of_origin for _, p in rows}),
            )
            quote.items = [
                CheckoutQuoteItem(
                    line_no=n,
                    product_id=p.id,
                    sku=p.sku,
                    canonical_item_code=p.canonical_item_code,
                    name=p.name,
                    manufacturer_name=p.manufacturer_name,
                    unit_label=p.unit_label,
                    units_per_pack=p.units_per_pack,
                    quantity=i.quantity,
                    unit_gross_minor=p.unit_gross_minor,
                    line_total_gross_minor=p.unit_gross_minor * i.quantity,
                    product_version=p.version,
                    country_of_origin=p.country_of_origin,
                )
                for n, (i, p) in enumerate(rows, start=1)
            ]
            session.add(quote)
            session.flush()
            refs.quote_id = quote.id
            refs.details = {"origin_countries": quote.origin_countries, "total_gross_minor": quote.total_gross_minor}
            return order_repo.quote_to_out(quote, now)

        return self._write("quote.create", customer_id, fn)

    def get_checkout_quote(self, customer_id: uuid.UUID, quote_id: uuid.UUID) -> QuoteOut:
        def fn(session: Session) -> QuoteOut:
            return order_repo.quote_to_out(order_repo.get_quote(session, quote_id, customer_id), self.clock())

        return self._read(fn)

    # ------------------------------------------------------------------ checkout
    def checkout(self, customer_id: uuid.UUID, quote_id: uuid.UUID, idempotency_key: str) -> CheckoutOut:
        """One Postgres transaction: idempotency, ownership, validation, locks, order + snapshot, mock payment,
        stock decrement, cart close, idempotency record and audit event. Any failure rolls everything back."""
        fingerprint = hashlib.sha256(json.dumps({"quote_id": str(quote_id)}, sort_keys=True).encode()).hexdigest()
        operation = "checkout"

        def fn(session: Session, refs: AuditRefs) -> CheckoutOut:
            refs.quote_id = quote_id
            # Serialise concurrent requests that carry the same idempotency key.
            session.execute(
                select(func.pg_advisory_xact_lock(func.hashtextextended(f"{customer_id}:{operation}:{idempotency_key}", 0)))
            )
            record = session.scalars(
                select(IdempotencyRecord).where(
                    IdempotencyRecord.customer_id == customer_id,
                    IdempotencyRecord.operation == operation,
                    IdempotencyRecord.idempotency_key == idempotency_key,
                )
            ).first()
            if record is not None:
                if record.request_fingerprint != fingerprint:
                    raise AppError("IDEMPOTENCY_CONFLICT")
                order = order_repo.get_order(session, record.order_id, customer_id)
                refs.order_id = order.id
                refs.cart_id = order.cart_id
                refs.details = {"idempotent_replay": True}
                return CheckoutOut(order=order_repo.order_to_out(order, self.shop.shop_id), idempotent_replay=True)

            quote = order_repo.get_quote(session, quote_id, customer_id)
            refs.cart_id = quote.cart_id
            cart = cart_repo.get_cart(session, quote.cart_id, customer_id, lock=True)
            if cart.status != "open":
                raise AppError("CART_ALREADY_CHECKED_OUT", details={"cart_id": str(cart.id)})
            now = self.clock()
            if now >= quote.expires_at:
                raise AppError("QUOTE_EXPIRED", details={"expired_at": quote.expires_at.isoformat()})

            # Lock products in a stable order (by id) so concurrent checkouts cannot deadlock.
            product_ids = sorted(qi.product_id for qi in quote.items)
            products = {
                p.id: p
                for p in session.scalars(
                    select(Product).where(Product.id.in_(product_ids)).order_by(Product.id).with_for_update(of=Product)
                ).unique()
            }
            method = session.get(ShippingMethod, quote.shipping_method_id)

            reasons: list[str] = []
            if cart.version != quote.cart_version:
                reasons.append("cart_changed")
            for qi in quote.items:
                p = products.get(qi.product_id)
                if p is None or not p.active:
                    reasons.append(f"product_unavailable:{qi.sku}")
                    continue
                if p.version != qi.product_version:
                    reasons.append(f"product_changed:{qi.sku}")
                if p.unit_gross_minor != qi.unit_gross_minor:
                    reasons.append(f"price_changed:{qi.sku}")
                if p.country_of_origin != qi.country_of_origin:
                    reasons.append(f"origin_changed:{qi.sku}")
            if method is None or not method.active or method.price_gross_minor != quote.shipping_gross_minor:
                reasons.append("shipping_changed")
            if reasons:
                raise AppError("QUOTE_STALE", details={"reasons": sorted(set(reasons)), "quote_id": str(quote.id)})

            shortages = [
                {"sku": qi.sku, "requested": qi.quantity, "available": products[qi.product_id].stock_quantity}
                for qi in quote.items
                if products[qi.product_id].stock_quantity < qi.quantity
            ]
            if shortages:
                raise AppError("INSUFFICIENT_STOCK", details={"items": shortages})

            number = session.scalar(order_number_seq.next_value())
            order = Order(
                order_number=f"{self.shop.order_prefix}-{now.year}-{number:08d}",
                customer_id=customer_id,
                quote_id=quote.id,
                cart_id=cart.id,
                status="placed",
                payment_status="paid_mock",
                currency=quote.currency,
                subtotal_gross_minor=quote.subtotal_gross_minor,
                shipping_gross_minor=quote.shipping_gross_minor,
                total_gross_minor=quote.total_gross_minor,
                shipping_method_code=quote.shipping_method_code,
                shipping_method_name=quote.shipping_method_name,
                shipping_address=quote.shipping_address,
                origin_countries=list(quote.origin_countries),
                created_at=now,
                updated_at=now,
            )
            order.items = [
                OrderItem(
                    line_no=qi.line_no, product_id=qi.product_id, sku=qi.sku,
                    canonical_item_code=qi.canonical_item_code, name=qi.name,
                    manufacturer_name=qi.manufacturer_name, unit_label=qi.unit_label,
                    units_per_pack=qi.units_per_pack, quantity=qi.quantity,
                    unit_gross_minor=qi.unit_gross_minor, line_total_gross_minor=qi.line_total_gross_minor,
                    product_version=qi.product_version, country_of_origin=qi.country_of_origin,
                )
                for qi in quote.items
            ]
            session.add(order)
            session.flush()

            payment = self.payment.charge(
                amount_gross_minor=order.total_gross_minor, currency=order.currency, reference=order.order_number
            )
            if not payment.approved:
                raise AppError("PAYMENT_DECLINED_MOCK", details={"reason": payment.decline_reason})
            session.add(
                MockPayment(
                    order_id=order.id, customer_id=customer_id, provider_reference=payment.reference,
                    amount_gross_minor=order.total_gross_minor, currency=order.currency, status="succeeded",
                    created_at=now,
                )
            )

            for qi in sorted(quote.items, key=lambda x: x.product_id):
                result = session.execute(
                    update(Product)
                    .where(Product.id == qi.product_id, Product.stock_quantity >= qi.quantity)
                    .values(stock_quantity=Product.stock_quantity - qi.quantity)
                    .execution_options(synchronize_session=False)
                )
                if result.rowcount != 1:  # defensive: rows are locked, this should be unreachable
                    raise AppError("INSUFFICIENT_STOCK", details={"sku": qi.sku})

            cart.status = "checked_out"
            cart.version += 1
            cart.checked_out_at = now
            cart.updated_at = now
            session.add(
                IdempotencyRecord(
                    customer_id=customer_id, operation=operation, idempotency_key=idempotency_key,
                    request_fingerprint=fingerprint, order_id=order.id,
                    result={"order_id": str(order.id), "order_number": order.order_number},
                )
            )
            session.flush()
            refs.order_id = order.id
            refs.details = {"origin_countries": order.origin_countries, "total_gross_minor": order.total_gross_minor,
                            "idempotent_replay": False}
            return CheckoutOut(order=order_repo.order_to_out(order, self.shop.shop_id), idempotent_replay=False)

        return self._write(operation, customer_id, fn)

    # ------------------------------------------------------------------ orders
    def list_orders(self, customer_id: uuid.UUID, limit: int = 20, offset: int = 0) -> OrderPage:
        limit = max(1, min(limit, 100))
        offset = max(0, min(offset, 100_000))
        return self._read(lambda s: order_repo.list_orders(s, customer_id, self.shop.shop_id, limit, offset))

    def get_order(self, customer_id: uuid.UUID, order_id: uuid.UUID) -> OrderOut:
        return self._read(lambda s: order_repo.order_to_out(order_repo.get_order(s, order_id, customer_id), self.shop.shop_id))
