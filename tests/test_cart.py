from tests.helpers import ADDRESS, api_error, buy, create_cart, fill_cart, make_quote, set_item, stock_of


def test_cart_lifecycle_and_totals(shop):
    cart = create_cart(shop)
    assert cart["status"] == "open" and cart["version"] == 1 and cart["items"] == [] and cart["subtotal_gross_minor"] == 0
    assert cart["customer_id"] == str(shop.customer_id()) and cart["origin_countries"] == []

    r = set_item(shop, cart["id"], shop.sku("PAP-A4-500"), 2)
    cart = r.json()
    paper = cart["items"][0]
    assert paper["quantity"] == 2 and paper["line_total_gross_minor"] == 2 * paper["unit_gross_minor"]
    assert paper["country_of_origin"] and cart["version"] == 2

    cart = set_item(shop, cart["id"], shop.sku("PEN-BALL-BLUE-10"), 3).json()
    assert cart["subtotal_gross_minor"] == sum(i["line_total_gross_minor"] for i in cart["items"])
    assert cart["subtotal_gross_decimal"].count(".") == 1
    assert cart["origin_countries"] == sorted(set(cart["origin_countries"]))

    cart = shop.client.get(f"/carts/{cart['id']}", headers=shop.headers()).json()
    assert [i["sku"] for i in cart["items"]] == sorted(i["sku"] for i in cart["items"])

    cart = shop.client.delete(f"/carts/{cart['id']}/items/{shop.sku('PEN-BALL-BLUE-10')}", headers=shop.headers()).json()
    assert len(cart["items"]) == 1
    cart = shop.client.delete(f"/carts/{cart['id']}/items", headers=shop.headers()).json()
    assert cart["items"] == [] and cart["subtotal_gross_minor"] == 0


def test_set_quantity_is_absolute_and_retry_safe(pl):
    cart = create_cart(pl)
    sku = pl.sku("PAP-A4-500")
    first = set_item(pl, cart["id"], sku, 4).json()
    again = set_item(pl, cart["id"], sku, 4).json()  # retry
    assert again["items"][0]["quantity"] == 4 and again["version"] == first["version"]  # no change, no version bump
    assert set_item(pl, cart["id"], sku, 2).json()["items"][0]["quantity"] == 2  # update, not increment


def test_expected_version_detects_concurrent_mutation(pl):
    cart = create_cart(pl)
    sku = pl.sku("PAP-A4-500")
    assert set_item(pl, cart["id"], sku, 1, expected_version=1).status_code == 200  # version -> 2
    err = api_error(set_item(pl, cart["id"], sku, 2, expected_version=1), 409, "CART_VERSION_CONFLICT")
    assert err["details"]["current_version"] == 2
    r = pl.client.delete(f"/carts/{cart['id']}/items/{sku}", params={"expected_version": 1}, headers=pl.headers())
    api_error(r, 409, "CART_VERSION_CONFLICT")


def test_invalid_quantities(pl):
    cart = create_cart(pl)
    sku = pl.sku("PAP-A4-500")
    for bad in (0, -1, 1001, 10**9):
        api_error(set_item(pl, cart["id"], sku, bad), 422, "INVALID_QUANTITY")
    for bad in (1.5, "2", True, None):
        assert set_item(pl, cart["id"], sku, bad).status_code == 422  # strict integer validation


def test_unknown_fields_are_rejected_agent_cannot_set_price_or_origin(pl):
    cart = create_cart(pl)
    for extra in ({"unit_gross_minor": 1}, {"price": 1}, {"country_of_origin": "DE"}, {"currency": "EUR"}):
        r = set_item(pl, cart["id"], pl.sku("PAP-A4-500"), 1, **extra)
        api_error(r, 422, "VALIDATION_ERROR")
    assert pl.client.get(f"/carts/{cart['id']}", headers=pl.headers()).json()["items"] == []


def test_unknown_inactive_and_out_of_stock_skus(pl):
    cart = create_cart(pl)
    api_error(set_item(pl, cart["id"], "PL-DOES-NOT-EXIST", 1), 404, "PRODUCT_NOT_FOUND")
    api_error(set_item(pl, cart["id"], pl.sku("PEN-GEL-BLACK-5"), 1), 409, "PRODUCT_INACTIVE")
    err = api_error(set_item(pl, cart["id"], pl.sku("PAP-A4-500"), 26), 409, "INSUFFICIENT_STOCK")  # stock is 25
    assert err["details"] == {"sku": pl.sku("PAP-A4-500"), "requested": 26, "available": 25}
    api_error(set_item(pl, cart["id"], pl.sku("MARK-WB-4"), 1), 409, "INSUFFICIENT_STOCK")  # stock 0


def test_cart_does_not_reserve_stock(pl):
    sku = pl.sku("PAP-A4-500")
    a, b = create_cart(pl), create_cart(pl, "other")
    assert set_item(pl, a["id"], sku, 25).status_code == 200
    assert set_item(pl, b["id"], sku, 25, who="other").status_code == 200  # both fit: nothing reserved
    assert stock_of(pl, sku) == 25


def test_line_limit(pl, monkeypatch):
    monkeypatch.setattr(pl.service.settings, "max_cart_lines", 2)
    cart = create_cart(pl)
    set_item(pl, cart["id"], pl.sku("PAP-A4-500"), 1)
    set_item(pl, cart["id"], pl.sku("PEN-BALL-BLUE-10"), 1)
    api_error(set_item(pl, cart["id"], pl.sku("BIND-A4-75-1"), 1), 422, "CART_LINE_LIMIT")
    assert set_item(pl, cart["id"], pl.sku("PAP-A4-500"), 3).status_code == 200  # existing line still editable


def test_empty_cart_cannot_be_quoted_or_bought(pl):
    cart = create_cart(pl)
    api_error(make_quote(pl, cart["id"]), 409, "EMPTY_CART")


def test_checked_out_cart_is_immutable(pl):
    cart, quote, order = buy(pl, {"PAP-A4-500": 1})
    sku = pl.sku("PEN-BALL-BLUE-10")
    api_error(set_item(pl, cart["id"], sku, 1), 409, "CART_ALREADY_CHECKED_OUT")
    api_error(pl.client.delete(f"/carts/{cart['id']}/items/{pl.sku('PAP-A4-500')}", headers=pl.headers()), 409, "CART_ALREADY_CHECKED_OUT")
    api_error(pl.client.delete(f"/carts/{cart['id']}/items", headers=pl.headers()), 409, "CART_ALREADY_CHECKED_OUT")
    api_error(make_quote(pl, cart["id"]), 409, "CART_ALREADY_CHECKED_OUT")
    final = pl.client.get(f"/carts/{cart['id']}", headers=pl.headers()).json()
    assert final["status"] == "checked_out" and final["checked_out_at"] and len(final["items"]) == 1


def test_missing_cart_and_bad_ids(pl):
    api_error(pl.client.get("/carts/00000000-0000-4000-8000-000000000000", headers=pl.headers()), 404, "CART_NOT_FOUND")
    api_error(pl.client.get("/carts/not-a-uuid", headers=pl.headers()), 422, "VALIDATION_ERROR")
    body = pl.client.get("/carts/not-a-uuid", headers=pl.headers()).text
    assert "not-a-uuid" not in body  # rejected input is not echoed back


def test_cart_lines_hold_only_one_shop_currency(shops):
    for shop in shops.values():
        cart = fill_cart(shop, {"PAP-A4-500": 1, "PEN-BALL-BLUE-10": 1})
        assert cart["currency"] == shop.definition.currency
