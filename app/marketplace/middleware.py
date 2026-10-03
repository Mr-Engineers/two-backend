"""Request context for the marketplace: the proxy's informational headers ``X-Request-Id`` and
``X-On-Behalf-Of`` are bound to context variables (logged, never trusted for authorisation)."""

from __future__ import annotations

import logging
import re
import time
import uuid
from contextvars import ContextVar

from starlette.datastructures import MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.request_context import correlation_id_var, request_id_var

on_behalf_of_var: ContextVar[str | None] = ContextVar("on_behalf_of", default=None)

_SAFE_ID = re.compile(r"^[A-Za-z0-9._:@\-]{1,128}$")
access_logger = logging.getLogger("marketplace.access")


class MarketplaceContextMiddleware:
    def __init__(self, app: ASGIApp):
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        headers = {k.decode("latin-1").lower(): v.decode("latin-1") for k, v in scope["headers"]}
        incoming = headers.get("x-request-id")
        request_id = incoming if incoming and _SAFE_ID.match(incoming) else uuid.uuid4().hex
        behalf = headers.get("x-on-behalf-of")
        request_id_var.set(request_id)
        correlation_id_var.set(request_id)
        on_behalf_of_var.set(behalf if behalf and _SAFE_ID.match(behalf) else None)

        status = 500
        started = time.perf_counter()

        async def send_wrapper(message: Message) -> None:
            nonlocal status
            if message["type"] == "http.response.start":
                status = message["status"]
                MutableHeaders(scope=message)["X-Request-Id"] = request_id
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        finally:
            access_logger.info(
                "request",
                extra={
                    "method": scope["method"],
                    "path": scope["path"],
                    "status": status,
                    "duration_ms": round((time.perf_counter() - started) * 1000, 1),
                    "on_behalf_of": on_behalf_of_var.get(),
                },
            )
