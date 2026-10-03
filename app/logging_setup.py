"""Structured JSON logging with redaction of secrets and address data."""

from __future__ import annotations

import json
import logging
import re
import sys
from datetime import datetime, timezone
from typing import Any

from app.request_context import correlation_id_var, request_id_var

_SENSITIVE_KEYS = re.compile(
    r"(authorization|api[_-]?key|token|password|secret|database_url|address|line1|line2|recipient|phone|email|postal)",
    re.IGNORECASE,
)
_BEARER = re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]+")
_DB_URL = re.compile(r"(postgres(?:ql)?(?:\+\w+)?://[^:/\s]+:)[^@\s]+@")
_API_KEY = re.compile(r"\bshk_[A-Za-z0-9_-]{8,}")

_STD_ATTRS = set(logging.LogRecord("", 0, "", 0, "", (), None).__dict__) | {"message", "asctime"}


def redact_text(text: str) -> str:
    text = _BEARER.sub("Bearer [REDACTED]", text)
    text = _DB_URL.sub(r"\1[REDACTED]@", text)
    return _API_KEY.sub("[REDACTED]", text)


def redact(value: Any, key: str | None = None) -> Any:
    if key is not None and _SENSITIVE_KEYS.search(key):
        return "[REDACTED]"
    if isinstance(value, dict):
        return {k: redact(v, str(k)) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [redact(v) for v in value]
    if isinstance(value, str):
        return redact_text(value)
    return value


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": redact_text(record.getMessage()),
            "request_id": request_id_var.get(),
            "correlation_id": correlation_id_var.get(),
        }
        for key, value in record.__dict__.items():
            if key not in _STD_ATTRS and not key.startswith("_"):
                payload[key] = redact(value, key)
        if record.exc_info:
            # Exception type only: messages may contain SQL parameters or connection strings.
            payload["exc_type"] = record.exc_info[0].__name__ if record.exc_info[0] else None
        return json.dumps(payload, default=str, ensure_ascii=False)


def configure_logging(level: str = "INFO") -> None:
    root = logging.getLogger()
    if any(getattr(h, "_shop_json", False) for h in root.handlers):
        root.setLevel(level.upper())
        return
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    handler._shop_json = True  # type: ignore[attr-defined]
    root.handlers = [handler]
    root.setLevel(level.upper())
    for noisy in ("httpx", "httpx2", "httpcore", "mcp.server.streamable_http_manager"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    logging.getLogger("uvicorn.access").disabled = True  # access logs may contain ids; audit covers mutations
