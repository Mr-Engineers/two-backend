"""Shared project configuration; no external database is contacted."""
import argparse

import pytest
from dotenv import dotenv_values
from sqlalchemy.engine import make_url

from app.cli import cmd_gen_credentials
from app.dbadmin import setup_statements
from app.shops import SHOPS
from scripts.run_shops import validate_shared_database


def test_setup_avoids_superuser_only_role_changes():
    statements = setup_statements(SHOPS["shop-pl"], "example-password")
    alters = [stmt for stmt in statements if stmt.startswith("ALTER ROLE")]
    for statement in alters:
        assert not any(flag in statement for flag in ("SUPERUSER", "REPLICATION", "BYPASSRLS"))
    assert any("rolsuper OR rolreplication OR rolbypassrls" in stmt for stmt in statements)
    assert any("LOGIN NOCREATEDB NOCREATEROLE PASSWORD" in stmt for stmt in alters)


@pytest.mark.parametrize("host,user", [
    ("db.example.supabase.co", "postgres"),
    ("aws-1-eu.pooler.supabase.com", "postgres.example"),
])
def test_credentials_share_owner_database(tmp_path, host, user):
    path = tmp_path / ".env"
    path.write_text("SHOP_ID=shop-de\n", encoding="utf-8")
    cmd_gen_credentials(argparse.Namespace(
        out=str(path), migration_url=f"postgresql://{user}:owner@{host}:5432/postgres?sslmode=require",
        db_host="ignored", db_port="5432", db_name="ignored", sslmode="prefer",
    ))
    values = dotenv_values(path)
    for shop_id, shop in SHOPS.items():
        url = make_url(values[f"DATABASE_URL_{shop_id.upper().replace('-', '_')}"])
        assert (url.host, url.port, url.database, url.query["sslmode"]) == (host, 5432, "postgres", "require")
        assert url.username == shop.runtime_role + (".example" if "pooler" in host else "")
        assert url.password == values[f"{shop_id.upper().replace('-', '_')}_DB_PASSWORD"]
    assert values["DATABASE_URL"] == values["DATABASE_URL_SHOP_DE"]


def test_launcher_rejects_different_supabase_tenants(monkeypatch):
    for shop_id, tenant in [("shop-pl", "first"), ("shop-de", "second")]:
        monkeypatch.setenv(f"DATABASE_URL_{shop_id.upper().replace('-', '_')}",
                           f"postgresql://role.{tenant}:pw@aws-1-eu.pooler.supabase.com:5432/postgres")
    with pytest.raises(SystemExit, match="same database"):
        validate_shared_database(["shop-pl", "shop-de"])
