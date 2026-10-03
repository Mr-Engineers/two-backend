"""Administrative CLI (uses the MIGRATION/owner account; never used by the running shops).

    python -m app.cli gen-credentials --db-host 127.0.0.1 --db-port 5432 --db-name shops
    python -m app.cli print-setup-sql          # SQL for the Supabase SQL editor
    python -m app.cli setup-db                 # schemas + runtime roles
    python -m app.cli migrate                  # alembic upgrade head for shop_pl/shop_de/shop_ru + grants
    python -m app.cli seed [--shop shop-pl|marketplace]   # idempotent, never restores sold stock
    python -m app.cli load-scenario foreign_cheapest      # marketplace demo scenario (owner account)
    python -m app.cli reset-demo --shop shop-pl --yes   # DESTRUCTIVE, needs ALLOW_DEMO_RESET=true
    python -m app.cli bootstrap                # setup-db + migrate + seed
"""

from __future__ import annotations

import argparse
import os
import secrets
import sys
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote

from dotenv import dotenv_values, load_dotenv
from sqlalchemy.engine import make_url

from app import dbadmin
from app.db.session import Database
from app.marketplace import MARKETPLACE_ROLE, MARKETPLACE_SCHEMA
from app.marketplace.scenarios import SCENARIO_IDS
from app.marketplace.seed import load_scenario, seed_marketplace
from app.security import generate_api_key
from app.seed import data, runner
from app.shops import SHOPS

DEFAULT_ENV_FILE = ".env.local"


def _require_env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        sys.exit(f"Missing required environment variable {name} (see .env.example / gen-credentials).")
    return value


def _password_env(shop_id: str) -> str:
    return f"{shop_id.upper().replace('-', '_')}_DB_PASSWORD"


def _database_url_env(shop_id: str) -> str:
    return f"DATABASE_URL_{shop_id.upper().replace('-', '_')}"


def cmd_gen_credentials(args: argparse.Namespace) -> None:
    path = Path(args.out)
    values = dict(dotenv_values(path)) if path.exists() else {}
    created: list[str] = []

    def ensure(name: str, factory) -> str:
        if not values.get(name):
            values[name] = factory()
            created.append(name)
        return values[name]  # type: ignore[return-value]

    values.setdefault("APP_ENV", "development")
    if args.migration_url:
        values["MIGRATION_DATABASE_URL"] = args.migration_url
    # A single owner URL is the source of host, database, TLS and pooler tenant
    # for every shop. Only the login role and password differ.
    admin_url = values.get("MIGRATION_DATABASE_URL") or os.environ.get("MIGRATION_DATABASE_URL")
    template = make_url(dbadmin.normalize_database_url(admin_url)) if admin_url else None
    if template and template.host and template.host.endswith(".pooler.supabase.com"):
        if template.port == 6543:
            sys.exit("Use the session pooler (port 5432) or direct connection for migrations.")
        if not template.username or "." not in template.username:
            sys.exit("Supabase pooler owner username must include the project ref: postgres.<project-ref>.")
    def runtime_url(role: str, password: str) -> str:
        username = role
        if template and template.host and template.host.endswith(".pooler.supabase.com"):
            username += "." + template.username.split(".", 1)[1]
        if template:
            return template.set(drivername="postgresql", username=username, password=password).render_as_string(
                hide_password=False
            )
        return (
            f"postgresql://{role}:{quote(password, safe='')}@{args.db_host}:{args.db_port}/"
            f"{args.db_name}?sslmode={args.sslmode}"
        )

    for shop_id, shop in SHOPS.items():
        password = ensure(_password_env(shop_id), lambda: secrets.token_urlsafe(24))
        for customer_key in data.DEMO_CUSTOMERS:
            ensure(runner.demo_key_env_name(shop_id, customer_key), generate_api_key)
        values[_database_url_env(shop_id)] = runtime_url(shop.runtime_role, password)
    # Marketplace: runtime DB role + the proxy's bearer token + a separate token for /admin/*.
    marketplace_password = ensure(_password_env(dbadmin.MARKETPLACE_ID), lambda: secrets.token_urlsafe(24))
    values["MARKETPLACE_DATABASE_URL"] = runtime_url(MARKETPLACE_ROLE, marketplace_password)
    ensure("MARKETPLACE_API_TOKEN", lambda: "mkt_" + secrets.token_urlsafe(32))
    ensure("MARKETPLACE_ADMIN_TOKEN", lambda: "mkt_" + secrets.token_urlsafe(32))
    if "SHOP_ID" in values:
        selected = values["SHOP_ID"]
        if selected in SHOPS:
            values["DATABASE_URL"] = values[_database_url_env(selected)]
    path.write_text("".join(f"{k}={v}\n" for k, v in sorted(values.items())), encoding="utf-8")
    print(f"Wrote {path} (new values: {len(created)}). The file contains secrets and is git-ignored.")


def cmd_print_setup_sql(_: argparse.Namespace) -> None:
    sys.stdout.write(dbadmin.render_setup_sql())


