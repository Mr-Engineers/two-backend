"""Buy the same comparable set in all three shops (PL, DE, RU) over plain REST and print the origin.

No gateway / AI_CONTROL_LAYER is involved - this is a direct integrator-style client.

    python scripts/demo.py                       # shops on 127.0.0.1:8001/8002/8003, keys from .env.local
    python scripts/demo.py --shops shop-ru       # only one shop
    python scripts/demo.py --base shop-ru=http://127.0.0.1:8003

Uses only the standard library (+ python-dotenv for .env.local). Every run buys real (mock-paid) goods in the demo
database, so stock decreases and is never restored by the seed.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv  # noqa: E402

from app.shops import CURRENCY_EXPONENT, SHOPS  # noqa: E402

# Comparable items (same canonical_item_code in every shop) and pack quantities.
BASKET = [
    ("PAPER-A4-80-500", 1),
    ("PEN-BALLPOINT-BLACK-10", 2),
    ("PEN-BALLPOINT-BLUE-10", 1),
    ("NOTEBOOK-A5-80", 1),
]

ADDRESSES = {
    "shop-pl": {"recipient_name": "Jan Demo", "line1": "ul. Przykladowa 1", "postal_code": "00-001", "city": "Warszawa", "country": "PL"},
    "shop-de": {"recipient_name": "Max Demo", "line1": "Beispielstrasse 1", "postal_code": "10115", "city": "Berlin", "country": "DE"},
    "shop-ru": {"recipient_name": "Ivan Demo", "line1": "Primernaya ul. 1", "postal_code": "101000", "city": "Moskva", "country": "RU"},
}


class ApiError(Exception):
    pass


def call(base: str, key: str, method: str, path: str, body: dict | None = None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(
        base + path, data=data, method=method,
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json", "Accept": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as exc:
        payload = json.loads(exc.read() or b"{}")
        err = payload.get("error", {})
        raise ApiError(f"{method} {path} -> {exc.code} {err.get('code')}: {err.get('message')}") from None
    except urllib.error.URLError as exc:
        raise ApiError(f"{method} {path}: cannot connect ({exc.reason})") from None


def money(minor: int, decimal: str, currency: str) -> str:
    return f"{decimal} {currency} ({minor} minor)"


def buy_in_shop(shop_id: str, base: str, key: str) -> dict:
    store = call(base, key, "GET", "/store")
    print(f"\n=== {store['name']} ({shop_id}) - store country {store['store_country']}, currency {store['currency']} ===")

    cart = call(base, key, "POST", "/carts")
    for canonical, qty in BASKET:
        found = call(base, key, "GET", f"/products?q={canonical}&availability=in_stock&limit=5")
        match = next((p for p in found["items"] if p["canonical_item_code"] == canonical and p["active"]), None)
        if match is None:
            raise ApiError(f"{shop_id}: no active in-stock product for {canonical}")
        cart = call(base, key, "PUT", f"/carts/{cart['id']}/items/{match['sku']}", {"quantity": qty, "expected_version": cart["version"]})

    quote = call(base, key, "POST", f"/carts/{cart['id']}/quotes", {
        "shipping_address": ADDRESSES[shop_id], "shipping_method_code": "standard", "expected_cart_version": cart["version"]})
    cur = quote["currency"]
    exp = CURRENCY_EXPONENT[cur]
    print(f"{'SKU':<24}{'qty':>4}  {'origin':<7}{'unit':>26}{'line total':>26}")
    for line in quote["items"]:
        unit = f"{line['unit_gross_minor'] / 10 ** exp:.{exp}f} {cur}"
        total = f"{line['line_total_gross_minor'] / 10 ** exp:.{exp}f} {cur}"
        print(f"{line['sku']:<24}{line['quantity']:>4}  {line['country_of_origin']:<7}{unit:>26}{total:>26}")
    print(f"origin_countries (quote): {quote['origin_countries']}")
    print(f"subtotal {quote['subtotal_gross_decimal']} + shipping {quote['shipping']['price_gross_decimal']} = total {money(quote['total_gross_minor'], quote['total_gross_decimal'], cur)}")
    print(f"quote {quote['quote_id']} expires {quote['expires_at']}")

    idem = f"demo-{uuid.uuid4()}"
    done = call(base, key, "POST", "/checkout", {"quote_id": quote["quote_id"], "idempotency_key": idem})
    order = done["order"]
    again = call(base, key, "POST", "/checkout", {"quote_id": quote["quote_id"], "idempotency_key": idem})
    assert again["order"]["order_id"] == order["order_id"] and again["idempotent_replay"] is True
    print(f"ORDER {order['order_number']} status={order['status']} payment={order['payment_status']} "
          f"total={money(order['total_gross_minor'], order['total_gross_decimal'], cur)} origin_countries={order['origin_countries']}")
    print("replay with the same idempotency key returned the same order (no second charge, no second stock decrement)")
    return order


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--shops", nargs="*", default=list(SHOPS), choices=list(SHOPS))
    parser.add_argument("--base", action="append", default=[], metavar="SHOP=URL", help="override a base URL")
    parser.add_argument("--env-file", default=str(ROOT / ".env.local"))
    parser.add_argument("--customer", default="agent", choices=["agent", "other"])
    args = parser.parse_args()
    load_dotenv(args.env_file)

    bases = {sid: f"http://127.0.0.1:{s.dev_port}" for sid, s in SHOPS.items()}
    for item in args.base:
        sid, _, url = item.partition("=")
        bases[sid] = url.rstrip("/")

    orders: dict[str, dict] = {}
    for shop_id in args.shops:
        env_name = f"DEMO_API_KEY_{shop_id.upper().replace('-', '_')}_{args.customer.upper()}"
        key = os.environ.get(env_name)
        if not key:
            print(f"Missing {env_name}. Run: python -m app.cli gen-credentials ...", file=sys.stderr)
            return 2
        try:
            orders[shop_id] = buy_in_shop(shop_id, bases[shop_id], key)
        except ApiError as exc:
            print(f"FAILED: {exc}", file=sys.stderr)
            return 1

    print("\n=== Summary ===")
    for shop_id, order in orders.items():
        print(f"{shop_id:<8} {order['order_number']:<20} {order['total_gross_decimal']:>10} {order['currency']}  origins={','.join(order['origin_countries'])}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
