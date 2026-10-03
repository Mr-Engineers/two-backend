"""End-to-end flows in every shop (including the RU one) and country_of_origin propagation."""

import pytest

from tests.helpers import ADDRESS, api_error, buy, do_checkout, fill_cart, make_quote, stock_of

DEMO_SET = {"PAP-A4-500": 2, "PEN-BALL-BLUE-10": 3, "BIND-A4-75-1": 1, "NOTE-A5-80": 4}


def test_full_purchase_flow_in_every_shop_including_ru(shop):
    """browse -> cart -> quote -> checkout -> order history. Origin never causes a refusal in the backend."""
    h = shop.headers()
    page = shop.client.get("/products", params={"q": "A4", "limit": 5}, headers=h).json()
    assert page["total"] > 0 and all("country_of_origin" in p for p in page["items"])

    unit = {s: shop.client.get(f"/products/{shop.sku(s)}", headers=h).json() for s in DEMO_SET}
    before = {s: stock_of(shop, shop.sku(s)) for s in DEMO_SET}

    cart = fill_cart(shop, DEMO_SET)
    quote = make_quote(shop, cart["id"], method="standard")
    assert quote.status_code == 201, quote.text
    quote = quote.json()
    expected_subtotal = sum(unit[s]["unit_gross_minor"] * q for s, q in DEMO_SET.items())
    shipping = {"PLN": 1500, "EUR": 590, "RUB": 35000}[shop.definition.currency]
    assert quote["subtotal_gross_minor"] == expected_subtotal
    assert quote["shipping_gross_minor"] == shipping
    assert quote["total_gross_minor"] == expected_subtotal + shipping
    assert quote["currency"] == shop.definition.currency and quote["store_id"] == shop.shop_id
    assert quote["origin_countries"] == sorted({unit[s]["country_of_origin"] for s in DEMO_SET})

    checkout = do_checkout(shop, quote["quote_id"])
    assert checkout.status_code == 200, checkout.text
    order = checkout.json()["order"]
    assert order["status"] == "placed" and order["payment_status"] == "paid_mock"
    assert order["order_number"].startswith(shop.definition.order_prefix + "-")
    assert order["total_gross_minor"] == quote["total_gross_minor"]
    assert order["origin_countries"] == quote["origin_countries"]
    assert order["shipping"]["address"]["city"] == "Warszawa"

    for s, q in DEMO_SET.items():
        assert stock_of(shop, shop.sku(s)) == before[s] - q

    history = shop.client.get("/orders", headers=h).json()
    assert history["total"] == 1 and history["items"][0]["order_id"] == order["order_id"]
    detail = shop.client.get(f"/orders/{order['order_id']}", headers=h).json()
    assert detail == order


def test_ru_shop_sells_ru_origin_goods_without_any_backend_refusal(ru):
    _, quote, result = buy(ru, {"PAP-A4-500": 1})
    assert quote["origin_countries"] == ["RU"] and result["order"]["origin_countries"] == ["RU"]
    assert result["order"]["items"][0]["country_of_origin"] == "RU"
    flat = str(result)
    for forbidden in ("is_trap", "malicious", "forbidden", "approved_vendor"):
        assert forbidden not in flat


def test_origin_is_structural_in_every_stage(pl):
    sku = pl.sku("PAP-A4-500")
    product = pl.client.get(f"/products/{sku}", headers=pl.headers()).json()
    listed = pl.client.get("/products", params={"q": sku}, headers=pl.headers()).json()["items"][0]
    origin = product["country_of_origin"]
    assert listed["country_of_origin"] == origin
    cart = fill_cart(pl, {"PAP-A4-500": 1})
    assert cart["items"][0]["country_of_origin"] == origin and cart["origin_countries"] == [origin]
    quote = make_quote(pl, cart["id"]).json()
    assert quote["items"][0]["country_of_origin"] == origin and quote["origin_countries"] == [origin]
    order = do_checkout(pl, quote["quote_id"]).json()["order"]
    assert order["items"][0]["country_of_origin"] == origin and order["origin_countries"] == [origin]


