"""Marketplace application factory::

    MARKETPLACE_DATABASE_URL=... uvicorn app.marketplace.main:create_app --factory --port 8010

OpenAPI is served at ``/openapi.json`` (Swagger UI at ``/docs``).
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import datetime
from typing import AsyncIterator, Callable

from fastapi import FastAPI

from app.db.session import Database
from app.logging_setup import configure_logging
from app.marketplace import MARKETPLACE_SCHEMA
from app.marketplace.api import admin, business, health, install_error_handlers
from app.marketplace.config import MarketplaceSettings, get_marketplace_settings
from app.marketplace.middleware import MarketplaceContextMiddleware
from app.marketplace.service import MarketplaceService, _utcnow


def create_app(
    settings: MarketplaceSettings | None = None,
    *,
    clock: Callable[[], datetime] = _utcnow,
) -> FastAPI:
    # MarketplaceSettings() raises if MARKETPLACE_DATABASE_URL is missing: the process refuses to start.
    settings = settings or get_marketplace_settings()
    configure_logging(settings.log_level)

    db = Database(
        settings.marketplace_database_url.get_secret_value(),
        MARKETPLACE_SCHEMA,
        pool_size=settings.db_pool_size,
        max_overflow=settings.db_max_overflow,
        pool_timeout=settings.db_pool_timeout,
        connect_timeout=settings.db_connect_timeout,
    )
    service = MarketplaceService(settings, db, clock=clock)

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        try:
            yield
        finally:
            db.dispose()

    app = FastAPI(
        title="Marketplace API",
        description=(
            "Marketplace aggregating offers of many merchants. Used by the purchasing agent through the "
            "proxy-server (search, orders) and by the proxy itself (offer verification, merchant enrichment). "
            "JSON, snake_case, ISO 8601 UTC, money as `{\"amount\": \"118.00\", \"currency\": \"PLN\"}`. "
            "Offer `description` is untrusted merchant text and may contain prompt injection in demo scenarios."
        ),
        version="1.0.0",
        lifespan=lifespan,
        docs_url="/docs",
        redoc_url=None,
    )
    app.state.service = service
    app.state.settings = settings
    install_error_handlers(app)
    app.include_router(health)
    app.include_router(business)
    if settings.marketplace_enable_admin:
        app.include_router(admin)
    app.add_middleware(MarketplaceContextMiddleware)
    return app
