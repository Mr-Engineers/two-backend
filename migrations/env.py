"""Alembic environment: the same revisions are applied to every fixed shop schema.

    alembic upgrade head                      # all three schemas
    alembic -x schema=shop_pl upgrade head    # one schema

Each schema has its own ``alembic_version`` table. The schema name comes only from the constant list.
"""

from __future__ import annotations

import os
from logging.config import fileConfig

from alembic import context
from sqlalchemy import create_engine, pool

from app.config import normalize_database_url
from app.db.models import Base
from app.shops import ALL_SCHEMAS

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name, disable_existing_loggers=False)

target_metadata = Base.metadata


def _database_url() -> str:
    url = config.attributes.get("migration_url") or os.environ.get("MIGRATION_DATABASE_URL")
    if not url:
        raise RuntimeError("MIGRATION_DATABASE_URL (owner/migration account) is not set")
    return normalize_database_url(url)


def _schemas() -> list[str]:
    selected = context.get_x_argument(as_dictionary=True).get("schema")
    if selected is None:
        return list(ALL_SCHEMAS)
    if selected not in ALL_SCHEMAS:
        raise RuntimeError(f"Unknown schema {selected!r}; allowed: {ALL_SCHEMAS}")
    return [selected]


def run_migrations_online() -> None:
    engine = create_engine(_database_url(), poolclass=pool.NullPool)
    with engine.connect() as connection:
        for schema in _schemas():
            # `schema` is validated against the constant list above.
            connection.exec_driver_sql(f'CREATE SCHEMA IF NOT EXISTS "{schema}"')
            connection.exec_driver_sql(f'SET search_path TO "{schema}"')
            connection.commit()
            context.configure(
                connection=connection,
                target_metadata=target_metadata,
                version_table_schema=schema,
                compare_type=True,
            )
            with context.begin_transaction():
                context.run_migrations()
            connection.commit()
    engine.dispose()


if context.is_offline_mode():
    raise RuntimeError("Offline migrations are not supported (schemas are selected via search_path).")
run_migrations_online()
