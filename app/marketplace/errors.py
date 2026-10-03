"""Marketplace errors. Wire format: ``{"error": {"code": "offer_not_found", "message": "..."}}``."""

from __future__ import annotations

# code -> (HTTP status, default message)
ERROR_CATALOG: dict[str, tuple[int, str]] = {
    "unauthorized": (401, "Missing or invalid bearer token."),
    "validation_error": (422, "Request validation failed."),
    "offer_not_found": (404, "Offer does not exist."),
    "merchant_not_found": (404, "Merchant does not exist."),
    "scenario_not_found": (404, "Scenario does not exist."),
    "not_found": (404, "Resource not found."),
    "method_not_allowed": (405, "Method not allowed."),
    "forbidden": (403, "Forbidden."),
    "insufficient_quantity": (409, "Requested quantity exceeds the available quantity."),
    "price_changed": (409, "The current price differs from expected_unit_price."),
    "idempotency_conflict": (409, "Idempotency-Key was already used with a different request body."),
    "db_unavailable": (503, "The database is unavailable."),
    "not_ready": (503, "The service is not ready."),
    "internal_error": (500, "Internal server error."),
}


class MarketplaceError(Exception):
    """An expected, client-visible error. The message must never contain secrets."""

    def __init__(self, code: str, message: str | None = None):
        status, default_message = ERROR_CATALOG[code]
        self.code = code
        self.status_code = status
        self.message = message or default_message
        super().__init__(f"{code}: {self.message}")
