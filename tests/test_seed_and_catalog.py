"""Seed data in the real database + catalog browsing."""

import pytest

from app.countries import ISO_3166_1_ALPHA2
from app.db.revision import HEAD_REVISION
from app.seed import data, runner
from app.dbadmin import head_revision
from tests.conftest import RESET_ENV
from tests.helpers import buy, stock_of

EXPECTED_PRICES = {  # sku suffix -> (shop_id -> minor units), values required by the specification
    "PAP-A4-500": {"shop-pl": 2490, "shop-de": 590, "shop-ru": 59000},
    "PEN-BALL-BLUE-10": {"shop-pl": 1490, "shop-de": 390, "shop-ru": 35000},
    "BIND-A4-75-1": {"shop-pl": 1290, "shop-de": 320, "shop-ru": 29000},
    "NOTE-A5-80": {"shop-pl": 890, "shop-de": 240, "shop-ru": 22000},
}


def all_products(shop):
    items, offset = [], 0
    while True:
        r = shop.client.get("/products", params={"limit": 100, "offset": offset}, headers=shop.headers())
        assert r.status_code == 200, r.text
        page = r.json()
        items += page["items"]
        offset += 100
        if offset >= page["total"]:
            return items


def test_migration_revision_constant_matches_alembic_head():
    assert HEAD_REVISION == head_revision()


def test_seed_values_are_complete_and_realistic(shop):
    products = all_products(shop)
    definition = shop.definition
    assert len(products) >= 20
    categories = shop.client.get("/categories", headers=shop.headers()).json()["items"]
    assert len(categories) >= 6 and sum(c["product_count"] for c in categories) == len(products)
    for p in products:
        assert p["currency"] == definition.currency
        assert p["country_of_origin"] in ISO_3166_1_ALPHA2
        assert p["unit_gross_minor"] > 0 and p["stock_quantity"] >= 0
        assert p["units_per_pack"] > 0 and p["unit_label"] and p["name"] and len(p["description"]) > 20
        assert "lorem" not in (p["name"] + p["description"]).lower()
        assert p["sku"].startswith(definition.order_prefix + "-")
        assert 1 <= p["unit_gross_minor"] <= 1_000_000
    assert any(p["stock_quantity"] == 0 for p in products)
    assert any(0 < p["stock_quantity"] <= 5 for p in products)
    assert any(25 <= p["stock_quantity"] <= 500 for p in products)
    assert max(p["stock_quantity"] for p in products) <= 500


def test_specified_demo_prices_use_correct_currency_scale(shop):
    for suffix, prices in EXPECTED_PRICES.items():
        p = shop.client.get(f"/products/{shop.sku(suffix)}", headers=shop.headers()).json()
        assert p["unit_gross_minor"] == prices[shop.shop_id], suffix
    paper = shop.client.get(f"/products/{shop.sku('PAP-A4-500')}", headers=shop.headers()).json()
    assert paper["unit_gross_decimal"] == {"shop-pl": "24.90", "shop-de": "5.90", "shop-ru": "590.00"}[shop.shop_id]


def test_catalog_language_matches_shop(shops):
    names = {sid: s.client.get(f"/products/{s.sku('PAP-A4-500')}", headers=s.headers()).json()["name"] for sid, s in shops.items()}
    assert names["shop-pl"].startswith("Papier ksero") and names["shop-de"].startswith("Kopierpapier")
    assert names["shop-ru"].startswith("A4 copy paper")


def test_origin_policy_of_seed_sets(shops):
    ru_origins = {p["country_of_origin"] for p in all_products(shops["shop-ru"])}
    assert ru_origins == {"RU"}
    for sid in ("shop-pl", "shop-de"):
        origins = {p["country_of_origin"] for p in all_products(shops[sid])}
        assert "RU" not in origins and len(origins) >= 3
    # store country is not product origin: the PL shop sells DE-made goods
    assert any(p["country_of_origin"] == "DE" for p in all_products(shops["shop-pl"]))


def test_at_least_eight_comparable_types_in_all_shops(shops):
    codes = [{p["canonical_item_code"] for p in all_products(s)} for s in shops.values()]
    assert len(codes[0] & codes[1] & codes[2]) >= 8


def test_packaging_variants_are_separate_skus(shop):
    ream = shop.client.get(f"/products/{shop.sku('PAP-A4-500')}", headers=shop.headers()).json()
    carton = shop.client.get(f"/products/{shop.sku('PAP-A4-2500')}", headers=shop.headers()).json()
    assert ream["canonical_item_code"] != carton["canonical_item_code"]
    assert (ream["units_per_pack"], carton["units_per_pack"]) == (500, 2500)


