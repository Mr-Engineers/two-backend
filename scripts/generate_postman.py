"""Generate shareable Postman v2.1 files without database credentials or API keys."""
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from app.api.routers import ALL_ROUTERS
from app.shops import SHOPS

OUT = ROOT / "docs" / "postman"


def event(lines):
    return [{"listen": "test", "script": {"type": "text/javascript", "exec": [
        "pm.test('Request succeeded', () => pm.expect(pm.response.code).to.be.within(200, 299));",
        "if (pm.response.code >= 200 && pm.response.code < 300) {",
        "const data = pm.response.json();", *lines, "}",
    ]}}]


def request(name, method, path, body=None, query=None, capture=None, public=False):
    raw = "{{base_url}}" + path
    url = {"raw": raw, "host": ["{{base_url}}"], "path": path.strip("/").split("/")}
    if query:
        url["query"] = query
        url["raw"] += "?" + "&".join(f"{q['key']}={q['value']}" for q in query if not q.get("disabled"))
    req = {"method": method, "header": [], "url": url}
    if public:
        req["auth"] = {"type": "noauth"}
    if body is not None:
        req["header"].append({"key": "Content-Type", "value": "application/json"})
        req["body"] = {"mode": "raw", "raw": json.dumps(body, indent=2),
                       "options": {"raw": {"language": "json"}}}
    item = {"name": name, "request": req, "response": []}
    if capture:
        item["event"] = event(capture)
    return item


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    cart = ["pm.environment.set('cart_id', data.id);", "pm.environment.set('cart_version', data.version);"]
    items = [
        request("Health: live", "GET", "/health/live", public=True),
        request("Health: ready", "GET", "/health/ready", public=True),
        request("Store", "GET", "/store"),
        request("Categories", "GET", "/categories"),
        request("Products: search and filters", "GET", "/products", query=[
            {"key": k, "value": v, **({"disabled": True} if optional else {})}
            for k, v, optional in [("q", "PAPER", True), ("category", "paper", True),
                ("availability", "in_stock", True), ("origin", "PL", True),
                ("sort", "name", False), ("limit", "20", False), ("offset", "0", False)]
        ]),
        request("Product by SKU", "GET", "/products/{{sku}}"),
        request("Shipping methods", "GET", "/shipping-methods"),
        request("1. Create cart", "POST", "/carts", capture=cart),
        request("2. Set item quantity", "PUT", "/carts/{{cart_id}}/items/{{sku}}", {"quantity": 1}, capture=cart),
        request("3. Get cart", "GET", "/carts/{{cart_id}}", capture=cart),
        request("4. Create quote", "POST", "/carts/{{cart_id}}/quotes", {
            "shipping_method_code": "standard", "shipping_address": {
                "recipient_name": "Demo Customer", "line1": "Example Street 1",
                "postal_code": "00-001", "city": "Demo City", "country": "{{country}}",
                "email": "demo@example.com"}}, capture=[
                    "pm.environment.set('quote_id', data.quote_id);",
                    "pm.environment.set('idempotency_key', pm.variables.replaceIn('{{$guid}}'));",
                ]),
        request("5. Get quote", "GET", "/quotes/{{quote_id}}"),
        request("6. Checkout (mock payment, reduces stock)", "POST", "/checkout", {
            "quote_id": "{{quote_id}}", "idempotency_key": "{{idempotency_key}}"},
            capture=["pm.environment.set('order_id', data.order.order_id);"]),
        request("7. Orders", "GET", "/orders", query=[{"key": "limit", "value": "20"}, {"key": "offset", "value": "0"}]),
        request("8. Order details", "GET", "/orders/{{order_id}}"),
    ]
    mutations = [
        request("Remove item (open cart)", "DELETE", "/carts/{{cart_id}}/items/{{sku}}", capture=cart,
                query=[{"key": "expected_version", "value": "{{cart_version}}", "disabled": True}]),
        request("Clear cart (open cart)", "DELETE", "/carts/{{cart_id}}/items", capture=cart,
                query=[{"key": "expected_version", "value": "{{cart_version}}", "disabled": True}]),
    ]
    actual = {(m, r.path) for router in ALL_ROUTERS for r in router.routes for m in r.methods}
    generated = {(i["request"]["method"], i["request"]["url"]["raw"].split("?")[0]
                  .replace("{{base_url}}", "").replace("{{", "{").replace("}}", "}")) for i in items + mutations}
    assert actual == generated, (actual - generated, generated - actual)
    mcp = []
    for name, payload in [
        ("MCP initialize", {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
            "protocolVersion": "2025-03-26", "capabilities": {}, "clientInfo": {"name": "Postman", "version": "1.0"}}}),
        ("MCP tools list", {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}}),
        ("MCP get store", {"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "get_store_info", "arguments": {}}}),
    ]:
        i = request(name, "POST", "/mcp", payload)
        i["request"]["header"].append({"key": "Accept", "value": "application/json, text/event-stream"})
        mcp.append(i)
    collection = {"info": {"name": "Office Shops Backend", "schema": "https://schema.getpostman.com/json/collection/v2.1.0/collection.json",
        "description": "All REST endpoints, MCP examples and API documentation. Select a shop environment and enter its api_key. Run numbered purchase requests in order. Cart deletion requests require an open cart."},
        "auth": {"type": "bearer", "bearer": [{"key": "token", "value": "{{api_key}}", "type": "string"}]},
        "item": [{"name": "REST: catalog and purchase flow", "item": items},
                 {"name": "Cart deletion: run separately before checkout", "item": mutations},
                 {"name": "MCP", "item": mcp},
                 {"name": "Documentation", "item": [request("Swagger UI", "GET", "/docs", public=True),
                     request("OpenAPI JSON", "GET", "/openapi.json", public=True)]}]}
    write("shops.postman_collection.json", collection)
    for sid, shop in SHOPS.items():
        values = {"base_url": f"http://127.0.0.1:{shop.dev_port}", "api_key": "",
                  "sku": f"{shop.order_prefix}-PAP-A4-500", "country": shop.country,
                  "cart_id": "", "cart_version": "", "quote_id": "", "order_id": "", "idempotency_key": ""}
        write(f"{sid}.postman_environment.json", {"name": sid, "_postman_variable_scope": "environment",
              "values": [{"key": k, "value": v, "enabled": True, "type": "secret" if k == "api_key" else "default"}
                         for k, v in values.items()]})
    print(f"Generated collection: {len(actual)} REST endpoints, MCP examples, docs and 3 environments in {OUT}")


def write(name, value):
    (OUT / name).write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
