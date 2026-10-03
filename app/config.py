"""Runtime settings. There are no defaults for the database or the shop id:
a missing value stops the process instead of producing a fake working shop."""

from __future__ import annotations

from functools import lru_cache
from typing import Literal
from urllib.parse import parse_qs, urlsplit

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.shops import ShopDefinition, ShopId, get_shop

_STRONG_SSLMODES = {"require", "verify-ca", "verify-full"}


def normalize_database_url(url: str) -> str:
    """Accept ``postgresql://`` / ``postgres://`` (the Supabase format) and return a
    SQLAlchemy URL for the psycopg 3 driver."""
    if url.startswith("postgres://"):
        url = "postgresql://" + url[len("postgres://") :]
    if url.startswith("postgresql://"):
        url = "postgresql+psycopg://" + url[len("postgresql://") :]
    if not url.startswith("postgresql+psycopg://"):
        raise ValueError("DATABASE_URL must be a PostgreSQL URL (postgresql://...)")
    return url


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", case_sensitive=False, extra="ignore"
    )

    shop_id: ShopId
    # Runtime database user of THIS shop (never the migration/owner account).
    database_url: SecretStr
    app_env: Literal["development", "test", "production"] = "development"
    log_level: str = "INFO"

    db_pool_size: int = Field(default=5, ge=1, le=20)
    db_max_overflow: int = Field(default=5, ge=0, le=20)
    db_pool_timeout: float = Field(default=10.0, gt=0, le=60)
    db_connect_timeout: int = Field(default=5, ge=1, le=60)

    quote_ttl_seconds: int = Field(default=300, ge=10, le=3600)
    max_cart_lines: int = Field(default=50, ge=1, le=500)
    max_line_quantity: int = Field(default=1000, ge=1, le=100000)

    # The outcome of the mock payment is a server-side setting, never a tool argument.
    mock_payment_mode: Literal["approve", "decline"] = "approve"

    # Extra Host header patterns accepted by the MCP endpoint (DNS rebinding protection),
    # comma separated, e.g. "shop-pl:*,shop-pl.internal:*".
    mcp_allowed_hosts: str = ""

    @field_validator("database_url")
    @classmethod
    def _check_url(cls, value: SecretStr) -> SecretStr:
        return SecretStr(normalize_database_url(value.get_secret_value()))

    @model_validator(mode="after")
    def _check_production_tls(self) -> "Settings":
        if self.app_env == "production":
            query = parse_qs(urlsplit(self.database_url.get_secret_value()).query)
            mode = (query.get("sslmode") or ["prefer"])[0]
            if mode not in _STRONG_SSLMODES:
                raise ValueError(
                    "In production DATABASE_URL must contain sslmode=require, verify-ca or "
                    "verify-full (prefer verify-full with sslrootcert for Supabase)."
                )
        return self

    @property
    def shop(self) -> ShopDefinition:
        return get_shop(self.shop_id)

    @property
    def extra_allowed_hosts(self) -> list[str]:
        return [h.strip() for h in self.mcp_allowed_hosts.split(",") if h.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]
