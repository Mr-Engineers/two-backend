from __future__ import annotations

import logging
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.errors import AppError
from app.request_context import correlation_id_var, request_id_var

logger = logging.getLogger("shop.api")

_HTTP_CODES = {404: "NOT_FOUND", 405: "METHOD_NOT_ALLOWED", 401: "UNAUTHORIZED", 403: "FORBIDDEN"}


def error_response(code: str, message: str, status: int, details: dict[str, Any] | None = None) -> JSONResponse:
    headers = {"WWW-Authenticate": "Bearer"} if status == 401 else None
    return JSONResponse(
        {
            "error": {
                "code": code,
                "message": message,
                "request_id": request_id_var.get(),
                "correlation_id": correlation_id_var.get(),
                "details": details or {},
            }
        },
        status_code=status,
        headers=headers,
    )


def install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(AppError)
    async def _app_error(_: Request, exc: AppError) -> JSONResponse:
        return error_response(exc.code, exc.message, exc.status_code, exc.details)

    @app.exception_handler(RequestValidationError)
    async def _validation(_: Request, exc: RequestValidationError) -> JSONResponse:
        # Field locations and messages only - never echo the rejected input values.
        problems = [{"loc": [str(p) for p in e["loc"]], "message": e["msg"], "type": e["type"]} for e in exc.errors()]
        return error_response("VALIDATION_ERROR", "Request validation failed.", 422, {"errors": problems})

    @app.exception_handler(StarletteHTTPException)
    async def _http(_: Request, exc: StarletteHTTPException) -> JSONResponse:
        return error_response(_HTTP_CODES.get(exc.status_code, "HTTP_ERROR"), str(exc.detail), exc.status_code)

    @app.exception_handler(Exception)
    async def _unhandled(_: Request, exc: Exception) -> JSONResponse:
        logger.error("unhandled exception", extra={"exc_type": type(exc).__name__})
        return error_response("INTERNAL_ERROR", "Internal server error.", 500)
