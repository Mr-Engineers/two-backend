"""Request / correlation identifiers (context variables) and the ASGI middleware that binds them."""

from __future__ import annotations

import re
import uuid
from contextvars import ContextVar
from typing import Mapping

from starlette.datastructures import MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

request_id_var: ContextVar[str] = ContextVar("request_id", default="-")
correlation_id_var: ContextVar[str] = ContextVar("correlation_id", default="-")

_SAFE_ID = re.compile(r"^[A-Za-z0-9._:\-]{1,128}$")


def new_request_id() -> str:
    return uuid.uuid4().hex


def bind_ids(headers: Mapping[str, str] | None) -> tuple[str, str]:
    """Bind a fresh request id; keep the integrator's correlation id when it is well formed."""
    request_id = new_request_id()
    correlation = None
    if headers:
        candidate = headers.get("x-correlation-id") or headers.get("X-Correlation-ID")
        if candidate and _SAFE_ID.match(candidate):
            correlation = candidate
    correlation = correlation or request_id
    request_id_var.set(request_id)
    correlation_id_var.set(correlation)
    return request_id, correlation


class RequestContextMiddleware:
    def __init__(self, app: ASGIApp):
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        headers = {k.decode("latin-1").lower(): v.decode("latin-1") for k, v in scope["headers"]}
        request_id, correlation = bind_ids(headers)

        async def send_wrapper(message: Message) -> None:
            if message["type"] == "http.response.start":
                out = MutableHeaders(scope=message)
                out["X-Request-ID"] = request_id
                out["X-Correlation-ID"] = correlation
            await send(message)

        await self.app(scope, receive, send_wrapper)
