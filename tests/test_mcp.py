"""MCP through the OFFICIAL Python SDK client over a real HTTP connection (Streamable HTTP)."""

from __future__ import annotations

import json

import anyio
import httpx
import pytest
from mcp import Client
from mcp.client.streamable_http import streamable_http_client
from mcp.shared._httpx_utils import create_mcp_http_client

from app.mcp_server import TOOL_CLASSES, TOOL_NAMES
from tests.helpers import ADDRESS, new_key


def run_mcp(live, fn, key="agent", headers=None):
    async def main():
        token = live["keys"][key] if key in live["keys"] else key
        http = create_mcp_http_client(headers={"Authorization": f"Bearer {token}", **(headers or {})})
        async with http:
            async with Client(streamable_http_client(f"{live['url']}/mcp", http_client=http)) as client:
                return await fn(client)

    return anyio.run(main)


def error_of(result) -> dict:
    """The SDK prefixes tool failures with 'Error executing tool X: '; the rest is our JSON error payload."""
    assert result.is_error
    text = result.content[0].text
    return json.loads(text[text.index("{"):])["error"]


def rest(live, who="agent"):
    return httpx.Client(base_url=live["url"], headers={"Authorization": f"Bearer {live['keys'][who]}"})


def test_tools_are_identical_typed_and_annotated(live_server, ru):
    tools = run_mcp(live_server, lambda c: c.list_tools()).tools
    by_name = {t.name: t for t in tools}
    assert set(by_name) == set(TOOL_NAMES) and len(TOOL_NAMES) == 15
    for name, tool in by_name.items():
        assert tool.input_schema and tool.output_schema, name  # typed input AND structured output
        assert tool.description
        assert tool.meta["io.shop/tool_class"] == TOOL_CLASSES[name]

    for name in ("search_products", "get_product", "get_cart", "create_checkout_quote", "checkout", "get_order", "list_orders"):
        assert "country_of_origin" in json.dumps(by_name[name].output_schema), name
    for name in ("get_cart", "create_checkout_quote", "get_checkout_quote", "checkout", "get_order"):
        assert "origin_countries" in json.dumps(by_name[name].output_schema), name

    read_only = {n for n, t in by_name.items() if t.annotations.read_only_hint}
    assert read_only == {"get_store_info", "list_categories", "search_products", "get_product", "list_shipping_methods",
                         "get_cart", "get_checkout_quote", "list_orders", "get_order"}
    assert not by_name["create_cart"].annotations.read_only_hint  # creates state
    assert not by_name["create_checkout_quote"].annotations.read_only_hint
    assert by_name["checkout"].annotations.destructive_hint and not by_name["checkout"].annotations.read_only_hint
    assert by_name["checkout"].meta["io.shop/tool_class"] == "purchase/payment_mock"
    assert by_name["set_cart_item"].annotations.idempotent_hint
    # the purchase is a separate sensitive tool, not hidden in a generic one
    assert set(by_name["checkout"].input_schema["properties"]) == {"quote_id", "idempotency_key"}


def test_full_purchase_over_mcp_matches_rest_and_origin_is_structured(live_server, ru):
    async def scenario(c: Client):
        store = (await c.call_tool("get_store_info", {})).structured_content
        assert store["currency"] == "RUB" and store["store_country"] == "RU"
        found = (await c.call_tool("search_products", {"q": "PAP-A4-500"})).structured_content
        assert found["items"][0]["country_of_origin"] == "RU"
        cart = (await c.call_tool("create_cart", {})).structured_content
        for sku, qty in (("RU-PAP-A4-500", 2), ("RU-PEN-BALL-BLUE-10", 1)):
            cart = (await c.call_tool("set_cart_item", {"cart_id": cart["id"], "sku": sku, "quantity": qty})).structured_content
        assert cart["origin_countries"] == ["RU"] and cart["subtotal_gross_minor"] == 2 * 59000 + 35000
        quote = (await c.call_tool("create_checkout_quote", {
            "cart_id": cart["id"], "shipping_address": ADDRESS, "shipping_method_code": "standard"})).structured_content
        assert quote["origin_countries"] == ["RU"] and quote["total_gross_minor"] == 153000 + 35000
        assert quote["items"][0]["country_of_origin"] == "RU"
        read = (await c.call_tool("get_checkout_quote", {"quote_id": quote["quote_id"]})).structured_content
        assert read == quote
        key = new_key()
        done = (await c.call_tool("checkout", {"quote_id": quote["quote_id"], "idempotency_key": key})).structured_content
        again = (await c.call_tool("checkout", {"quote_id": quote["quote_id"], "idempotency_key": key})).structured_content
        assert done["idempotent_replay"] is False and again["idempotent_replay"] is True
        assert again["order"] == done["order"]
        orders = (await c.call_tool("list_orders", {})).structured_content
        assert orders["total"] == 1
        one = (await c.call_tool("get_order", {"order_id": done["order"]["order_id"]})).structured_content
        assert one == done["order"]
        return quote, done["order"]

    quote, order = run_mcp(live_server, scenario)
    assert order["origin_countries"] == ["RU"] and order["payment_status"] == "paid_mock"

    # the very same data/logic through REST (same service layer, same database)
    with rest(live_server) as http:
        assert http.get(f"/orders/{order['order_id']}").json() == order
        assert http.get(f"/quotes/{quote['quote_id']}").json() == quote
        rest_page = http.get("/products", params={"q": "A4", "sort": "price_desc", "limit": 5}).json()
    mcp_page = run_mcp(live_server, lambda c: c.call_tool("search_products", {"q": "A4", "sort": "price_desc", "limit": 5})).structured_content
    assert mcp_page == rest_page
    assert ru.sql("SELECT stock_quantity FROM products WHERE sku = 'RU-PAP-A4-500'")[0][0] == 131 - 2


