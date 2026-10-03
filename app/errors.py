"""Domain errors shared by REST and MCP."""

from __future__ import annotations

from typing import Any

# code -> (HTTP status, default message)
ERROR_CATALOG: dict[str, tuple[int, str]] = {
    "UNAUTHORIZED": (401, "Missing or invalid API key."),
    "VALIDATION_ERROR": (422, "Request validation failed."),
    "PRODUCT_NOT_FOUND": (404, "Product not found."),
    "CART_NOT_FOUND": (404, "Cart not found."),
    "QUOTE_NOT_FOUND": (404, "Checkout quote not found."),
    "ORDER_NOT_FOUND": (404, "Order not found."),
    "SHIPPING_METHOD_NOT_FOUND": (404, "Shipping method not found."),
    "INVALID_QUANTITY": (422, "Quantity must be a positive integer within the allowed limit."),
    "CART_LINE_LIMIT": (422, "The cart has reached the maximum number of lines."),
    "CART_VERSION_CONFLICT": (409, "The cart was modified by another request."),
    "CART_ALREADY_CHECKED_OUT": (409, "The cart has already been checked out."),
    "EMPTY_CART": (409, "The cart is empty."),
    "PRODUCT_INACTIVE": (409, "The product is not active."),
    "INSUFFICIENT_STOCK": (409, "Requested quantity exceeds available stock."),
    "QUOTE_STALE": (409, "The quote no longer matches the cart, prices, origin or shipping."),
    "QUOTE_EXPIRED": (409, "The quote has expired."),
    "IDEMPOTENCY_CONFLICT": (409, "Idempotency key was already used with a different request."),
    "PAYMENT_DECLINED_MOCK": (402, "The (mock) payment was declined."),
    "DB_UNAVAILABLE": (503, "The database is unavailable."),
    "NOT_READY": (503, "The service is not ready."),
    "INTERNAL_ERROR": (500, "Internal server error."),
}


class AppError(Exception):
    """An expected, client-visible error. ``details`` must never contain secrets."""

    def __init__(self, code: str, message: str | None = None, *, details: dict[str, Any] | None = None):
        status, default_message = ERROR_CATALOG[code]
        self.code = code
        self.status_code = status
        self.message = message or default_message
        self.details = details or {}
        super().__init__(f"{code}: {self.message}")
