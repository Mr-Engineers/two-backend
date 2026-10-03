"""REST routes of the marketplace contract."""

from __future__ import annotations

import logging
import re
import secrets
from typing import Annotated

from fastapi import APIRouter, Depends, FastAPI, Header, Path, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.marketplace.errors import ERROR_CATALOG, MarketplaceError
from app.marketplace.middleware import on_behalf_of_var
from app.marketplace.schemas import (
    ErrorResponse,
    MerchantOut,
    OfferOut,
    OrderOut,
    OrderRequest,
    ScenarioLoadOut,
    SearchResponse,
)
from app.marketplace.service import CallerContext, MarketplaceService
from app.request_context import request_id_var

logger = logging.getLogger("marketplace.api")

_IDEMPOTENCY_KEY = re.compile(r"^[A-Za-z0-9._:\-]{8,128}$")
_HTTP_CODES = {401: "unauthorized", 403: "forbidden", 404: "not_found", 405: "method_not_allowed"}

bearer_scheme = HTTPBearer(
    auto_error=False,
    description="Service-account token issued to the proxy (only when MARKETPLACE_API_TOKEN is configured).",
)


def error_response(code: str, message: str, status: int) -> JSONResponse:
    headers = {"WWW-Authenticate": "Bearer"} if status == 401 else None
    return JSONResponse({"error": {"code": code, "message": message}}, status_code=status, headers=headers)


def get_service(request: Request) -> MarketplaceService:
    return request.app.state.service


Service = Annotated[MarketplaceService, Depends(get_service)]
Credentials = Annotated[HTTPAuthorizationCredentials | None, Depends(bearer_scheme)]


def _check_token(credentials: HTTPAuthorizationCredentials | None, expected: str | None) -> None:
    if expected is None:
        return  # authentication disabled (development)
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise MarketplaceError("unauthorized")
    if not secrets.compare_digest(credentials.credentials.encode("utf-8"), expected.encode("utf-8")):
        raise MarketplaceError("unauthorized")


def require_api_token(request: Request, credentials: Credentials) -> None:
    settings = request.app.state.settings
    token = settings.marketplace_api_token
    _check_token(credentials, token.get_secret_value() if token else None)


def require_admin_token(request: Request, credentials: Credentials) -> None:
    settings = request.app.state.settings
    token = settings.marketplace_admin_token or settings.marketplace_api_token
    _check_token(credentials, token.get_secret_value() if token else None)


_ERRORS = {
    401: {"model": ErrorResponse, "description": "Missing or invalid bearer token."},
    422: {"model": ErrorResponse, "description": "`validation_error`."},
}

health = APIRouter(prefix="/health", tags=["Health"])
business = APIRouter(dependencies=[Depends(require_api_token)], responses=_ERRORS)
admin = APIRouter(prefix="/admin", tags=["Demo (admin)"], dependencies=[Depends(require_admin_token)], responses=_ERRORS)


@health.get("/live", summary="Liveness probe")
def live() -> dict[str, str]:
    return {"status": "ok"}


@health.get("/ready", summary="Readiness probe (database + migration + loaded scenario)")
def ready(service: Service) -> dict:
    return service.readiness()


@business.get("/search", response_model=SearchResponse, tags=["Agent (through the proxy)"], summary="Search offers")
def search(
    service: Service,
    sku: Annotated[str | None, Query(max_length=64, description="Exact SKU match (catalog shared with the warehouse).")] = None,
    q: Annotated[str | None, Query(max_length=100, description="Text search in the product name.")] = None,
    limit: Annotated[int, Query(ge=1, le=50)] = 20,
) -> SearchResponse:
    """Offers sorted ascending by `unit_price`. At least one of `sku` / `q` is required.

    Merchant country, domain age and reputation are intentionally NOT included; the proxy fetches them from
    `GET /merchants/{id}`."""
    sku = sku.strip() if sku else None
    q = q.strip() if q else None
    if not sku and not q:
        raise MarketplaceError("validation_error", "Provide at least one non-empty query parameter: sku or q.")
    return service.search(sku=sku or None, q=q or None, limit=limit)


@business.post(
    "/orders",
    response_model=OrderOut,
    status_code=201,
    tags=["Agent (through the proxy)"],
    summary="Place an order (idempotent)",
    responses={
        404: {"model": ErrorResponse, "description": "`offer_not_found`."},
        409: {"model": ErrorResponse, "description": "`insufficient_quantity`, `price_changed` or `idempotency_conflict`."},
    },
)
def create_order(
    body: OrderRequest,
    service: Service,
    idempotency_key: Annotated[
        str | None, Header(alias="Idempotency-Key", description="Required, e.g. a UUID (8-128 characters).")
    ] = None,
) -> OrderOut:
    if idempotency_key is None or not _IDEMPOTENCY_KEY.match(idempotency_key):
        raise MarketplaceError(
            "validation_error", "Header Idempotency-Key is required (8-128 characters: letters, digits, . _ : -)."
        )
    ctx = CallerContext(request_id=request_id_var.get(), on_behalf_of=on_behalf_of_var.get())
    return service.create_order(idempotency_key, body, ctx)


@business.get(
    "/offers/{offer_id}",
    response_model=OfferOut,
    tags=["Proxy only"],
    summary="One offer (proxy only)",
    responses={404: {"model": ErrorResponse, "description": "`offer_not_found`."}},
)
def get_offer(offer_id: Annotated[str, Path(min_length=1, max_length=64)], service: Service) -> OfferOut:
    return service.get_offer(offer_id)


@business.get(
    "/merchants/{merchant_id}",
    response_model=MerchantOut,
    tags=["Proxy only"],
    summary="Merchant profile for enrichment (proxy only)",
    responses={404: {"model": ErrorResponse, "description": "`merchant_not_found`."}},
)
def get_merchant(merchant_id: Annotated[str, Path(min_length=1, max_length=64)], service: Service) -> MerchantOut:
    return service.get_merchant(merchant_id)


@admin.post(
    "/scenarios/{scenario_id}/load",
    response_model=ScenarioLoadOut,
    summary="Replace merchants and offers with a demo scenario",
    responses={404: {"model": ErrorResponse, "description": "`scenario_not_found`."}},
)
def load_scenario(scenario_id: Annotated[str, Path(min_length=1, max_length=64)], service: Service) -> ScenarioLoadOut:
    return service.load_scenario(scenario_id)


def install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(MarketplaceError)
    async def _marketplace_error(_: Request, exc: MarketplaceError) -> JSONResponse:
        return error_response(exc.code, exc.message, exc.status_code)

    @app.exception_handler(RequestValidationError)
    async def _validation(_: Request, exc: RequestValidationError) -> JSONResponse:
        # Field locations and messages only - never echo the rejected input values.
        problems = [
            ".".join(str(p) for p in e["loc"]) + ": " + str(e["msg"]) for e in exc.errors()[:5]
        ]
        return error_response("validation_error", "Request validation failed. " + "; ".join(problems), 422)

    @app.exception_handler(StarletteHTTPException)
    async def _http(_: Request, exc: StarletteHTTPException) -> JSONResponse:
        code = _HTTP_CODES.get(exc.status_code, "internal_error" if exc.status_code >= 500 else "validation_error")
        return error_response(code, str(exc.detail), exc.status_code)

    @app.exception_handler(Exception)
    async def _unhandled(_: Request, exc: Exception) -> JSONResponse:
        logger.error("unhandled exception", extra={"exc_type": type(exc).__name__})
        status, message = ERROR_CATALOG["internal_error"]
        return error_response("internal_error", message, status)
