"""MCP endpoint (official Python SDK, Streamable HTTP). Tools are thin wrappers over ``ShopService``,
the same service layer the REST routers use. Identity always comes from the bearer API key."""

import functools
import json
import logging
import uuid
from typing import Annotated, Any, Callable, Literal, TypeVar

import anyio.to_thread
from mcp.server.mcpserver import Context, MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.server.transport_security import TransportSecuritySettings
from mcp_types import ToolAnnotations
from pydantic import Field
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from app.countries import CountryCode
from app.errors import AppError
from app.request_context import bind_ids, correlation_id_var, request_id_var
from app.schemas.cart import CartOut
from app.schemas.catalog import (
    CategoryList,
    ProductOut,
    ProductPage,
    ShippingMethodList,
    StoreInfo,
)
from app.schemas.checkout import (
    CheckoutOut,
    OrderOut,
    OrderPage,
    QuoteOut,
    QuoteRequest,
    ShippingAddress,
)
from app.services.shop import ShopService

logger = logging.getLogger("shop.mcp")
T = TypeVar("T")

# Integration metadata (informational only; no company policy is enforced by the shops).
TOOL_CLASSES = {
    "get_store_info": "catalog_read",
    "list_categories": "catalog_read",
    "search_products": "catalog_read",
    "get_product": "catalog_read",
    "list_shipping_methods": "catalog_read",
    "create_cart": "cart_write",
    "get_cart": "cart_read",
    "set_cart_item": "cart_write",
    "remove_cart_item": "cart_write",
    "clear_cart": "cart_write",
    "create_checkout_quote": "quote_write",
    "get_checkout_quote": "quote_read",
    "checkout": "purchase/payment_mock",
    "list_orders": "order_read",
    "get_order": "order_read",
}
TOOL_NAMES = tuple(TOOL_CLASSES)

_READ = ToolAnnotations(read_only_hint=True, destructive_hint=False, idempotent_hint=True, open_world_hint=False)
_WRITE_ADDITIVE = ToolAnnotations(read_only_hint=False, destructive_hint=False, idempotent_hint=False, open_world_hint=False)
_WRITE_IDEMPOTENT = ToolAnnotations(read_only_hint=False, destructive_hint=False, idempotent_hint=True, open_world_hint=False)
_WRITE_DESTRUCTIVE_IDEMPOTENT = ToolAnnotations(read_only_hint=False, destructive_hint=True, idempotent_hint=True, open_world_hint=False)
_PURCHASE = ToolAnnotations(
    title="Place order (mock payment)", read_only_hint=False, destructive_hint=True, idempotent_hint=True,
    open_world_hint=False,
)


def error_payload(code: str, message: str, details: dict[str, Any] | None = None) -> dict[str, Any]:
    return {
        "error": {
            "code": code,
            "message": message,
            "request_id": request_id_var.get(),
            "correlation_id": correlation_id_var.get(),
            "details": details or {},
        }
    }


def transport_security(extra_hosts: list[str]) -> TransportSecuritySettings:
    hosts = ["127.0.0.1:*", "localhost:*", "[::1]:*", *extra_hosts]
    origins = [f"http://{h}" for h in ["127.0.0.1:*", "localhost:*", "[::1]:*", *extra_hosts]]
    return TransportSecuritySettings(enable_dns_rebinding_protection=True, allowed_hosts=hosts, allowed_origins=origins)


class McpAuthApp:
    """ASGI wrapper: the whole /mcp endpoint requires a valid bearer API key (real HTTP 401 otherwise)."""

    def __init__(self, inner: ASGIApp, service: ShopService):
        self.inner = inner
        self.service = service

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.inner(scope, receive, send)
            return
        request = Request(scope)
        try:
            customer_id = await anyio.to_thread.run_sync(self.service.authenticate, _bearer(request))
        except AppError as exc:
            status = exc.status_code
            response = JSONResponse(
                error_payload(exc.code, exc.message, exc.details),
                status_code=status,
                headers={"WWW-Authenticate": "Bearer"} if status == 401 else None,
            )
            await response(scope, receive, send)
            return
        scope.setdefault("state", {})["customer_id"] = customer_id
        await self.inner(scope, receive, send)


def _bearer(request: Request) -> str | None:
    header = request.headers.get("authorization", "")
    scheme, _, token = header.partition(" ")
    return token.strip() if scheme.lower() == "bearer" and token.strip() else None


