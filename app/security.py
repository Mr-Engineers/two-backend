"""API key helpers. Keys are random 256-bit tokens, stored only as SHA-256 hashes."""

from __future__ import annotations

import hashlib
import secrets

API_KEY_PREFIX = "shk_"


def generate_api_key() -> str:
    return API_KEY_PREFIX + secrets.token_urlsafe(32)


def hash_api_key(key: str) -> str:
    return hashlib.sha256(key.encode("utf-8")).hexdigest()
