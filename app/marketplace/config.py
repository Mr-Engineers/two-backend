"""Marketplace runtime settings. No defaults for the database: a missing value stops the process."""

from __future__ import annotations

from functools import lru_cache
from typing import Literal
from urllib.parse import parse_qs, urlsplit

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.config import normalize_database_url

_STRONG_SSLMODES = {"require", "verify-ca", "verify-full"}


class MarketplaceSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", case_sensitive=False, extra="ignore"
    )

    # Runtime database user of the marketplace (never the migration/owner account).
    marketplace_database_url: SecretStr
    app_env: Literal["development", "test", "production"] = "development"
    log_level: str = "INFO"

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

    @field_validator("marketplace_database_url")
    @classmethod
    def _check_url(cls, value: SecretStr) -> SecretStr:
        return SecretStr(normalize_database_url(value.get_secret_value()))

    @field_validator("marketplace_api_token", "marketplace_admin_token")
    @classmethod
    def _empty_token_is_none(cls, value: SecretStr | None) -> SecretStr | None:
        if value is not None and not value.get_secret_value():
            return None
        return value

    @model_validator(mode="after")
    def _check_production(self) -> "MarketplaceSettings":
        if self.app_env == "production":
            query = parse_qs(urlsplit(self.marketplace_database_url.get_secret_value()).query)
            mode = (query.get("sslmode") or ["prefer"])[0]
            if mode not in _STRONG_SSLMODES:
                raise ValueError(
                    "In production MARKETPLACE_DATABASE_URL must contain sslmode=require, verify-ca or verify-full."
                )
            if self.marketplace_api_token is None:
                raise ValueError("In production MARKETPLACE_API_TOKEN must be set.")
        return self


@lru_cache
def get_marketplace_settings() -> MarketplaceSettings:
    return MarketplaceSettings()  # type: ignore[call-arg]