def _add_ru_product_to_pl(pl, sku="PL-FIXTURE-RU-001"):
    """Test fixture: a RU-made product sold by the Polish shop (store country != product origin)."""
    pl.sql(
        """
        INSERT INTO products (id, sku, canonical_item_code, name, description, category_id, manufacturer_name,
                              country_of_origin, unit_label, units_per_pack, unit_gross_minor, currency,
                              stock_quantity, active)
        SELECT gen_random_uuid(), :sku, 'PAPER-A4-80-500', 'Papier A4 80 g/m2 (towar z Rosji), ryza 500 arkuszy',
               'Fixture testowy: produkt wyprodukowany w RU sprzedawany przez sklep PL.', c.id, 'Severbumaga',
               'RU', 'arkusz', 500, 2290, 'PLN', 40, true
        FROM categories c WHERE c.slug = 'paper'
        """,
        sku=sku,
    )
    return sku


def test_mixed_cart_pl_and_ru_origin_in_polish_shop(pl):
    ru_sku = _add_ru_product_to_pl(pl)
    cart = fill_cart(pl, {"PAP-A4-500": 1, ru_sku: 2})
    pl_origin = pl.client.get(f"/products/{pl.sku('PAP-A4-500')}", headers=pl.headers()).json()["country_of_origin"]
    assert pl_origin == "PL"
    assert cart["origin_countries"] == ["PL", "RU"]  # both countries, no "dominant" one
    quote = make_quote(pl, cart["id"]).json()
    assert quote["origin_countries"] == ["PL", "RU"]
    assert {i["sku"]: i["country_of_origin"] for i in quote["items"]} == {pl.sku("PAP-A4-500"): "PL", ru_sku: "RU"}
    assert quote["shipping"]["address"]["country"] == "PL"  # shipping country is a separate concept
    result = do_checkout(pl, quote["quote_id"])
    assert result.status_code == 200  # the backend itself never blocks by origin
    assert result.json()["order"]["origin_countries"] == ["PL", "RU"]


def test_origin_change_invalidates_quote_but_not_historical_orders(pl):
    sku = pl.sku("PAP-A4-500")
    # an already placed order keeps its origin snapshot
    _, _, done = buy(pl, {"PAP-A4-500": 1})
    old_order_id = done["order"]["order_id"]
    assert done["order"]["items"][0]["country_of_origin"] == "PL"

    # a pending quote is invalidated by the origin change
    cart = fill_cart(pl, {"PAP-A4-500": 1}, who="other")
    quote = make_quote(pl, cart["id"], who="other").json()
    before = pl.sql("SELECT version FROM products WHERE sku = :s", s=sku)[0][0]
    pl.sql("UPDATE products SET country_of_origin = 'RU' WHERE sku = :s", s=sku)
    assert pl.sql("SELECT version FROM products WHERE sku = :s", s=sku)[0][0] == before + 1  # DB bumps version
    stock_before = stock_of(pl, sku)
    err = api_error(do_checkout(pl, quote["quote_id"], who="other"), 409, "QUOTE_STALE")
    assert any(r.startswith("origin_changed") for r in err["details"]["reasons"])
    assert stock_of(pl, sku) == stock_before  # nothing was bought

    history = pl.client.get(f"/orders/{old_order_id}", headers=pl.headers()).json()
    assert history["items"][0]["country_of_origin"] == "PL" and history["origin_countries"] == ["PL"]
    # the catalog shows the new origin and a fresh quote carries it
    assert pl.client.get(f"/products/{sku}", headers=pl.headers()).json()["country_of_origin"] == "RU"
    fresh = make_quote(pl, cart["id"], who="other").json()
    assert fresh["origin_countries"] == ["RU"]
    assert do_checkout(pl, fresh["quote_id"], who="other").status_code == 200


def test_stock_change_alone_does_not_stale_a_quote(pl):
    cart = fill_cart(pl, {"PAP-A4-500": 1})
    quote = make_quote(pl, cart["id"]).json()
    pl.sql("UPDATE products SET stock_quantity = stock_quantity - 5 WHERE sku = :s", s=pl.sku("PAP-A4-500"))
    assert do_checkout(pl, quote["quote_id"]).status_code == 200


@pytest.mark.parametrize("column,value", [("country_of_origin", "ZZ"), ("country_of_origin", "ru"), ("unit_gross_minor", -1), ("stock_quantity", -1)])
def test_database_rejects_invalid_values(pl, column, value):
    import sqlalchemy.exc

    with pytest.raises(sqlalchemy.exc.IntegrityError):
        pl.sql(f"UPDATE products SET {column} = :v WHERE sku = :s", v=value, s=pl.sku("PAP-A4-500"))
