"""Integration test harness: a REAL PostgreSQL with the production layout
(one database, three shop schemas + the marketplace schema, least-privilege runtime roles, Alembic migrations, seed).

Database source:
  * TEST_DATABASE_ADMIN_URL (owner account of a throw-away database, e.g. the Compose Postgres), or
  * a local PostgreSQL started through the `pgserver` wheel (no Docker needed).
The schemas shop_pl/shop_de/shop_ru in that database are DROPPED and recreated.
"""

from __future__ import annotations

import os
import secrets
import socket
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path

import pytest
import uvicorn
from fastapi.testclient import TestClient
from sqlalchemy.engine import make_url

from app import dbadmin
from app.config import Settings
from app.db.session import Database
from app.main import create_app
from app.marketplace import MARKETPLACE_SCHEMA
from app.security import generate_api_key
from app.seed import data, runner
from app.services.shop import ShopService
from app.shops import ALL_SCHEMAS, SHOPS

RESET_ENV = {"ALLOW_DEMO_RESET": "1", "APP_ENV": "test"}


def pytest_configure(config):
    import logging

    logging.raiseExceptions = False  # third-party atexit logging after pytest closed its streams
    config.addinivalue_line("markers", "slow: slower tests")


@pytest.fixture(scope="session")
def admin_url(tmp_path_factory) -> str:
    url = os.environ.get("TEST_DATABASE_ADMIN_URL")
    if url:
        yield url
        return
    try:
        import pgserver
    except ImportError:  # pragma: no cover
        pytest.fail("No database: set TEST_DATABASE_ADMIN_URL or `pip install pgserver` (see README).")
    server = pgserver.get_server(tmp_path_factory.mktemp("pgdata"), cleanup_mode="stop")
    yield server.get_uri()
    server.cleanup()


@dataclass
class Shop:
    shop_id: str
    runtime_url: str
    admin_db: Database
    settings: Settings
    service: ShopService
    client: TestClient
    keys: dict[str, str]
    cart_ids: list[str] = field(default_factory=list)

    @property
    def definition(self):
        return SHOPS[self.shop_id]

    @property
    def sku_prefix(self) -> str:
        return self.definition.order_prefix

    def headers(self, who: str = "agent", **extra: str) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.keys[who]}", **extra}

    def customer_id(self, who: str = "agent") -> uuid.UUID:
        return data.demo_customer_id(self.shop_id, who)

    def sku(self, short: str) -> str:
        return f"{self.sku_prefix}-{short}"

    def sql(self, statement: str, **params):
        """Run SQL as the owner account (tampering / inspection helper)."""
        from sqlalchemy import text

        with self.admin_db.begin() as session:
            session.execute(text(f'SET LOCAL search_path TO "{self.definition.schema}"'))
            result = session.execute(text(statement), params)
            return result.fetchall() if result.returns_rows else None


@pytest.fixture(scope="session")
def db_env(admin_url):
    import psycopg

    url = admin_url.replace("postgresql://", "postgresql://", 1)
    with psycopg.connect(url, autocommit=True) as conn:
        for schema in (*ALL_SCHEMAS, MARKETPLACE_SCHEMA):
            conn.execute(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')
        for role in ("anon", "authenticated", "service_role"):  # Supabase-like Data API roles
            conn.execute(f"DO $$ BEGIN IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname='{role}') THEN CREATE ROLE {role} NOLOGIN; END IF; END $$")
    passwords = {sid: secrets.token_urlsafe(12) for sid in (*SHOPS, dbadmin.MARKETPLACE_ID)}
    keys = {sid: {k: generate_api_key() for k in data.DEMO_CUSTOMERS} for sid in SHOPS}
    dbadmin.setup_database(admin_url, passwords)
    dbadmin.migrate(admin_url)
    for sid, shop in SHOPS.items():
        db = Database(admin_url, shop.schema)
        runner.seed_shop(db, sid, keys[sid])
        db.dispose()
    return {"passwords": passwords, "keys": keys}


def runtime_url_for(admin_url: str, shop_id: str, password: str) -> str:
    u = make_url(admin_url)
    return u.set(username=SHOPS[shop_id].runtime_role, password=password).render_as_string(hide_password=False)


@pytest.fixture(scope="session")
def shops(admin_url, db_env) -> dict[str, Shop]:
    result: dict[str, Shop] = {}
    contexts = []
    for sid, definition in SHOPS.items():
        rt = runtime_url_for(admin_url, sid, db_env["passwords"][sid])
        settings = Settings(shop_id=sid, database_url=rt, app_env="test", _env_file=None)
        app = create_app(settings)
        client = TestClient(app, raise_server_exceptions=False)
        client.__enter__()
        contexts.append(client)
        result[sid] = Shop(
            shop_id=sid,
            runtime_url=rt,
            admin_db=Database(admin_url, definition.schema),
            settings=settings,
            service=app.state.service,
            client=client,
            keys=db_env["keys"][sid],
        )
    yield result
    for client in contexts:
        client.__exit__(None, None, None)
    for shop in result.values():
        shop.admin_db.dispose()


@pytest.fixture(autouse=True)
def fresh_data(request):
    """Every test starts from freshly seeded data (stock restored, no carts/orders)."""
    if "shops" not in request.fixturenames and "live_server" not in request.fixturenames:
        yield
        return
    shops = request.getfixturevalue("shops")
    db_env = request.getfixturevalue("db_env")
    for sid, shop in shops.items():
        runner.reset_demo(shop.admin_db, sid, db_env["keys"][sid], confirm=True, environ=RESET_ENV)
    yield


@pytest.fixture
def pl(shops) -> Shop:
    return shops["shop-pl"]


@pytest.fixture
def de(shops) -> Shop:
    return shops["shop-de"]


@pytest.fixture
def ru(shops) -> Shop:
    return shops["shop-ru"]


@pytest.fixture(params=list(SHOPS))
def shop(request, shops) -> Shop:
    return shops[request.param]


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="session")
def live_server(admin_url, db_env):
    """A real uvicorn server (shop-ru) for the official MCP client over HTTP."""
    sid = "shop-ru"
    rt = runtime_url_for(admin_url, sid, db_env["passwords"][sid])
    settings = Settings(shop_id=sid, database_url=rt, app_env="test", _env_file=None)
    app = create_app(settings)
    port = _free_port()
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.time() + 20
    while not server.started:
        if time.time() > deadline:
            raise RuntimeError("live server did not start")
        time.sleep(0.05)
    yield {"url": f"http://127.0.0.1:{port}", "keys": db_env["keys"][sid], "shop_id": sid}
    server.should_exit = True
    thread.join(timeout=10)
