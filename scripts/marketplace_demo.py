"""Walk through the marketplace flow like the proxy-server does: search -> merchant enrichment -> order.

    python scripts/marketplace_demo.py                                  # happy_path, PAP-A4-80, 38 units
    python scripts/marketplace_demo.py --scenario foreign_cheapest      # loads the scenario first (/admin)
    python scripts/marketplace_demo.py --sku TON-HP-59A --quantity 5 --base http://127.0.0.1:8010

Reads MARKETPLACE_API_TOKEN / MARKETPLACE_ADMIN_TOKEN from .env.local. Only the standard library is used for HTTP.
This client orders the CHEAPEST offer blindly - it is exactly what the proxy must not allow in the risky
scenarios, so use it to see the data, not as a policy.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv  # noqa: E402

from app.marketplace import MARKETPLACE_DEV_PORT  # noqa: E402
from app.marketplace.scenarios import SCENARIO_IDS  # noqa: E402


def call(method: str, url: str, token: str | None, body: dict | None = None, headers: dict | None = None):
    request = urllib.request.Request(url, method=method, data=json.dumps(body).encode() if body is not None else None)
    request.add_header("Content-Type", "application/json")
    if token:
        request.add_header("Authorization", f"Bearer {token}")
    for name, value in (headers or {}).items():
        request.add_header(name, value)
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            return response.status, json.loads(response.read())
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read() or b"{}")


def main() -> int:
    for stream in (sys.stdout, sys.stderr):  # offers contain non-ASCII text; never crash on a legacy console codepage
        stream.reconfigure(errors="replace")  # type: ignore[attr-defined]
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--base", default=f"http://127.0.0.1:{MARKETPLACE_DEV_PORT}")
    parser.add_argument("--env-file", default=str(ROOT / ".env.local"))
    parser.add_argument("--scenario", choices=list(SCENARIO_IDS))
    parser.add_argument("--sku", default="PAP-A4-80")
    parser.add_argument("--quantity", type=int, default=38)
    args = parser.parse_args()
    load_dotenv(args.env_file)
    api = os.environ.get("MARKETPLACE_API_TOKEN") or None
    admin = os.environ.get("MARKETPLACE_ADMIN_TOKEN") or api
    base = args.base.rstrip("/")
    agent_headers = {"X-Request-Id": uuid.uuid4().hex, "X-On-Behalf-Of": "demo-agent"}

    if args.scenario:
        status, body = call("POST", f"{base}/admin/scenarios/{args.scenario}/load", admin)
        print(f"load {args.scenario}: {status} {body}")
        if status != 200:
            return 1

    status, found = call("GET", f"{base}/search?{urllib.parse.urlencode({'sku': args.sku})}", api, headers=agent_headers)
    if status != 200:
        print("search failed:", status, found)
        return 1
    print(f"search {args.sku}: {found['total']} offers")
    for offer in found["offers"]:
        price = offer["unit_price"]
        print(f"  {offer['offer_id']:<12} {price['amount']:>8} {price['currency']}  from {offer['ships_from']}  "
              f"{offer['merchant']['name']} ({offer['merchant']['domain']})")
        print(f"      description: {offer['description']!r}")
    if not found["offers"]:
        return 1

    cheapest = found["offers"][0]
    status, merchant = call("GET", f"{base}/merchants/{cheapest['merchant']['id']}", api, headers=agent_headers)
    print(f"merchant {cheapest['merchant']['id']}: {status} {merchant}")

    status, order = call(
        "POST", f"{base}/orders", api,
        {"offer_id": cheapest["offer_id"], "quantity": args.quantity, "expected_unit_price": cheapest["unit_price"]},
        {"Idempotency-Key": str(uuid.uuid4()), **agent_headers},
    )
    print(f"order: {status} {order}")
    return 0 if status == 201 else 1


if __name__ == "__main__":
    raise SystemExit(main())