def build_mcp_server(service: ShopService) -> MCPServer:
    shop = service.shop
    mcp = MCPServer(
        name=shop.shop_id,
        title=f"{shop.name} ({shop.country}) office supplies shop",
        instructions=(
            f"Online shop '{shop.name}' (store country {shop.country}, currency {shop.currency}). "
            "Browse the catalog, build a cart, create a checkout quote and check out. "
            "Every product, cart line, quote line and order line carries country_of_origin (ISO 3166-1 alpha-2, "
            "the country where that product was made); quotes and orders also list origin_countries. "
            "Amounts are integers in minor units (fields ending in _minor). Payment is a mock."
        ),
        version="1.0.0",
    )

    async def run(ctx: Context, fn: Callable[[uuid.UUID], T]) -> T:
        request = ctx.request_context.request
        headers = request.headers if request is not None else None
        bind_ids(headers)
        try:
            customer_id = getattr(request.state, "customer_id", None) if request is not None else None
            if customer_id is None:
                customer_id = await anyio.to_thread.run_sync(
                    service.authenticate, _bearer(request) if request is not None else None
                )
            return await anyio.to_thread.run_sync(functools.partial(fn, customer_id))
        except AppError as exc:
            raise ToolError(json.dumps(error_payload(exc.code, exc.message, exc.details))) from None
        except ToolError:
            raise
        except Exception as exc:  # noqa: BLE001 - never leak SQL / connection strings to the agent
            logger.error("unexpected tool failure", extra={"exc_type": type(exc).__name__})
            raise ToolError(json.dumps(error_payload("INTERNAL_ERROR", "Internal server error."))) from None

    def meta(tool: str) -> dict[str, Any]:
        return {"io.shop/tool_class": TOOL_CLASSES[tool]}

    CartId = Annotated[uuid.UUID, Field(description="Cart id returned by create_cart.")]
    Sku = Annotated[str, Field(min_length=1, max_length=64, description="Product SKU from the catalog.")]
    ExpectedVersion = Annotated[
        int | None, Field(ge=1, description="Optional cart version for optimistic concurrency.")
    ]

    @mcp.tool(annotations=_READ, meta=meta("get_store_info"))
    async def get_store_info(ctx: Context) -> StoreInfo:
        """Shop name, store country, currency, locale and limits. Store country is NOT product origin."""
        return await run(ctx, lambda _c: service.get_store_info())

    @mcp.tool(annotations=_READ, meta=meta("list_categories"))
    async def list_categories(ctx: Context) -> CategoryList:
        """List product categories with the number of active products."""
        return await run(ctx, lambda _c: CategoryList(items=service.list_categories()))

    @mcp.tool(annotations=_READ, meta=meta("search_products"))
    async def search_products(
        ctx: Context,
        q: Annotated[str | None, Field(max_length=100, description="Search in name, SKU and canonical_item_code.")] = None,
        category: Annotated[str | None, Field(max_length=64, description="Category slug.")] = None,
        availability: Annotated[Literal["in_stock", "out_of_stock"] | None, Field()] = None,
        origin_countries: Annotated[
            list[CountryCode] | None,
            Field(max_length=30, description="Only products made in one of these countries."),
        ] = None,
        sort: Annotated[Literal["name", "sku", "price_asc", "price_desc"], Field(description="Stable sort order.")] = "name",
        limit: Annotated[int, Field(ge=1, le=100)] = 20,
        offset: Annotated[int, Field(ge=0, le=100000)] = 0,
    ) -> ProductPage:
        """Browse/search active products. Every item has country_of_origin, unit_gross_minor and stock_quantity."""
        return await run(
            ctx,
            lambda _c: service.search_products(
                q=q, category=category, availability=availability, origin_countries=origin_countries,
                sort=sort, limit=limit, offset=offset,
            ),
        )

    @mcp.tool(annotations=_READ, meta=meta("get_product"))
    async def get_product(ctx: Context, sku: Sku) -> ProductOut:
        """Product details by SKU (includes country_of_origin, packaging, price and stock)."""
        return await run(ctx, lambda _c: service.get_product(sku))

    @mcp.tool(annotations=_READ, meta=meta("list_shipping_methods"))
    async def list_shipping_methods(ctx: Context) -> ShippingMethodList:
        """Available shipping methods with explicit gross prices."""
        return await run(ctx, lambda _c: ShippingMethodList(items=service.list_shipping_methods()))

    @mcp.tool(annotations=_WRITE_ADDITIVE, meta=meta("create_cart"))
    async def create_cart(ctx: Context) -> CartOut:
        """Create an empty cart owned by the authenticated customer. A cart does not reserve stock."""
        return await run(ctx, lambda c: service.create_cart(c))

    @mcp.tool(annotations=_READ, meta=meta("get_cart"))
    async def get_cart(ctx: Context, cart_id: CartId) -> CartOut:
        """Read a cart with line totals, subtotal and origin_countries."""
        return await run(ctx, lambda c: service.get_cart(c, cart_id))

    @mcp.tool(annotations=_WRITE_IDEMPOTENT, meta=meta("set_cart_item"))
    async def set_cart_item(
        ctx: Context,
        cart_id: CartId,
        sku: Sku,
        quantity: Annotated[int, Field(description="ABSOLUTE number of packs for this SKU (not a delta).")],
        expected_version: ExpectedVersion = None,
    ) -> CartOut:
        """Add the SKU or set its quantity to an absolute value. Retrying is safe. Price/origin come from the catalog."""
        return await run(ctx, lambda c: service.set_cart_item(c, cart_id, sku, quantity, expected_version))

    @mcp.tool(annotations=_WRITE_DESTRUCTIVE_IDEMPOTENT, meta=meta("remove_cart_item"))
    async def remove_cart_item(ctx: Context, cart_id: CartId, sku: Sku, expected_version: ExpectedVersion = None) -> CartOut:
        """Remove a line from the cart (no-op when the line is absent)."""
        return await run(ctx, lambda c: service.remove_cart_item(c, cart_id, sku, expected_version))

    @mcp.tool(annotations=_WRITE_DESTRUCTIVE_IDEMPOTENT, meta=meta("clear_cart"))
    async def clear_cart(ctx: Context, cart_id: CartId, expected_version: ExpectedVersion = None) -> CartOut:
        """Remove all lines from the cart."""
        return await run(ctx, lambda c: service.clear_cart(c, cart_id, expected_version))

    @mcp.tool(annotations=_WRITE_ADDITIVE, meta=meta("create_checkout_quote"))
    async def create_checkout_quote(
        ctx: Context,
        cart_id: CartId,
        shipping_address: ShippingAddress,
        shipping_method_code: Annotated[str | None, Field(pattern=r"^[a-z0-9_-]{1,32}$")] = None,
        expected_cart_version: ExpectedVersion = None,
    ) -> QuoteOut:
        """Create an immutable priced quote with a TTL. It does NOT buy, pay or reserve stock. Inspect
        items[].country_of_origin and origin_countries before calling checkout."""
        request = QuoteRequest(
            shipping_address=shipping_address,
            shipping_method_code=shipping_method_code,
            expected_cart_version=expected_cart_version,
        )
        return await run(ctx, lambda c: service.create_checkout_quote(c, cart_id, request))

    @mcp.tool(annotations=_READ, meta=meta("get_checkout_quote"))
    async def get_checkout_quote(ctx: Context, quote_id: Annotated[uuid.UUID, Field(description="Quote id.")]) -> QuoteOut:
        """Read a quote (prices and country_of_origin of every line, totals, expiry)."""
        return await run(ctx, lambda c: service.get_checkout_quote(c, quote_id))

    @mcp.tool(annotations=_PURCHASE, meta=meta("checkout"))
    async def checkout(
        ctx: Context,
        quote_id: Annotated[uuid.UUID, Field(description="Quote to buy.")],
        idempotency_key: Annotated[
            str,
            Field(min_length=8, max_length=128, pattern=r"^[A-Za-z0-9._:\-]+$",
                  description="Client-generated unique key. The same key + quote returns the same order."),
        ],
    ) -> CheckoutOut:
        """PURCHASE: places the order for the quote with a mock payment and decrements stock. Fails with
        QUOTE_STALE / QUOTE_EXPIRED / INSUFFICIENT_STOCK instead of buying at a changed price."""
        return await run(ctx, lambda c: service.checkout(c, quote_id, idempotency_key))

    @mcp.tool(annotations=_READ, meta=meta("list_orders"))
    async def list_orders(
        ctx: Context,
        limit: Annotated[int, Field(ge=1, le=100)] = 20,
        offset: Annotated[int, Field(ge=0, le=100000)] = 0,
    ) -> OrderPage:
        """Order history of the authenticated customer, newest first."""
        return await run(ctx, lambda c: service.list_orders(c, limit, offset))

    @mcp.tool(annotations=_READ, meta=meta("get_order"))
    async def get_order(ctx: Context, order_id: Annotated[uuid.UUID, Field(description="Order id.")]) -> OrderOut:
        """Order details with the immutable snapshot (including country_of_origin per line)."""
        return await run(ctx, lambda c: service.get_order(c, order_id))

    return mcp
