"""Start the three shops as three separate uvicorn processes (127.0.0.1:8001/8002/8003).

    python scripts/run_shops.py [--host 127.0.0.1] [--shops shop-pl shop-de shop-ru]

Reads .env.local (see `python -m app.cli gen-credentials`). Every child process receives ONLY its own
SHOP_ID and its own runtime DATABASE_URL: no migration URL, no other shop's password, no demo API keys.
Press Ctrl+C to stop all of them.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
from pathlib import Path

from dotenv import load_dotenv
from sqlalchemy.engine import make_url

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.shops import SHOPS  # noqa: E402

_SECRET_PREFIXES = ("DATABASE_URL", "MIGRATION_DATABASE_URL", "DEMO_API_KEY_", "MARKETPLACE_")
_SECRET_SUFFIXES = ("_DB_PASSWORD",)


def child_env(shop_id: str) -> dict[str, str]:
    env = {
        k: v
        for k, v in os.environ.items()
        if not k.startswith(_SECRET_PREFIXES) and not k.endswith(_SECRET_SUFFIXES) and k not in ("SHOP_ID",)
    }
    url_name = f"DATABASE_URL_{shop_id.upper().replace('-', '_')}"
    url = os.environ.get(url_name)
    if not url:
        raise SystemExit(f"Missing {url_name} in .env.local (run: python -m app.cli gen-credentials).")
    env["SHOP_ID"] = shop_id
    env["DATABASE_URL"] = url
    return env


def validate_shared_database(shop_ids: list[str]) -> None:
    """Reject accidental configuration of the shops against different projects."""
    targets = set()
    for shop_id in shop_ids:
        url = make_url(child_env(shop_id)["DATABASE_URL"])
        tenant = ""
        if url.host and url.host.endswith(".pooler.supabase.com"):
            if not url.username or "." not in url.username:
                raise SystemExit("Supabase pooler usernames must contain the project ref.")
            tenant = url.username.split(".", 1)[1]
        targets.add((url.host, url.port or 5432, url.database, tenant))
    if len(targets) > 1:
        raise SystemExit("All shops must use the same database/project endpoint. Regenerate credentials from one MIGRATION_DATABASE_URL.")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--shops", nargs="*", default=list(SHOPS), choices=list(SHOPS))
    parser.add_argument("--env-file", default=str(ROOT / ".env.local"))
    args = parser.parse_args()
    load_dotenv(args.env_file)
    validate_shared_database(args.shops)

    procs: list[subprocess.Popen] = []
    try:
        for shop_id in args.shops:
            port = SHOPS[shop_id].dev_port
            cmd = [sys.executable, "-m", "uvicorn", "app.main:create_app", "--factory", "--host", args.host, "--port", str(port)]
            procs.append(subprocess.Popen(cmd, cwd=ROOT, env=child_env(shop_id)))
            print(f"{shop_id}: http://{args.host}:{port}  (MCP: /mcp, docs: /docs)", flush=True)
        while all(p.poll() is None for p in procs):
            time.sleep(0.5)
        print("a shop process exited; stopping the others", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 0
    finally:
        for p in procs:
            if p.poll() is None:
                p.terminate()
        for p in procs:
            try:
                p.wait(timeout=10)
            except subprocess.TimeoutExpired:
                p.kill()


if __name__ == "__main__":
    raise SystemExit(main())
