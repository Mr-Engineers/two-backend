from __future__ import annotations

import uuid
from typing import Annotated, Literal

from fastapi import APIRouter, Query

from app.api.deps import Customer, Service
from app.countries import CountryCode
from app.schemas.cart import CartOut, SetCartItemRequest
from app.schemas.catalog import (
    CategoryList,
    ProductOut,
    ProductPage,
    ShippingMethodList,
    StoreInfo,
)
from app.schemas.checkout import CheckoutOut, CheckoutRequest, OrderOut, OrderPage, QuoteOut, QuoteRequest

ExpectedVersionQuery = Annotated[int | None, Query(ge=1, description="Optional cart version (optimistic lock).")]

health = APIRouter(prefix="/health", tags=["Health"])
catalog = APIRouter(tags=["Catalog (catalog_read)"])
carts = APIRouter(tags=["Carts (cart_write)"])
checkout = APIRouter(tags=["Quotes and checkout (quote_write / purchase)"])
orders = APIRouter(tags=["Orders (order_read)"])


@health.get("/live", summary="Liveness probe")
def live() -> dict[str, str]:
    return {"status": "ok"}


@health.get("/ready", summary="Readiness probe (database + migrations + seed)")
def ready(service: Service) -> dict:
    return service.readiness()


@catalog.get("/store", response_model=StoreInfo, summary="Shop information")
def get_store(service: Service, _: Customer) -> StoreInfo:
    return service.get_store_info()


@catalog.get("/categories", response_model=CategoryList)
def get_categories(service: Service, _: Customer) -> CategoryList:
    return CategoryList(items=service.list_categories())


@catalog.get("/products", response_model=ProductPage, summary="Browse / search products")
def get_products(
    service: Service,
    _: Customer,
    q: Annotated[str | None, Query(max_length=100, description="Search in name, SKU, canonical_item_code.")] = None,
    category: Annotated[str | None, Query(max_length=64, description="Category slug.")] = None,
    availability: Literal["in_stock", "out_of_stock"] | None = None,
    origin: Annotated[list[CountryCode] | None, Query(max_length=30, description="Filter by country_of_origin.")] = None,
    sort: Literal["name", "sku", "price_asc", "price_desc"] = "name",
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    offset: Annotated[int, Query(ge=0, le=100000)] = 0,
) -> ProductPage:
    return service.search_products(
        q=q, category=category, availability=availability, origin_countries=origin, sort=sort,
        limit=limit, offset=offset,
    )


@catalog.get("/products/{sku}", response_model=ProductOut)
def get_product(sku: str, service: Service, _: Customer) -> ProductOut:
    return service.get_product(sku)


@catalog.get("/shipping-methods", response_model=ShippingMethodList)
def get_shipping_methods(service: Service, _: Customer) -> ShippingMethodList:
    return ShippingMethodList(items=service.list_shipping_methods())


@carts.post("/carts", response_model=CartOut, status_code=201, summary="Create an empty cart")
def create_cart(service: Service, customer: Customer) -> CartOut:
    return service.create_cart(customer)


@carts.get("/carts/{cart_id}", response_model=CartOut)
def get_cart(cart_id: uuid.UUID, service: Service, customer: Customer) -> CartOut:
    return service.get_cart(customer, cart_id)


@carts.put("/carts/{cart_id}/items/{sku}", response_model=CartOut, summary="Set the absolute quantity of a line")
def set_cart_item(cart_id: uuid.UUID, sku: str, body: SetCartItemRequest, service: Service, customer: Customer) -> CartOut:
    return service.set_cart_item(customer, cart_id, sku, body.quantity, body.expected_version)


@carts.delete("/carts/{cart_id}/items/{sku}", response_model=CartOut, summary="Remove a line")
def remove_cart_item(
    cart_id: uuid.UUID, sku: str, service: Service, customer: Customer, expected_version: ExpectedVersionQuery = None
) -> CartOut:
    return service.remove_cart_item(customer, cart_id, sku, expected_version)


@carts.delete("/carts/{cart_id}/items", response_model=CartOut, summary="Remove all lines")
def clear_cart(cart_id: uuid.UUID, service: Service, customer: Customer, expected_version: ExpectedVersionQuery = None) -> CartOut:
    return service.clear_cart(customer, cart_id, expected_version)


@checkout.post("/carts/{cart_id}/quotes", response_model=QuoteOut, status_code=201,
               summary="Create an immutable checkout quote (does not buy or reserve stock)")
def create_quote(cart_id: uuid.UUID, body: QuoteRequest, service: Service, customer: Customer) -> QuoteOut:
    return service.create_checkout_quote(customer, cart_id, body)


@checkout.get("/quotes/{quote_id}", response_model=QuoteOut)
def get_quote(quote_id: uuid.UUID, service: Service, customer: Customer) -> QuoteOut:
    return service.get_checkout_quote(customer, quote_id)


@checkout.post("/checkout", response_model=CheckoutOut, summary="PURCHASE: place the order for a quote (mock payment)")
def post_checkout(body: CheckoutRequest, service: Service, customer: Customer) -> CheckoutOut:
    return service.checkout(customer, body.quote_id, body.idempotency_key)


@orders.get("/orders", response_model=OrderPage)
def get_orders(
    service: Service,
    customer: Customer,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    offset: Annotated[int, Query(ge=0, le=100000)] = 0,
) -> OrderPage:
    return service.list_orders(customer, limit, offset)


@orders.get("/orders/{order_id}", response_model=OrderOut)
def get_order(order_id: uuid.UUID, service: Service, customer: Customer) -> OrderOut:
    return service.get_order(customer, order_id)


ALL_ROUTERS = (health, catalog, carts, checkout, orders)
