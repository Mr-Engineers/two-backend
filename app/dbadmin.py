"""Administrative database operations (schemas, runtime roles, grants, migrations).

Everything here runs with the MIGRATION/owner account. The runtime accounts only get the privileges
listed in ``RUNTIME_GRANTS``. Schema and role names come from the fixed shop list.
"""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Mapping

import psycopg
from alembic import command
from alembic.config import Config
from psycopg import sql as pgsql
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine

from app.config import normalize_database_url
from app.shops import SHOPS, ShopDefinition

ROOT = Path(__file__).resolve().parent.parent

# Least-privilege matrix for the runtime users (products: only stock may be changed, via column grant).
RUNTIME_GRANTS: dict[str, str] = {
    "alembic_version": "SELECT",
    "store_config": "SELECT",
    "categories": "SELECT",
    "products": "SELECT",
    "customers": "SELECT",
    "api_keys": "SELECT",
    "shipping_methods": "SELECT",
    "carts": "SELECT, INSERT, UPDATE",
    "cart_items": "SELECT, INSERT, UPDATE, DELETE",
    "checkout_quotes": "SELECT, INSERT",
    "checkout_quote_items": "SELECT, INSERT",
    "orders": "SELECT, INSERT",
    "order_items": "SELECT, INSERT",
    "mock_payments": "SELECT, INSERT",
    "idempotency_records": "SELECT, INSERT",
    "audit_events": "SELECT, INSERT",
}

_SUPABASE_ROLES = "ARRAY['anon','authenticated','service_role','authenticator']"


def _run_statements(admin_url: str, statements: list[str]) -> None:
    """Execute DDL with raw psycopg (no client-side '%' interpretation) in autocommit mode."""
    url = normalize_database_url(admin_url).replace("postgresql+psycopg://", "postgresql://", 1)
    with psycopg.connect(url, autocommit=True) as conn:
        for stmt in statements:
            conn.execute(stmt)  # type: ignore[arg-type]


def _admin_engine(url: str) -> Engine:
    return create_engine(normalize_database_url(url), isolation_level="AUTOCOMMIT")


def _literal(value: str | None, placeholder: str) -> str:
    if value is None:
        return f"'<{placeholder}>'"
    return pgsql.Literal(value).as_string()


def setup_statements(shop: ShopDefinition, password: str | None, *, placeholder: bool = False) -> list[str]:
    """Schema + runtime role. ``placeholder=True`` renders a password placeholder (for the Supabase SQL editor)."""
    s, r = shop.schema, shop.runtime_role
    if placeholder:
        pw: str | None = _literal(None, f"PASSWORD_FOR_{r.upper()}")
    else:
        pw = _literal(password, "") if password else None
    # Supabase's postgres is not a superuser. Even the negative forms of
    # SUPERUSER, REPLICATION and BYPASSRLS require elevated privileges in
    # ALTER ROLE. New roles already default to false for these attributes.
    # Validate existing roles instead of attempting to change protected flags.
    alter = f'ALTER ROLE "{r}" WITH LOGIN NOCREATEDB NOCREATEROLE'
    if pw:
        alter += f" PASSWORD {pw}"
    return [
        f"DO $$ BEGIN IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{r}') "
        f'THEN CREATE ROLE "{r}" LOGIN; END IF; END $$',
        f"DO $$ BEGIN IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{r}' "
        "AND (rolsuper OR rolreplication OR rolbypassrls)) THEN "
        f"RAISE EXCEPTION 'Runtime role {r} has unsafe privileges; use a restricted role'; "
        "END IF; END $$",
        alter,
        f'ALTER ROLE "{r}" SET search_path = "{s}"',
        f"ALTER ROLE \"{r}\" SET statement_timeout = '15s'",
        f"ALTER ROLE \"{r}\" SET lock_timeout = '10s'",
        f"ALTER ROLE \"{r}\" SET idle_in_transaction_session_timeout = '30s'",
        f'CREATE SCHEMA IF NOT EXISTS "{s}"',
        f'REVOKE ALL ON SCHEMA "{s}" FROM PUBLIC',
        f'GRANT USAGE ON SCHEMA "{s}" TO "{r}"',
    ]