def cmd_setup_db(_: argparse.Namespace) -> None:
    passwords = {shop_id: _require_env(_password_env(shop_id)) for shop_id in (*SHOPS, dbadmin.MARKETPLACE_ID)}
    dbadmin.setup_database(_require_env("MIGRATION_DATABASE_URL"), passwords)
    print("Schemas and runtime roles are ready.")


def cmd_migrate(_: argparse.Namespace) -> None:
    dbadmin.migrate(_require_env("MIGRATION_DATABASE_URL"))
    print("Migrations applied and runtime grants refreshed.")


def _shops(selected: str | None) -> list[str]:
    return [selected] if selected else list(SHOPS)


def _seed_marketplace(url: str) -> None:
    db = Database(url, MARKETPLACE_SCHEMA)
    try:
        print(dbadmin.MARKETPLACE_ID, seed_marketplace(db, datetime.now(timezone.utc).date()))
    finally:
        db.dispose()


def cmd_seed(args: argparse.Namespace) -> None:
    url = _require_env("MIGRATION_DATABASE_URL")
    if args.shop == dbadmin.MARKETPLACE_ID:
        _seed_marketplace(url)
        return
    for shop_id in _shops(args.shop):
        db = Database(url, SHOPS[shop_id].schema)
        try:
            print(shop_id, runner.seed_shop(db, shop_id, runner.demo_keys_from_env(shop_id)))
        finally:
            db.dispose()
    if not args.shop:
        _seed_marketplace(url)


def cmd_load_scenario(args: argparse.Namespace) -> None:
    """Replace the marketplace merchants and offers with a demo scenario (owner account)."""
    db = Database(_require_env("MIGRATION_DATABASE_URL"), MARKETPLACE_SCHEMA)
    try:
        print(load_scenario(db, args.scenario_id, datetime.now(timezone.utc).date()))
    finally:
        db.dispose()


def cmd_reset_demo(args: argparse.Namespace) -> None:
    url = _require_env("MIGRATION_DATABASE_URL")
    host = url.split("@")[-1].lower()
    if ("supabase." in host) and not args.i_know_this_is_supabase:
        sys.exit("Refusing to reset a Supabase database without --i-know-this-is-supabase.")
    for shop_id in _shops(args.shop):
        db = Database(url, SHOPS[shop_id].schema)
        try:
            try:
                result = runner.reset_demo(db, shop_id, runner.demo_keys_from_env(shop_id), confirm=args.yes)
            except runner.ResetNotAllowed as exc:
                sys.exit(str(exc))
            print(shop_id, "reset", result)
        finally:
            db.dispose()


def cmd_bootstrap(args: argparse.Namespace) -> None:
    cmd_setup_db(args)
    cmd_migrate(args)
    args.shop = None
    cmd_seed(args)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="python -m app.cli", description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    parser.add_argument("--env-file", default=DEFAULT_ENV_FILE, help="configuration file (e.g. .env or .env.local)")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("gen-credentials", help="generate local DB passwords + demo API keys")
    p.add_argument("--out", default=None)
    p.add_argument("--db-host", default="127.0.0.1")
    p.add_argument("--db-port", default="5432")
    p.add_argument("--db-name", default="postgres")
    p.add_argument("--sslmode", default="prefer", help="use verify-full for Supabase")
    p.add_argument("--migration-url", default=None, help="owner/migration account URL to store in the file")
    p.set_defaults(func=cmd_gen_credentials)

    sub.add_parser("print-setup-sql", help="SQL for the Supabase SQL editor").set_defaults(func=cmd_print_setup_sql)
    sub.add_parser("setup-db", help="create schemas and runtime roles").set_defaults(func=cmd_setup_db)
    sub.add_parser("migrate", help="alembic upgrade head for all schemas + grants").set_defaults(func=cmd_migrate)

    p = sub.add_parser("seed", help="idempotent seed (shops + marketplace base catalog if empty)")
    p.add_argument("--shop", choices=[*SHOPS, dbadmin.MARKETPLACE_ID])
    p.set_defaults(func=cmd_seed)

    p = sub.add_parser("load-scenario", help="replace marketplace merchants/offers with a demo scenario")
    p.add_argument("scenario_id", choices=list(SCENARIO_IDS))
    p.set_defaults(func=cmd_load_scenario)

    p = sub.add_parser("reset-demo", help="DESTRUCTIVE demo reset (opt-in)")
    p.add_argument("--shop", choices=list(SHOPS))
    p.add_argument("--yes", action="store_true")
    p.add_argument("--i-know-this-is-supabase", action="store_true")
    p.set_defaults(func=cmd_reset_demo)

    sub.add_parser("bootstrap", help="setup-db + migrate + seed").set_defaults(func=cmd_bootstrap)

    args = parser.parse_args(argv)
    load_dotenv(args.env_file)
    if args.command == "gen-credentials" and args.out is None:
        args.out = args.env_file
    args.func(args)


if __name__ == "__main__":
    main()
