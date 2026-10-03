from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.models import CheckoutQuote, Order
from app.errors import AppError
from app.schemas.checkout import (
    LineSnapshot,
    OrderOut,
    OrderPage,
    QuoteOut,
    ShippingAddress,
    ShippingSnapshot,
)
from app.schemas.common import minor_to_decimal


def _line(i) -> LineSnapshot:  # works for CheckoutQuoteItem and OrderItem
    return LineSnapshot(
        line_no=i.line_no,
        sku=i.sku,
        product_id=i.product_id,
        canonical_item_code=i.canonical_item_code,
        name=i.name,
        manufacturer_name=i.manufacturer_name,
        unit_label=i.unit_label,
        units_per_pack=i.units_per_pack,
        quantity=i.quantity,
        unit_gross_minor=i.unit_gross_minor,
        line_total_gross_minor=i.line_total_gross_minor,
        product_version=i.product_version,
        country_of_origin=i.country_of_origin,
    )


def _shipping(code: str, name: str, price: int, currency: str, address: dict) -> ShippingSnapshot:
    return ShippingSnapshot(
        method_code=code,
        method_name=name,
        price_gross_minor=price,
        price_gross_decimal=minor_to_decimal(price, currency),
        address=ShippingAddress.model_validate(address),
    )


def quote_to_out(q: CheckoutQuote, now: datetime) -> QuoteOut:
    return QuoteOut(
        quote_id=q.id,
        cart_id=q.cart_id,
        cart_version=q.cart_version,
        customer_id=q.customer_id,
        store_id=q.store_id,
        created_at=q.created_at,
        expires_at=q.expires_at,
        is_expired=now >= q.expires_at,
        currency=q.currency,
        items=[_line(i) for i in q.items],
        shipping=_shipping(q.shipping_method_code, q.shipping_method_name, q.shipping_gross_minor, q.currency, q.shipping_address),
        subtotal_gross_minor=q.subtotal_gross_minor,
        subtotal_gross_decimal=minor_to_decimal(q.subtotal_gross_minor, q.currency),
        shipping_gross_minor=q.shipping_gross_minor,
        total_gross_minor=q.total_gross_minor,
        total_gross_decimal=minor_to_decimal(q.total_gross_minor, q.currency),
        origin_countries=sorted(q.origin_countries),
    )


def order_to_out(o: Order, store_id: str) -> OrderOut:
    return OrderOut(
        order_id=o.id,
        order_number=o.order_number,
        customer_id=o.customer_id,
        store_id=store_id,
        quote_id=o.quote_id,
        cart_id=o.cart_id,
        status=o.status,  # type: ignore[arg-type]
        payment_status=o.payment_status,  # type: ignore[arg-type]
        currency=o.currency,
        items=[_line(i) for i in o.items],
        shipping=_shipping(o.shipping_method_code, o.shipping_method_name, o.shipping_gross_minor, o.currency, o.shipping_address),
        subtotal_gross_minor=o.subtotal_gross_minor,
        subtotal_gross_decimal=minor_to_decimal(o.subtotal_gross_minor, o.currency),
        shipping_gross_minor=o.shipping_gross_minor,
        total_gross_minor=o.total_gross_minor,
        total_gross_decimal=minor_to_decimal(o.total_gross_minor, o.currency),
        origin_countries=sorted(o.origin_countries),
        created_at=o.created_at,
        updated_at=o.updated_at,
    )


def get_quote(session: Session, quote_id: uuid.UUID, customer_id: uuid.UUID) -> CheckoutQuote:
    q = session.scalars(
        select(CheckoutQuote).where(CheckoutQuote.id == quote_id, CheckoutQuote.customer_id == customer_id)
    ).first()
    if q is None:
        raise AppError("QUOTE_NOT_FOUND", details={"quote_id": str(quote_id)})
    return q


def get_order(session: Session, order_id: uuid.UUID, customer_id: uuid.UUID) -> Order:
    o = session.scalars(select(Order).where(Order.id == order_id, Order.customer_id == customer_id)).first()
    if o is None:
        raise AppError("ORDER_NOT_FOUND", details={"order_id": str(order_id)})
    return o


def list_orders(session: Session, customer_id: uuid.UUID, store_id: str, limit: int, offset: int) -> OrderPage:
    total = session.scalar(select(func.count()).select_from(Order).where(Order.customer_id == customer_id)) or 0
    rows = session.scalars(
        select(Order)
        .where(Order.customer_id == customer_id)
        .order_by(Order.created_at.desc(), Order.id.desc())
        .limit(limit)
        .offset(offset)
    ).all()
    return OrderPage(items=[order_to_out(o, store_id) for o in rows], total=total, limit=limit, offset=offset)
