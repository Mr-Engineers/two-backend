"""Engine / session factory. One engine per process, bound to one schema from the fixed shop list."""

from __future__ import annotations

from contextlib import contextmanager
from typing import Iterator

from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.config import normalize_database_url
from app.shops import ALL_SCHEMAS


class Database:
    def __init__(
        self,
        url: str,
        schema: str,
        *,
        pool_size: int = 5,
        max_overflow: int = 5,
        pool_timeout: float = 10.0,
        connect_timeout: int = 5,
    ):
        if schema not in ALL_SCHEMAS:
            raise ValueError(f"Schema {schema!r} is not one of the fixed shop schemas")
        self.schema = schema
        self.engine: Engine = create_engine(
            normalize_database_url(url),
            pool_size=pool_size,
            max_overflow=max_overflow,
            pool_timeout=pool_timeout,
            pool_pre_ping=True,
            connect_args={
                "connect_timeout": connect_timeout,
                "application_name": f"shop-{schema}",
                "prepare_threshold": None,  # no server-side prepared statements: safe behind PgBouncer/Supavisor pooling
            },
            execution_options={"schema_translate_map": {None: schema}},
        )
        self._sessionmaker = sessionmaker(self.engine, expire_on_commit=False, autoflush=True)

    @contextmanager
    def begin(self) -> Iterator[Session]:
        """One transaction: commit on success, rollback on any exception."""
        with self._sessionmaker() as session:
            with session.begin():
                yield session

    def dispose(self) -> None:
        self.engine.dispose()
