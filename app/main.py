"""Application factory. One code base, three instances selected by SHOP_ID::

    SHOP_ID=shop-pl DATABASE_URL=... uvicorn app.main:create_app --factory --port 8001
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import datetime
from typing import AsyncIterator, Callable

from fastapi import FastAPI
from mcp.server.streamable_http_manager import StreamableHTTPASGIApp
from starlette.routing import Route

from app.api.errors import install_error_handlers
from app.api.routers import ALL_ROUTERS
from app.config import Settings, get_settings
from app.db.session import Database
from app.logging_setup import configure_logging
from app.mcp_server import McpAuthApp, build_mcp_server, transport_security
from app.request_context import RequestContextMiddleware
from app.services.payment import PaymentProvider
from app.services.shop import ShopService, _utcnow


def create_app(
    settings: Settings | None = None,
    *,
    payment: PaymentProvider | None = None,
    clock: Callable[[], datetime] = _utcnow,
) -> FastAPI:
    # Settings() raises if SHOP_ID / DATABASE_URL are missing: the process refuses to start
    # instead of pretending to be a working shop.
    settings = settings or get_settings()
    configure_logging(settings.log_level)
    shop = settings.shop

    db = Database(
        settings.database_url.get_secret_value(),
        shop.schema,
        pool_size=settings.db_pool_size,
        max_overflow=settings.db_max_overflow,
        pool_timeout=settings.db_pool_timeout,
        connect_timeout=settings.db_connect_timeout,
    )
    service = ShopService(settings, db, payment=payment, clock=clock)
    mcp = build_mcp_server(service)
    # Builds the Streamable HTTP session manager (stateless: every request is self-contained).
    mcp.streamable_http_app(
        json_response=True,
        stateless_http=True,
        transport_security=transport_security(settings.extra_allowed_hosts),
    )

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        async with mcp.session_manager.run():  # MCP SDK lifecycle must run inside the FastAPI lifespan
            try:
                yield
            finally:
                db.dispose()

    app = FastAPI(
        title=f"{shop.name} - office supplies shop ({shop.shop_id})",
        description=(
            f"Demo shop backend. Store country **{shop.country}**, currency **{shop.currency}**, "
            "catalog language "
            f"**{shop.locale}**. Every product carries `country_of_origin` (ISO 3166-1 alpha-2) which is "
            "independent from the store country. Authenticate with `Authorization: Bearer <demo API key>`. "
            "The same operations are available over MCP at `/mcp` (Streamable HTTP)."
        ),
        version="1.0.0",
        lifespan=lifespan,
        docs_url="/docs",
        redoc_url=None,
    )
    app.state.service = service
    app.state.settings = settings
    install_error_handlers(app)
    for router in ALL_ROUTERS:
        app.include_router(router)

    mcp_endpoint = McpAuthApp(StreamableHTTPASGIApp(mcp.session_manager), service)
    app.router.routes.append(Route("/mcp", endpoint=mcp_endpoint))
    app.add_middleware(RequestContextMiddleware)
    return app
