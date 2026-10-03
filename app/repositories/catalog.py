"""Catalog queries (read-only)."""

from __future__ import annotations

from typing import Literal, Sequence

from sqlalchemy import and_, func, or_, select
from sqlalchemy.orm import Session

from app.db.models import Category, Product, ShippingMethod, StoreConfig
from app.schemas.catalog import CategoryOut, ProductOut, ProductPage, ShippingMethodOut
from app.schemas.common import minor_to_decimal

SortKey = Literal["name", "sku", "price_asc", "price_desc"]


def product_to_out(p: Product) -> ProductOut:
    return ProductOut(
        id=p.id,
        sku=p.sku,
        canonical_item_code=p.canonical_item_code,
        name=p.name,
        description=p.description,
        category_slug=p.category.slug,
        category_name=p.category.name,
        manufacturer_name=p.manufacturer_name,
        country_of_origin=p.country_of_origin,
        unit_label=p.unit_label,
        units_per_pack=p.units_per_pack,
        unit_gross_minor=p.unit_gross_minor,
        unit_gross_decimal=minor_to_decimal(p.unit_gross_minor, p.currency),
        currency=p.currency,
        stock_quantity=p.stock_quantity,
        in_stock=p.stock_quantity > 0,
        active=p.active,
        compatible_with_canonical_codes=list(p.compatible_with or []),
        version=p.version,
        created_at=p.created_at,
        updated_at=p.updated_at,
    )


def _escape_like(term: str) -> str:
    return term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def search_products(
    session: Session,
    *,
    q: str | None,
    category: str | None,
    availability: Literal["in_stock", "out_of_stock"] | None,
    origin_countries: Sequence[str] | None,
    sort: SortKey,
    limit: int,
    offset: int,
) -> ProductPage:
    stmt = select(Product).where(Product.active.is_(True))
    if q:
        for term in q.split()[:6]:
            pattern = f"%{_escape_like(term)}%"
            stmt = stmt.where(
                or_(
                    Product.name.ilike(pattern, escape="\\"),
                    Product.sku.ilike(pattern, escape="\\"),
                    Product.canonical_item_code.ilike(pattern, escape="\\"),
                )
            )
    if category:
        stmt = stmt.where(Product.category_id.in_(select(Category.id).where(Category.slug == category)))
    if availability == "in_stock":
        stmt = stmt.where(Product.stock_quantity > 0)
    elif availability == "out_of_stock":
        stmt = stmt.where(Product.stock_quantity == 0)
    if origin_countries:
        stmt = stmt.where(Product.country_of_origin.in_(list(origin_countries)))

    total = session.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    order = {
        "name": (Product.name, Product.sku),
        "sku": (Product.sku,),
        "price_asc": (Product.unit_gross_minor, Product.sku),
        "price_desc": (Product.unit_gross_minor.desc(), Product.sku),
    }[sort]
    rows = session.scalars(stmt.order_by(*order).limit(limit).offset(offset)).unique().all()
    return ProductPage(items=[product_to_out(p) for p in rows], total=total, limit=limit, offset=offset)


def get_product_by_sku(session: Session, sku: str) -> Product | None:
    return session.scalars(select(Product).where(Product.sku == sku)).unique().first()


def list_categories(session: Session) -> list[CategoryOut]:
    stmt = (
        select(Category, func.count(Product.id))
        .outerjoin(Product, and_(Product.category_id == Category.id, Product.active.is_(True)))
        .group_by(Category.id)
        .order_by(Category.sort_order, Category.slug)
    )
    return [
        CategoryOut(id=c.id, slug=c.slug, name=c.name, product_count=count) for c, count in session.execute(stmt)
    ]


def shipping_to_out(m: ShippingMethod) -> ShippingMethodOut:
    return ShippingMethodOut(
        code=m.code,
        name=m.name,
        description=m.description,
        price_gross_minor=m.price_gross_minor,
        price_gross_decimal=minor_to_decimal(m.price_gross_minor, m.currency),
        currency=m.currency,
        estimated_delivery_days=m.estimated_delivery_days,
    )


def list_shipping_methods(session: Session) -> list[ShippingMethod]:
    return list(
        session.scalars(
            select(ShippingMethod).where(ShippingMethod.active.is_(True)).order_by(ShippingMethod.sort_order, ShippingMethod.code)
        )
    )


def get_store_config(session: Session) -> StoreConfig | None:
    return session.get(StoreConfig, 1)
