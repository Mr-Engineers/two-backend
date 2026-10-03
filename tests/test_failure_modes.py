"""Missing database / config / credentials never produce a pretend-successful shop."""

import pytest
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy import create_engine, text

from app.config import Settings
from app.db.models import Base
from app.main import create_app
from tests.helpers import ADDRESS, api_error

DEAD_URL = "postgresql://shop_pl_rt:pw@127.0.0.1:1/postgres"  # nothing listens on port 1


def test_application_refuses_to_start_without_config(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)  # no .env here
    for name in ("SHOP_ID", "DATABASE_URL"):
        monkeypatch.delenv(name, raising=False)
    with pytest.raises(ValidationError):
        create_app()


def test_unreachable_database_never_fakes_success():
    settings = Settings(shop_id="shop-pl", database_url=DEAD_URL, db_connect_timeout=1, app_env="test", _env_file=None)
    with TestClient(create_app(settings), raise_server_exceptions=False) as client:
        assert client.get("/health/live").status_code == 200  # process is alive...
        api_error(client.get("/health/ready"), 503, "DB_UNAVAILABLE")  # ...but not ready
        h = {"Authorization": "Bearer shk_anything_anything_anything"}
        api_error(client.get("/store", headers=h), 503, "DB_UNAVAILABLE")
        api_error(client.get("/products", headers=h), 503, "DB_UNAVAILABLE")
        api_error(client.post("/carts", headers=h), 503, "DB_UNAVAILABLE")
        r = client.post("/checkout", json={"quote_id": "00000000-0000-4000-8000-000000000000", "idempotency_key": "idem-0000001"}, headers=h)
        api_error(r, 503, "DB_UNAVAILABLE")


def test_error_responses_never_contain_connection_strings():
    settings = Settings(shop_id="shop-pl", database_url=DEAD_URL, db_connect_timeout=1, app_env="test", _env_file=None)
    with TestClient(create_app(settings), raise_server_exceptions=False) as client:
        text_ = client.get("/health/ready").text
    for leak in ("shop_pl_rt", "pw@", "127.0.0.1", "postgresql", "psycopg", "Traceback"):
        assert leak not in text_


def test_readiness_reports_ready_for_migrated_seeded_database(shops):
    for shop in shops.values():
        body = shop.client.get("/health/ready").json()
        assert body["status"] == "ready" and body["store_id"] == shop.shop_id


def test_wrong_shop_id_cannot_serve_another_schemas_data(admin_url, db_env):
    """A shop-de process pointed at the shop_pl role must not work: it has no rights in shop_de."""
    from tests.conftest import runtime_url_for

    url = runtime_url_for(admin_url, "shop-pl", db_env["passwords"]["shop-pl"])
    settings = Settings(shop_id="shop-de", database_url=url, app_env="test", _env_file=None)
    with TestClient(create_app(settings), raise_server_exceptions=False) as client:
        r = client.get("/health/ready")
        assert r.status_code >= 500  # permission denied -> never a working shop


def test_models_match_the_alembic_migration(admin_url, db_env):
    engine = create_engine(admin_url.replace("postgresql://", "postgresql+psycopg://", 1))
    with engine.connect() as conn:
        conn.execute(text('SET search_path TO "shop_pl"'))
        ctx = MigrationContext.configure(conn, opts={"compare_type": True, "version_table_schema": "shop_pl"})
        diff = [d for d in compare_metadata(ctx, Base.metadata)
                if not (d[0] == "remove_table" and d[1].name == "alembic_version")]
    engine.dispose()
    assert diff == []