def grants_statements(shop: ShopDefinition) -> list[str]:
    s, r = shop.schema, shop.runtime_role
    stmts = [
        f'REVOKE ALL ON SCHEMA "{s}" FROM PUBLIC',
        f'REVOKE ALL ON ALL TABLES IN SCHEMA "{s}" FROM PUBLIC',
        f'REVOKE ALL ON ALL SEQUENCES IN SCHEMA "{s}" FROM PUBLIC',
        f'REVOKE ALL ON ALL FUNCTIONS IN SCHEMA "{s}" FROM PUBLIC',
        # Supabase Data API roles must never reach shop tables.
        "DO $$ DECLARE r text; BEGIN FOREACH r IN ARRAY "
        + _SUPABASE_ROLES
        + " LOOP IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = r) THEN "
        + f"EXECUTE 'REVOKE ALL ON SCHEMA \"{s}\" FROM ' || quote_ident(r); "
        + f"EXECUTE 'REVOKE ALL ON ALL TABLES IN SCHEMA \"{s}\" FROM ' || quote_ident(r); "
        + f"EXECUTE 'REVOKE ALL ON ALL SEQUENCES IN SCHEMA \"{s}\" FROM ' || quote_ident(r); "
        + f"EXECUTE 'REVOKE ALL ON ALL FUNCTIONS IN SCHEMA \"{s}\" FROM ' || quote_ident(r); "
        + "END IF; END LOOP; END $$",
        f'REVOKE ALL ON ALL TABLES IN SCHEMA "{s}" FROM "{r}"',
        f'GRANT USAGE ON SCHEMA "{s}" TO "{r}"',
    ]
    for table, privileges in RUNTIME_GRANTS.items():
        stmts.append(f'GRANT {privileges} ON "{s}"."{table}" TO "{r}"')
    stmts.append(f'GRANT UPDATE (stock_quantity) ON "{s}"."products" TO "{r}"')
    stmts.append(f'GRANT USAGE ON SEQUENCE "{s}"."order_number_seq" TO "{r}"')
    return stmts


def setup_database(admin_url: str, passwords: Mapping[str, str | None]) -> None:
    """Create schemas and runtime roles. ``passwords`` maps shop_id -> runtime password."""
    statements: list[str] = []
    for shop_id, shop in SHOPS.items():
        statements.extend(setup_statements(shop, passwords.get(shop_id)))
    _run_statements(admin_url, statements)


def apply_grants(admin_url: str) -> None:
    statements: list[str] = []
    for shop in SHOPS.values():
        statements.extend(grants_statements(shop))
    _run_statements(admin_url, statements)


def migrate(admin_url: str, schema: str | None = None) -> None:
    """alembic upgrade head for every shop schema (or one), then (re)apply runtime grants."""
    cfg = Config(str(ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(ROOT / "migrations"))
    cfg.attributes["migration_url"] = admin_url
    if schema:
        cfg.cmd_opts = argparse.Namespace(x=[f"schema={schema}"])
    command.upgrade(cfg, "head")
    apply_grants(admin_url)


def head_revision() -> str:
    from alembic.script import ScriptDirectory

    cfg = Config(str(ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(ROOT / "migrations"))
    head = ScriptDirectory.from_config(cfg).get_current_head()
    assert head is not None
    return head


def render_setup_sql() -> str:
    """SQL for the Supabase SQL editor (manual administrative step). Replace the password placeholders."""
    parts = ["-- Run as the Supabase `postgres` user in the SQL editor. Replace the <PASSWORD_...> placeholders."]
    for shop in SHOPS.values():
        parts.append(f"\n-- {shop.shop_id}")
        parts.extend(stmt + ";" for stmt in setup_statements(shop, None, placeholder=True))
    parts.append("\n-- After `alembic upgrade head` run (python -m app.cli migrate does it automatically):")
    for shop in SHOPS.values():
        parts.append(f"\n-- {shop.shop_id} grants")
        parts.extend(stmt + ";" for stmt in grants_statements(shop))
    return "\n".join(parts) + "\n"


def schema_revision(engine: Engine, schema: str) -> str | None:
    with engine.connect() as conn:
        return conn.execute(text(f'SELECT version_num FROM "{schema}".alembic_version')).scalar()
