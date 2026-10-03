from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import Cart, CartItem, Product
from app.errors import AppError
from app.schemas.cart import CartLineOut, CartOut
from app.schemas.common import minor_to_decimal


def get_cart(session: Session, cart_id: uuid.UUID, customer_id: uuid.UUID, *, lock: bool = False) -> Cart:
    """Ownership is part of the query: a foreign (or unknown) UUID looks exactly like a missing cart."""
    stmt = select(Cart).where(Cart.id == cart_id, Cart.customer_id == customer_id)
    if lock:
        stmt = stmt.with_for_update()
    cart = session.scalars(stmt).first()
    if cart is None:
        raise AppError("CART_NOT_FOUND", details={"cart_id": str(cart_id)})
    return cart


def cart_to_out(session: Session, cart: Cart, store_id: str) -> CartOut:
    rows = session.execute(
        select(CartItem, Product)
        .join(Product, Product.id == CartItem.product_id)
        .where(CartItem.cart_id == cart.id)
        .order_by(Product.sku)
    ).unique().all()
    lines = [
        CartLineOut(
            sku=p.sku,
            product_id=p.id,
            canonical_item_code=p.canonical_item_code,
            name=p.name,
            manufacturer_name=p.manufacturer_name,
            country_of_origin=p.country_of_origin,
            unit_label=p.unit_label,
            units_per_pack=p.units_per_pack,
            quantity=ci.quantity,
            unit_gross_minor=p.unit_gross_minor,
            line_total_gross_minor=p.unit_gross_minor * ci.quantity,
            product_version=p.version,
            active=p.active,
            available_stock=p.stock_quantity,
        )
        for ci, p in rows
    ]
    subtotal = sum(line.line_total_gross_minor for line in lines)
    return CartOut(
        id=cart.id,
        customer_id=cart.customer_id,
        store_id=store_id,
        status=cart.status,  # type: ignore[arg-type]
        version=cart.version,
        currency=cart.currency,
        items=lines,
        subtotal_gross_minor=subtotal,
        subtotal_gross_decimal=minor_to_decimal(subtotal, cart.currency),
        origin_countries=sorted({line.country_of_origin for line in lines}),
        created_at=cart.created_at,
        updated_at=cart.updated_at,
        checked_out_at=cart.checked_out_at,
    )
