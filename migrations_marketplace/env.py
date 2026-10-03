"""Alembic environment of the marketplace: one fixed schema (``marketplace``) with its own alembic_version table.

Invoked by ``app.dbadmin.migrate`` (the owner URL is passed as a config attribute) - use
``python -m app.cli migrate``. The shops' migrations live in ``migrations/``.
"""

from __future__ import annotations

import os

from alembic import context
from sqlalchemy import create_engine, pool

from app.config import normalize_database_url
from app.marketplace import MARKETPLACE_SCHEMA
from app.marketplace.models import MarketplaceBase

config = context.config
target_metadata = MarketplaceBase.metadata


def _database_url() -> str:
    url = config.attributes.get("migration_url") or os.environ.get("MIGRATION_DATABASE_URL")
    if not url:
        raise RuntimeError("MIGRATION_DATABASE_URL (owner/migration account) is not set")
    return normalize_database_url(url)


def run_migrations_online() -> None:
    engine = create_engine(_database_url(), poolclass=pool.NullPool)
    with engine.connect() as connection:
        connection.exec_driver_sql(f'CREATE SCHEMA IF NOT EXISTS "{MARKETPLACE_SCHEMA}"')
        connection.exec_driver_sql(f'SET search_path TO "{MARKETPLACE_SCHEMA}"')
        connection.commit()
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            version_table_schema=MARKETPLACE_SCHEMA,
            compare_type=True,
        )
        with context.begin_transaction():
            context.run_migrations()
        connection.commit()
    engine.dispose()


if context.is_offline_mode():
    raise RuntimeError("Offline migrations are not supported (the schema is selected via search_path).")
run_migrations_online()
