"""Import the synthetic catalog specified in Prompt_Claude_Shop_Backends.md.

    python scripts/import_products_supabase.py --dry-run
    python scripts/import_products_supabase.py --env-file .env

Uses the existing deterministic catalog: 24 products and six categories per shop.
Tables must already exist (run app.cli bootstrap or migrate first).
"""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from dotenv import dotenv_values
from sqlalchemy.exc import SQLAlchemyError

from app.config import normalize_database_url
from app.db.session import Database
from app.seed.data import categories_for, products_for
from app.seed.runner import seed_shop
from app.shops import SHOPS


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file", type=Path, default=ROOT / ".env")
    parser.add_argument("--shop", choices=list(SHOPS), help="Import only this shop; default: all three")
    parser.add_argument("--dry-run", action="store_true", help="Print JSON without connecting to the database")
    args = parser.parse_args(argv)
    shops = [args.shop] if args.shop else list(SHOPS)
    if args.dry_run:
        print(json.dumps({sid: {
            "schema": SHOPS[sid].schema,
            "categories": [dict(id=str(cid), slug=slug, name=name, sort_order=order)
                           for cid, slug, name, order in categories_for(sid)],
            "products": [asdict(product) for product in products_for(sid)],
        } for sid in shops}, ensure_ascii=False, indent=2, default=str))
        return 0

    # Read only the chosen file so a stale shell variable cannot select another project.
    values = dotenv_values(args.env_file)
    url = values.get("MIGRATION_DATABASE_URL")
    if not url:
        parser.error("Set MIGRATION_DATABASE_URL in the selected --env-file.")
    try:
        normalize_database_url(url)
    except ValueError:
        parser.error("MIGRATION_DATABASE_URL must be a PostgreSQL connection URL.")
    for sid in shops:
        db = Database(url, SHOPS[sid].schema, pool_size=1, max_overflow=0)
        try:
            result = seed_shop(db, sid, catalog_only=True)
        except SQLAlchemyError:
            # SQLAlchemy exceptions can contain SQL values or credentials.
            print(f"Import failed for {sid}. Check the database connection, owner permissions "
                  "and migrations. This shop's transaction was rolled back. "
                  "Previously completed shops remain imported; rerunning is safe.", file=sys.stderr)
            return 1
        finally:
            db.dispose()
        print(f"{sid} ({SHOPS[sid].schema}): imported {result['products']} products "
              f"and {result['categories']} categories.")
    print("Import completed. Existing stock and orders were preserved.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
