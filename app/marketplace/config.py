"""Marketplace runtime settings. No defaults for the data source: a missing value stops the process.

Two ways to reach the data (the same API and the same tables either way):

* **Supabase REST** - ``SUPABASE_URL`` + ``SUPABASE_KEY`` (the variables used by the AWS deployment). The key is the
  ``service_role`` key; ``shops`` and ``warehouse`` must be in the project's exposed schemas (``python -m app.cli
  expose-api``). Takes precedence when both ways are configured.
* **Direct PostgreSQL** - ``MARKETPLACE_DATABASE_URL`` (runtime user ``marketplace_rt``).
"""

from __future__ import annotations

from functools import lru_cache
from typing import Literal
from urllib.parse import parse_qs, urlsplit

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.config import normalize_database_url

_STRONG_SSLMODES = {"require", "verify-ca", "verify-full"}


def normalize_supabase_url(url: str) -> str:
    """``https://<ref>.supabase.co`` (also accepts a trailing slash or ``/rest/v1``) -> the project URL."""
    url = url.strip().rstrip("/")
    if url.endswith("/rest/v1"):
        url = url[: -len("/rest/v1")]
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.netloc or parts.query or parts.fragment or parts.path:
        raise ValueError("SUPABASE_URL must look like https://<project-ref>.supabase.co")
    return url


class MarketplaceSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", case_sensitive=False, extra="ignore"
    )

    # Supabase REST mode (SUPABASE_URL / SUPABASE_KEY). SUPABASE_KEY is a server-side secret: the service_role key.
    supabase_url: str | None = None
    supabase_key: SecretStr | None = None
    # Direct SQL mode: runtime database user of the marketplace (never the migration/owner account).
    marketplace_database_url: SecretStr | None = None
    app_env: Literal["development", "test", "production"] = "development"
    log_level: str = "INFO"
    # Timeout (seconds) of one request to the Supabase REST API.
    supabase_timeout: float = Field(default=10.0, gt=0, le=60)

    # Authentication (ADR 0003 leaves it to the shop team). When ``marketplace_api_token`` is set, every
    # business endpoint requires ``Authorization: Bearer <token>`` (the proxy's service account).
    # ``marketplace_admin_token`` optionally protects /admin/* with a separate token; without it the API token
    # is used. With neither set the API is open (development only; production refuses to start).
    marketplace_api_token: SecretStr | None = None
    marketplace_admin_token: SecretStr | None = None
    # POST /admin/scenarios/{id}/load needs write access to merchants and offers. Switch it off outside the demo.
    marketplace_enable_admin: bool = True
    # Open question 2 of the contract: when true, a confirmed order lowers ``available_qty``;
    # when false (default) the stock stays constant, which keeps the demo repeatable.
    marketplace_decrement_stock: bool = False

    db_pool_size: int = Field(default=5, ge=1, le=20)
    db_max_overflow: int = Field(default=5, ge=0, le=20)
    db_pool_timeout: float = Field(default=10.0, gt=0, le=60)
    db_connect_timeout: int = Field(default=5, ge=1, le=60)

    @field_validator("supabase_url", mode="before")
    @classmethod
    def _check_supabase_url(cls, value: str | None) -> str | None:
        if value is None or not str(value).strip():
            return None
        return normalize_supabase_url(str(value))

    @field_validator("marketplace_database_url", "supabase_key", mode="before")
    @classmethod
    def _empty_secret_is_none(cls, value):
        if value is None:
            return None
        raw = value.get_secret_value() if isinstance(value, SecretStr) else str(value)
        return None if not raw.strip() else raw.strip()

    @field_validator("marketplace_database_url")
    @classmethod
    def _check_url(cls, value: SecretStr | None) -> SecretStr | None:
        if value is None:
            return None
        return SecretStr(normalize_database_url(value.get_secret_value()))

    @field_validator("marketplace_api_token", "marketplace_admin_token")
    @classmethod
    def _empty_token_is_none(cls, value: SecretStr | None) -> SecretStr | None:
        if value is not None and not value.get_secret_value():
            return None
        return value

    @model_validator(mode="after")
    def _check_data_source(self) -> "MarketplaceSettings":
        if (self.supabase_url is None) != (self.supabase_key is None):
            raise ValueError("SUPABASE_URL and SUPABASE_KEY must be set together.")
        if self.supabase_url is None and self.marketplace_database_url is None:
            raise ValueError(
                "Set SUPABASE_URL and SUPABASE_KEY (Supabase REST) or MARKETPLACE_DATABASE_URL (direct PostgreSQL)."
            )
        if self.app_env == "production":
            if self.backend == "supabase":
                if not self.supabase_url.startswith("https://"):
                    raise ValueError("In production SUPABASE_URL must use https.")
            else:
                query = parse_qs(urlsplit(self.marketplace_database_url.get_secret_value()).query)
                mode = (query.get("sslmode") or ["prefer"])[0]
                if mode not in _STRONG_SSLMODES:
                    raise ValueError(
                        "In production MARKETPLACE_DATABASE_URL must contain sslmode=require, verify-ca or verify-full."
                    )
            if self.marketplace_api_token is None:
                raise ValueError("In production MARKETPLACE_API_TOKEN must be set.")
        return self

    @property
    def backend(self) -> Literal["supabase", "postgres"]:
        """``supabase`` (REST) wins when SUPABASE_URL + SUPABASE_KEY are set."""
        return "supabase" if self.supabase_url is not None else "postgres"


@lru_cache
def get_marketplace_settings() -> MarketplaceSettings:
    return MarketplaceSettings()  # type: ignore[call-arg]
