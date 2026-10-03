"""Working MCP client (official Python SDK, Streamable HTTP) for the three shops.

    python scripts/mcp_client.py                      # list tools + read-only tour of every shop
    python scripts/mcp_client.py --buy                # additionally buy a small basket through MCP tools
    python scripts/mcp_client.py --shops shop-ru --buy

Each shop is a separate MCP server at http://127.0.0.1:800X/mcp and needs `Authorization: Bearer <api key>`.
The key is read from .env.local (DEMO_API_KEY_<SHOP>_AGENT) - keys are never printed.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import uuid
from pathlib import Path

import anyio
from dotenv import load_dotenv
from mcp import Client
from mcp.client.streamable_http import streamable_http_client
from mcp.shared._httpx_utils import create_mcp_http_client

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.shops import SHOPS  # noqa: E402

BASKET = [("PAPER-A4-80-500", 1), ("PEN-BALLPOINT-BLACK-10", 2)]
ADDRESS = {"recipient_name": "Demo Buyer", "line1": "Demo street 1", "postal_code": "00-001", "city": "Demo City"}
COUNTRY = {"shop-pl": "PL", "shop-de": "DE", "shop-ru": "RU"}


def tool_error(result) -> str:
    text = result.content[0].text if result.content else "unknown error"
    return text


async def run_shop(shop_id: str, url: str, key: str, buy: bool) -> None:
    http = create_mcp_http_client(headers={"Authorization": f"Bearer {key}"})
    async with http:
        async with Client(streamable_http_client(f"{url}/mcp", http_client=http)) as client:
            tools = (await client.list_tools()).tools
            print(f"\n=== {shop_id} @ {url}/mcp - {len(tools)} tools ===")
            for t in tools:
                print(f"  {t.name:<24} class={t.meta.get('io.shop/tool_class'):<20} read_only={bool(t.annotations and t.annotations.read_only_hint)}")

            store = (await client.call_tool("get_store_info", {})).structured_content
            print(f"store: {store['name']} ({store['store_country']}, {store['currency']}, quote TTL {store['quote_ttl_seconds']}s)")
            page = (await client.call_tool("search_products", {"q": "PAPER-A4-80", "limit": 3})).structured_content
            for p in page["items"]:
                print(f"  {p['sku']:<22} {p['unit_gross_decimal']:>8} {p['currency']}  origin={p['country_of_origin']}  stock={p['stock_quantity']}")
            if not buy:
                return

            cart = (await client.call_tool("create_cart", {})).structured_content
            for canonical, qty in BASKET:
                found = (await client.call_tool("search_products", {"q": canonical, "availability": "in_stock", "limit": 5})).structured_content
                product = next(p for p in found["items"] if p["canonical_item_code"] == canonical)
                res = await client.call_tool(
                    "set_cart_item",
                    {"cart_id": cart["id"], "sku": product["sku"], "quantity": qty, "expected_version": cart["version"]},
                )
                if res.is_error:
                    print("  set_cart_item failed:", tool_error(res))
                    return
                cart = res.structured_content
            quote = (await client.call_tool("create_checkout_quote", {
                "cart_id": cart["id"], "shipping_address": {**ADDRESS, "country": COUNTRY[shop_id]}, "shipping_method_code": "standard",
            })).structured_content
            print(f"quote {quote['quote_id']} total {quote['total_gross_decimal']} {quote['currency']} origins={quote['origin_countries']}")
            done = (await client.call_tool("checkout", {"quote_id": quote["quote_id"], "idempotency_key": f"mcp-{uuid.uuid4()}"})).structured_content
            order = done["order"]
            print(f"ORDER {order['order_number']} {order['payment_status']} total {order['total_gross_decimal']} {order['currency']} origins={order['origin_countries']}")


async def main_async(args: argparse.Namespace) -> int:
    failed = False
    for shop_id in args.shops:
        key = os.environ.get(f"DEMO_API_KEY_{shop_id.upper().replace('-', '_')}_AGENT")
        if not key:
            print(f"missing demo key for {shop_id}; run python -m app.cli gen-credentials", file=sys.stderr)
            return 2
        url = f"http://127.0.0.1:{SHOPS[shop_id].dev_port}"
        try:
            await run_shop(shop_id, url, key, args.buy)
        except Exception as exc:  # noqa: BLE001 - a demo script: report and continue with the next shop
            failed = True
            print(f"{shop_id}: FAILED ({type(exc).__name__}: {exc})", file=sys.stderr)
    return 1 if failed else 0


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--shops", nargs="*", default=list(SHOPS), choices=list(SHOPS))
    parser.add_argument("--buy", action="store_true")
    parser.add_argument("--env-file", default=str(ROOT / ".env.local"))
    args = parser.parse_args()
    load_dotenv(args.env_file)
    return anyio.run(main_async, args)


if __name__ == "__main__":
    raise SystemExit(main())
