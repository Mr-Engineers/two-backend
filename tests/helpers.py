from __future__ import annotations

import uuid

ADDRESS = {
    "recipient_name": "Jan Testowy",
    "line1": "ul. Przykladowa 12/3",
    "postal_code": "00-001",
    "city": "Warszawa",
    "country": "PL",
    "phone": "+48 600 100 200",
    "email": "jan.testowy@example.test",
}


def new_key() -> str:
    return "idem-" + uuid.uuid4().hex[:16]


def api_error(response, status: int, code: str) -> dict:
    assert response.status_code == status, response.text
    body = response.json()
    assert body["error"]["code"] == code, body
    assert body["error"]["request_id"]
    assert "message" in body["error"] and "details" in body["error"]
    return body["error"]


def create_cart(shop, who="agent") -> dict:
    r = shop.client.post("/carts", headers=shop.headers(who))
    assert r.status_code == 201, r.text
    return r.json()


def set_item(shop, cart_id, sku, quantity, who="agent", **body):
    return shop.client.put(
        f"/carts/{cart_id}/items/{sku}", json={"quantity": quantity, **body}, headers=shop.headers(who)
    )


def fill_cart(shop, items: dict[str, int], who="agent") -> dict:
    """items: {short_sku_suffix_or_full_sku: quantity}. Returns the final cart json."""
    cart = create_cart(shop, who)
    for sku, qty in items.items():
        full = sku if sku.startswith(f"{shop.sku_prefix}-") else shop.sku(sku)
        r = set_item(shop, cart["id"], full, qty, who)
        assert r.status_code == 200, r.text
        cart = r.json()
    return cart


def make_quote(shop, cart_id, who="agent", method=None, address=None, **extra):
    body = {"shipping_address": address or ADDRESS, **extra}
    if method:
        body["shipping_method_code"] = method
    return shop.client.post(f"/carts/{cart_id}/quotes", json=body, headers=shop.headers(who))


def do_checkout(shop, quote_id, key=None, who="agent"):
    return shop.client.post(
        "/checkout", json={"quote_id": quote_id, "idempotency_key": key or new_key()}, headers=shop.headers(who)
    )


def buy(shop, items: dict[str, int], who="agent", method=None):
    """cart -> quote -> checkout. Returns (cart, quote, checkout_response_json)."""
    cart = fill_cart(shop, items, who)
    qr = make_quote(shop, cart["id"], who, method)
    assert qr.status_code == 201, qr.text
    quote = qr.json()
    cr = do_checkout(shop, quote["quote_id"], who=who)
    assert cr.status_code == 200, cr.text
    return cart, quote, cr.json()


def stock_of(shop, sku: str) -> int:
    return shop.sql("SELECT stock_quantity FROM products WHERE sku = :sku", sku=sku)[0][0]