def test_mcp_errors_use_sdk_error_results_with_structured_codes(live_server, ru):
    async def scenario(c: Client):
        cart = (await c.call_tool("create_cart", {})).structured_content
        bad = await c.call_tool("set_cart_item", {"cart_id": cart["id"], "sku": "RU-PAP-A4-500", "quantity": 0})
        missing = await c.call_tool("get_product", {"sku": "RU-NOPE"})
        stock = await c.call_tool("set_cart_item", {"cart_id": cart["id"], "sku": "RU-PAP-A4-500", "quantity": 100000 // 100})
        return bad, missing, stock

    bad, missing, stock = run_mcp(live_server, scenario)
    for result, code in ((bad, "INVALID_QUANTITY"), (missing, "PRODUCT_NOT_FOUND"), (stock, "INSUFFICIENT_STOCK")):
        assert result.is_error
        payload = error_of(result)
        assert payload["code"] == code and payload["request_id"] and "details" in payload and "Traceback" not in json.dumps(payload)


def test_mcp_tools_do_not_accept_price_or_payment_overrides(live_server, ru):
    async def scenario(c: Client):
        cart = (await c.call_tool("create_cart", {})).structured_content
        try:
            res = await c.call_tool("set_cart_item", {"cart_id": cart["id"], "sku": "RU-PAP-A4-500", "quantity": 1,
                                                      "unit_gross_minor": 1, "country_of_origin": "PL"})
            line = res.structured_content["items"][0] if res.structured_content else None
        except Exception:  # SDK may also reject unknown arguments
            return "rejected"
        return line

    outcome = run_mcp(live_server, scenario)
    assert outcome == "rejected" or (outcome["unit_gross_minor"] == 59000 and outcome["country_of_origin"] == "RU")


def test_mcp_ownership_and_authentication(live_server, ru):
    async def mine(c: Client):
        return (await c.call_tool("create_cart", {})).structured_content["id"]

    cart_id = run_mcp(live_server, mine)
    res = run_mcp(live_server, lambda c: c.call_tool("get_cart", {"cart_id": cart_id}), key="other")
    assert error_of(res)["code"] == "CART_NOT_FOUND"

    with httpx.Client(base_url=live_server["url"]) as http:
        body = {"jsonrpc": "2.0", "id": 1, "method": "tools/list"}
        hdrs = {"Accept": "application/json, text/event-stream", "Content-Type": "application/json"}
        r = http.post("/mcp", json=body, headers=hdrs)
        assert r.status_code == 401 and r.json()["error"]["code"] == "UNAUTHORIZED"
        r = http.post("/mcp", json=body, headers={**hdrs, "Authorization": "Bearer shk_wrong_key_wrong_key_wrong"})
        assert r.status_code == 401
    with pytest.raises(Exception):
        run_mcp(live_server, lambda c: c.list_tools(), key="shk_wrong_key_wrong_key_wrong")


def test_mcp_keeps_integrator_correlation_id_in_audit(live_server, ru):
    run_mcp(live_server, lambda c: c.call_tool("create_cart", {}), headers={"X-Correlation-ID": "gw-corr-42"})
    rows = ru.sql("SELECT operation, correlation_id FROM audit_events")
    assert rows == [("cart.create", "gw-corr-42")]
