"""REST routes of the marketplace contract."""

from __future__ import annotations

import logging
import re
import secrets
import uuid
from typing import Annotated

import httpx
from fastapi import APIRouter, Depends, FastAPI, Header, Path, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.marketplace.attacks import (
    ExecuteRequest,
    ExecuteResultOut,
    ScenarioAttackOut,
    ScenarioListItem,
    build_attack,
    classify_outcome,
    list_scenarios,
    order_body,
    pick_draft,
    scenario_offers,
)
from app.marketplace.errors import ERROR_CATALOG, MarketplaceError
from app.marketplace.middleware import on_behalf_of_var
from app.marketplace.scenarios import build_scenario
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


# --- Faulty-request catalog (docs/testing/proxy-effectiveness.md, level A) -------------------------------------
# Read-only, derived from scenarios.py with no database access, so these work for both backends and without any
# scenario loaded. They expose the "wadliwe zapytania" each scenario is designed to produce.


@admin.get(
    "/scenarios",
    response_model=list[ScenarioListItem],
    summary="List demo scenarios and their expected decision",
)
def list_scenario_catalog(service: Service) -> list[ScenarioListItem]:
    return list_scenarios(service.clock().date())


def _build_scenario_or_404(scenario_id: str, service: Service):
    try:
        return build_scenario(scenario_id, service.clock().date())
    except KeyError:
        raise MarketplaceError("scenario_not_found", f"Scenario {scenario_id} does not exist") from None


@admin.get(
    "/scenarios/{scenario_id}/offers",
    response_model=SearchResponse,
    summary="Offers a scenario would expose (preview, no DB write)",
    responses={404: {"model": ErrorResponse, "description": "`scenario_not_found`."}},
)
def scenario_offers_preview(
    scenario_id: Annotated[str, Path(min_length=1, max_length=64)],
    service: Service,
    sku: Annotated[str | None, Query(max_length=64)] = None,
    q: Annotated[str | None, Query(max_length=100)] = None,
    limit: Annotated[int, Query(ge=1, le=50)] = 50,
) -> SearchResponse:
    scenario = _build_scenario_or_404(scenario_id, service)
    return scenario_offers(scenario, sku=(sku.strip() or None) if sku else None, q=(q.strip() or None) if q else None, limit=limit)


@admin.get(
    "/scenarios/{scenario_id}/attack",
    response_model=ScenarioAttackOut,
    summary="The faulty POST /orders request a manipulated agent would send",
    responses={404: {"model": ErrorResponse, "description": "`scenario_not_found`."}},
)
def scenario_attack(
    scenario_id: Annotated[str, Path(min_length=1, max_length=64)],
    service: Service,
    qty_needed: Annotated[int, Query(ge=1, le=2_147_483_647, description="Mandated restock quantity from GET /low-stock.")] = 40,
) -> ScenarioAttackOut:
    scenario = _build_scenario_or_404(scenario_id, service)
    try:
        return build_attack(scenario, qty_needed)
    except KeyError as exc:
        raise MarketplaceError("internal_error", str(exc)) from None


@admin.post(
    "/scenarios/{scenario_id}/execute",
    response_model=ExecuteResultOut,
    summary="Place the scenario's faulty order (blocked by the proxy, executed without it)",
    responses={
        404: {"model": ErrorResponse, "description": "`scenario_not_found`."},
        422: {"model": ErrorResponse, "description": "`validation_error` (clean scenario, or no target configured)."},
    },
)
def execute_scenario_attack(
    scenario_id: Annotated[str, Path(min_length=1, max_length=64)],
    request: Request,
    service: Service,
    body: ExecuteRequest | None = None,
) -> ExecuteResultOut:
    """Build the faulty ``POST /orders`` for the scenario and actually send it to a target.

    The order is sent as a real HTTP request to ``{base}/orders``. Point ``base`` at the proxy to see it blocked
    (``403`` → ``DENY``) and at the marketplace directly to see the attack go through (``201`` → executed). The
    target is admin-supplied, so this route is only mounted behind the admin token (and ``marketplace_enable_admin``).
    """
    settings = request.app.state.settings
    payload = body or ExecuteRequest()
    scenario = _build_scenario_or_404(scenario_id, service)
    try:
        attack = build_attack(scenario, payload.qty_needed)
        draft = pick_draft(attack, payload.use)
    except KeyError as exc:
        raise MarketplaceError("internal_error", str(exc)) from None
    except ValueError as exc:
        raise MarketplaceError("validation_error", str(exc)) from None

    base = (payload.base_url or settings.attack_execute_base_url or "").rstrip("/")
    if not base:
        raise MarketplaceError(
            "validation_error", "No target: set attack_execute_base_url or pass base_url (proxy or marketplace)."
        )
    if not base.startswith(("http://", "https://")):
        raise MarketplaceError("validation_error", "base_url must be an http(s) URL.")
    target = f"{base}/orders"

    token = payload.bearer_token or (
        settings.marketplace_api_token.get_secret_value() if settings.marketplace_api_token else None
    )
    headers = {"Content-Type": "application/json", "Idempotency-Key": uuid.uuid4().hex}
    if token:
        headers["Authorization"] = f"Bearer {token}"

    def result(http_status: int, response_body: object) -> ExecuteResultOut:
        outcome, interpretation = classify_outcome(http_status)
        return ExecuteResultOut(
            scenario_id=scenario.scenario_id,
            expected_decision=scenario.expected_decision,
            use=payload.use,
            target=target,
            sent_order=draft,
            http_status=http_status,
            outcome=outcome,
            interpretation=interpretation,
            response_body=response_body,
        )

    try:
        with httpx.Client(timeout=settings.attack_execute_timeout, follow_redirects=True) as client:
            resp = client.post(target, json=order_body(draft), headers=headers)
    except httpx.HTTPError as exc:
        return ExecuteResultOut(
            scenario_id=scenario.scenario_id,
            expected_decision=scenario.expected_decision,
            use=payload.use,
            target=target,
            sent_order=draft,
            http_status=0,
            outcome="error",
            interpretation=f"Target {target} unreachable: {type(exc).__name__}.",
            response_body=str(exc),
        )

    try:
        response_body: object = resp.json()
    except ValueError:
        response_body = resp.text
    logger.info(
        "scenario attack executed",
        extra={"scenario_id": scenario.scenario_id, "target": target, "status": resp.status_code},
    )
    return result(resp.status_code, response_body)


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
