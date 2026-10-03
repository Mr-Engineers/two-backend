"""Start the marketplace API (127.0.0.1:8010 by default).

    python scripts/run_marketplace.py [--host 127.0.0.1] [--port 8010] [--env-file .env.local]

Reads .env.local (see `python -m app.cli gen-credentials`). The child process receives ONLY the marketplace
settings: no migration URL, no shop passwords, no shop API keys. Press Ctrl+C to stop.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.marketplace import MARKETPLACE_DEV_PORT  # noqa: E402

_FORWARDED = (
    "SUPABASE_URL", "SUPABASE_KEY", "MARKETPLACE_DATABASE_URL", "MARKETPLACE_API_TOKEN", "MARKETPLACE_ADMIN_TOKEN",
    "MARKETPLACE_ENABLE_ADMIN", "MARKETPLACE_DECREMENT_STOCK", "APP_ENV", "LOG_LEVEL",
    "DB_POOL_SIZE", "DB_MAX_OVERFLOW",
)


def child_env() -> dict[str, str]:
    """Process environment without any secret that does not belong to the marketplace."""
    secret_prefixes = ("DATABASE_URL", "MIGRATION_DATABASE_URL", "DEMO_API_KEY_", "SHOP_")
    env = {
        k: v
        for k, v in os.environ.items()
        if not k.startswith(secret_prefixes) and not k.endswith("_DB_PASSWORD") and not k.startswith("MARKETPLACE_")
    }
    for name in _FORWARDED:
        if name in os.environ:
            env[name] = os.environ[name]
    if not (env.get("SUPABASE_URL") and env.get("SUPABASE_KEY")) and not env.get("MARKETPLACE_DATABASE_URL"):
        raise SystemExit(
            "Missing data source: set SUPABASE_URL + SUPABASE_KEY or MARKETPLACE_DATABASE_URL "
            "(run: python -m app.cli gen-credentials)."
        )
    return env


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=MARKETPLACE_DEV_PORT)
    parser.add_argument("--env-file", default=str(ROOT / ".env.local"))
    args = parser.parse_args()
    load_dotenv(args.env_file)
    # Run uvicorn in this very process (no wrapper process to leak) after dropping every foreign secret.
    env = child_env()
    os.environ.clear()
    os.environ.update(env)
    os.chdir(ROOT)
    import uvicorn

    print(f"marketplace: http://{args.host}:{args.port}  (docs: /docs, OpenAPI: /openapi.json)", flush=True)
    uvicorn.run("app.marketplace.main:create_app", factory=True, host=args.host, port=args.port)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