def test_consumable_compatibility_is_explicit(shop):
    staples = shop.client.get(f"/products/{shop.sku('STAPLES-24-6-1000')}", headers=shop.headers()).json()
    assert staples["compatible_with_canonical_codes"] == ["STAPLER-24-6"]


def test_search_filters_sort_and_pagination(shop):
    h = shop.headers()
    get = lambda **p: shop.client.get("/products", params=p, headers=h).json()  # noqa: E731

    by_name = get(q="a4", limit=100)["items"]
    assert by_name and all(
        "a4" in (p["name"] + p["sku"] + p["canonical_item_code"]).lower() for p in by_name
    )
    assert get(q=shop.sku("PAP-A4-500").lower())["total"] == 1  # search by SKU
    assert get(q="PAPER-A3-80-500")["items"][0]["sku"] == shop.sku("PAP-A3-500")  # canonical_item_code
    assert get(q="%")["total"] == 0 and get(q="_")["total"] == 0  # LIKE wildcards are escaped
    assert get(q="nonexistent-xyz")["total"] == 0

    assert all(p["category_slug"] == "paper" for p in get(category="paper", limit=100)["items"])
    assert all(p["stock_quantity"] == 0 for p in get(availability="out_of_stock", limit=100)["items"])
    assert all(p["stock_quantity"] > 0 for p in get(availability="in_stock", limit=100)["items"])
    origin = all_products(shop)[0]["country_of_origin"]
    assert all(p["country_of_origin"] == origin for p in get(origin=origin, limit=100)["items"])

    prices = [p["unit_gross_minor"] for p in get(sort="price_asc", limit=100)["items"]]
    assert prices == sorted(prices)
    # stable pagination: pages do not overlap and cover everything exactly once
    seen, offset = [], 0
    while True:
        page = get(sort="price_desc", limit=7, offset=offset)
        seen += [p["sku"] for p in page["items"]]
        offset += 7
        if offset >= page["total"]:
            break
    assert len(seen) == len(set(seen)) == page["total"]
    assert seen == [p["sku"] for p in get(sort="price_desc", limit=100)["items"]]

    assert shop.client.get("/products", params={"limit": 1000}, headers=h).status_code == 422
    assert shop.client.get("/products", params={"origin": "XX"}, headers=h).status_code == 422


def test_inactive_product_is_hidden_from_lists_but_visible_by_sku(pl):
    listed = {p["sku"] for p in all_products(pl)}
    assert pl.sku("PEN-GEL-BLACK-5") not in listed
    detail = pl.client.get(f"/products/{pl.sku('PEN-GEL-BLACK-5')}", headers=pl.headers()).json()
    assert detail["active"] is False


def test_shipping_methods_have_explicit_costs(shops):
    expected = {"shop-pl": ("PLN", 1500), "shop-de": ("EUR", 590), "shop-ru": ("RUB", 35000)}
    for sid, shop in shops.items():
        methods = shop.client.get("/shipping-methods", headers=shop.headers()).json()["items"]
        assert methods and methods[0]["currency"] == expected[sid][0] and methods[0]["price_gross_minor"] == expected[sid][1]


def test_seed_is_repeatable_and_does_not_restore_sold_stock(pl):
    _, _, order = buy(pl, {"PAP-A4-500": 3})
    sku = pl.sku("PAP-A4-500")
    after_sale = stock_of(pl, sku)
    for _ in range(2):
        runner.seed_shop(pl.admin_db, "shop-pl", pl.keys)
    assert stock_of(pl, sku) == after_sale
    assert pl.sql("SELECT count(*) FROM orders")[0][0] == 1
    assert pl.sql("SELECT count(*) FROM products")[0][0] == len(data.products_for("shop-pl"))
    assert pl.client.get("/orders", headers=pl.headers()).json()["total"] == 1


def test_demo_reset_is_opt_in_and_guarded(pl):
    with pytest.raises(runner.ResetNotAllowed):
        runner.reset_demo(pl.admin_db, "shop-pl", pl.keys, confirm=False, environ=RESET_ENV)
    with pytest.raises(runner.ResetNotAllowed):
        runner.reset_demo(pl.admin_db, "shop-pl", pl.keys, confirm=True, environ={})
    with pytest.raises(runner.ResetNotAllowed):
        runner.reset_demo(pl.admin_db, "shop-pl", pl.keys, confirm=True, environ={"ALLOW_DEMO_RESET": "1", "APP_ENV": "production"})
    buy(pl, {"PAP-A4-500": 1})
    runner.reset_demo(pl.admin_db, "shop-pl", pl.keys, confirm=True, environ=RESET_ENV)
    assert pl.sql("SELECT count(*) FROM orders")[0][0] == 0
    assert stock_of(pl, pl.sku("PAP-A4-500")) == 25
