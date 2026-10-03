import threading
import uuid
from datetime import datetime, timedelta, timezone

import pytest

from app.errors import AppError
from app.schemas.checkout import QuoteRequest, ShippingAddress
from app.services.payment import MockPaymentProvider
from tests.helpers import ADDRESS, api_error, buy, create_cart, do_checkout, fill_cart, make_quote, new_key, set_item, stock_of


def counts(shop):
    return {t: shop.sql(f"SELECT count(*) FROM {t}")[0][0] for t in ("orders", "order_items", "mock_payments", "idempotency_records")}


# ------------------------------------------------------------------ quotes
def test_quote_is_a_snapshot_with_ttl_and_does_not_buy_or_reserve(pl):
    cart = fill_cart(pl, {"PAP-A4-500": 2})
    stock = stock_of(pl, pl.sku("PAP-A4-500"))
    before = counts(pl)
    q = make_quote(pl, cart["id"], method="express")
    assert q.status_code == 201, q.text
    q = q.json()
    created, expires = (datetime.fromisoformat(q[k]) for k in ("created_at", "expires_at"))
    assert expires - created == timedelta(seconds=300) and q["is_expired"] is False
    assert q["cart_version"] == cart["version"] and q["customer_id"] == str(pl.customer_id()) and q["store_id"] == "shop-pl"
    assert q["shipping"]["method_code"] == "express" and q["shipping_gross_minor"] == 2500
    assert q["items"][0]["product_version"] >= 1 and q["items"][0]["canonical_item_code"] == "PAPER-A4-80-500"
    assert stock_of(pl, pl.sku("PAP-A4-500")) == stock and counts(pl) == before
    again = pl.client.get(f"/quotes/{q['quote_id']}", headers=pl.headers()).json()
    assert again == q


def test_quote_input_validation(pl):
    cart = fill_cart(pl, {"PAP-A4-500": 1})
    api_error(make_quote(pl, cart["id"], method="teleport"), 404, "SHIPPING_METHOD_NOT_FOUND")
    bad = dict(ADDRESS, country="XX")
    api_error(make_quote(pl, cart["id"], address=bad), 422, "VALIDATION_ERROR")
    r = pl.client.post(f"/carts/{cart['id']}/quotes", json={"shipping_address": ADDRESS, "total_gross_minor": 1}, headers=pl.headers())
    api_error(r, 422, "VALIDATION_ERROR")
    api_error(make_quote(pl, cart["id"], expected_cart_version=99), 409, "CART_VERSION_CONFLICT")
    assert make_quote(pl, cart["id"]).json()["shipping"]["method_code"] == "standard"  # default method


def test_quote_fails_for_unavailable_lines(pl):
    cart = fill_cart(pl, {"PAP-A4-500": 5})
    pl.sql("UPDATE products SET stock_quantity = 2 WHERE sku = :s", s=pl.sku("PAP-A4-500"))
    err = api_error(make_quote(pl, cart["id"]), 409, "INSUFFICIENT_STOCK")
    assert err["details"]["available"] == 2


def test_currencies_are_never_mixed_or_converted(shops):
    totals = {}
    for sid, shop in shops.items():
        cart = fill_cart(shop, {"PAP-A4-500": 1})
        q = make_quote(shop, cart["id"]).json()
        assert q["currency"] == cart["currency"] == shop.definition.currency
        assert q["total_gross_minor"] == q["items"][0]["unit_gross_minor"] + q["shipping_gross_minor"]
        totals[sid] = (q["currency"], q["total_gross_minor"])
    assert totals == {"shop-pl": ("PLN", 2490 + 1500), "shop-de": ("EUR", 590 + 590), "shop-ru": ("RUB", 59000 + 35000)}


# ------------------------------------------------------------------ stale / expired
def test_quote_goes_stale_after_price_cart_or_shipping_change(pl):
    sku = pl.sku("PAP-A4-500")
    # price change
    cart = fill_cart(pl, {"PAP-A4-500": 1})
    quote = make_quote(pl, cart["id"]).json()
    pl.sql("UPDATE products SET unit_gross_minor = unit_gross_minor + 100 WHERE sku = :s", s=sku)
    err = api_error(do_checkout(pl, quote["quote_id"]), 409, "QUOTE_STALE")
    assert any(r.startswith("price_changed") for r in err["details"]["reasons"])
    # cart change
    set_item(pl, cart["id"], pl.sku("PEN-BALL-BLUE-10"), 1)
    fresh = make_quote(pl, cart["id"]).json()
    set_item(pl, cart["id"], pl.sku("PEN-BALL-BLUE-10"), 2)
    err = api_error(do_checkout(pl, fresh["quote_id"]), 409, "QUOTE_STALE")
    assert "cart_changed" in err["details"]["reasons"]
    # shipping cost change
    fresh = make_quote(pl, cart["id"]).json()
    pl.sql("UPDATE shipping_methods SET price_gross_minor = 1700 WHERE code = 'standard'")
    err = api_error(do_checkout(pl, fresh["quote_id"]), 409, "QUOTE_STALE")
    assert "shipping_changed" in err["details"]["reasons"]
    assert counts(pl)["orders"] == 0 and counts(pl)["mock_payments"] == 0  # never bought at a new price


def test_inactive_product_makes_quote_stale(pl):
    cart = fill_cart(pl, {"PAP-A4-500": 1})
    quote = make_quote(pl, cart["id"]).json()
    pl.sql("UPDATE products SET active = false WHERE sku = :s", s=pl.sku("PAP-A4-500"))
    err = api_error(do_checkout(pl, quote["quote_id"]), 409, "QUOTE_STALE")
    assert any(r.startswith("product_unavailable") for r in err["details"]["reasons"])


def test_expired_quote(pl, monkeypatch):
    cart = fill_cart(pl, {"PAP-A4-500": 1})
    quote = make_quote(pl, cart["id"]).json()
    monkeypatch.setattr(pl.service, "clock", lambda: datetime.now(timezone.utc) + timedelta(seconds=301))
    api_error(do_checkout(pl, quote["quote_id"]), 409, "QUOTE_EXPIRED")
    assert pl.client.get(f"/quotes/{quote['quote_id']}", headers=pl.headers()).json()["is_expired"] is True
    assert counts(pl)["orders"] == 0


def test_insufficient_stock_at_checkout(pl):
    cart = fill_cart(pl, {"PAP-A4-500": 5})
    quote = make_quote(pl, cart["id"]).json()
    pl.sql("UPDATE products SET stock_quantity = 3 WHERE sku = :s", s=pl.sku("PAP-A4-500"))  # sold elsewhere
    err = api_error(do_checkout(pl, quote["quote_id"]), 409, "INSUFFICIENT_STOCK")
    assert err["details"]["items"][0]["available"] == 3
    assert stock_of(pl, pl.sku("PAP-A4-500")) == 3 and counts(pl)["orders"] == 0
    assert pl.client.get(f"/carts/{cart['id']}", headers=pl.headers()).json()["status"] == "open"


# ------------------------------------------------------------------ idempotency
def test_retry_with_same_key_returns_same_order_without_second_charge_or_stock_change(pl):
    sku = pl.sku("PAP-A4-500")
    cart = fill_cart(pl, {"PAP-A4-500": 2})
    quote = make_quote(pl, cart["id"]).json()
    key = new_key()
    first = do_checkout(pl, quote["quote_id"], key).json()
    stock_after = stock_of(pl, sku)
    again = do_checkout(pl, quote["quote_id"], key).json()
    assert first["idempotent_replay"] is False and again["idempotent_replay"] is True
    assert again["order"] == first["order"]
    assert stock_of(pl, sku) == stock_after == 25 - 2
    assert counts(pl) == {"orders": 1, "order_items": 1, "mock_payments": 1, "idempotency_records": 1}


def test_same_key_with_different_payload_conflicts(pl):
    c1, c2 = fill_cart(pl, {"PAP-A4-500": 1}), fill_cart(pl, {"PEN-BALL-BLUE-10": 1})
    q1, q2 = make_quote(pl, c1["id"]).json(), make_quote(pl, c2["id"]).json()
    key = new_key()
    assert do_checkout(pl, q1["quote_id"], key).status_code == 200
    api_error(do_checkout(pl, q2["quote_id"], key), 409, "IDEMPOTENCY_CONFLICT")
    assert counts(pl)["orders"] == 1
    assert pl.client.get(f"/carts/{c2['id']}", headers=pl.headers()).json()["status"] == "open"


def test_new_key_cannot_buy_a_checked_out_cart_again(pl):
    cart = fill_cart(pl, {"PAP-A4-500": 1})
    quote = make_quote(pl, cart["id"]).json()
    assert do_checkout(pl, quote["quote_id"]).status_code == 200
    api_error(do_checkout(pl, quote["quote_id"], new_key()), 409, "CART_ALREADY_CHECKED_OUT")
    assert counts(pl)["orders"] == 1 and stock_of(pl, pl.sku("PAP-A4-500")) == 24


def test_replay_works_after_quote_expiry_and_for_the_owner_only(pl, monkeypatch):
    cart = fill_cart(pl, {"PAP-A4-500": 1})
    quote = make_quote(pl, cart["id"]).json()
    key = new_key()
    first = do_checkout(pl, quote["quote_id"], key).json()
    monkeypatch.setattr(pl.service, "clock", lambda: datetime.now(timezone.utc) + timedelta(hours=1))
    replay = do_checkout(pl, quote["quote_id"], key)  # fresh request = "re-authenticated" owner
    assert replay.status_code == 200 and replay.json()["order"]["order_id"] == first["order"]["order_id"]
    # the same key from another customer is a different scope and cannot see the order
    api_error(do_checkout(pl, quote["quote_id"], key, who="other"), 404, "QUOTE_NOT_FOUND")


def test_idempotency_key_is_mandatory_and_validated(pl):
    cart = fill_cart(pl, {"PAP-A4-500": 1})
    quote = make_quote(pl, cart["id"]).json()
    for body in ({"quote_id": quote["quote_id"]}, {"quote_id": quote["quote_id"], "idempotency_key": "short"},
                 {"quote_id": quote["quote_id"], "idempotency_key": "has space in key"}):
        api_error(pl.client.post("/checkout", json=body, headers=pl.headers()), 422, "VALIDATION_ERROR")
    r = pl.client.post("/checkout", json={"quote_id": quote["quote_id"], "idempotency_key": new_key(), "payment_status": "failed"}, headers=pl.headers())
    api_error(r, 422, "VALIDATION_ERROR")  # the agent cannot influence the payment result


# ------------------------------------------------------------------ payment + rollback
def test_mock_payment_failure_rolls_everything_back(pl, monkeypatch):
    sku = pl.sku("PAP-A4-500")
    cart = fill_cart(pl, {"PAP-A4-500": 3})
    quote = make_quote(pl, cart["id"]).json()
    monkeypatch.setattr(pl.service, "payment", MockPaymentProvider("decline"))
    err = api_error(do_checkout(pl, quote["quote_id"]), 402, "PAYMENT_DECLINED_MOCK")
    assert stock_of(pl, sku) == 25
    assert counts(pl) == {"orders": 0, "order_items": 0, "mock_payments": 0, "idempotency_records": 0}
    assert pl.client.get(f"/carts/{cart['id']}", headers=pl.headers()).json()["status"] == "open"
    assert pl.sql("SELECT last_value, is_called FROM order_number_seq") is not None
    audit = pl.sql("SELECT result, error_code FROM audit_events WHERE operation = 'checkout'")
    assert audit == [("error", "PAYMENT_DECLINED_MOCK")]
    # the quote is still valid: with the provider healthy again the same quote can be bought
    monkeypatch.setattr(pl.service, "payment", MockPaymentProvider("approve"))
    assert do_checkout(pl, quote["quote_id"]).status_code == 200
    assert stock_of(pl, sku) == 22


def test_failure_inside_transaction_leaves_no_partial_order(pl, monkeypatch):
    """Even a crash after the order rows were written must roll back (real transaction, not a mock DB)."""

    class Boom(MockPaymentProvider):
        def charge(self, **kw):
            raise RuntimeError("provider exploded")

    cart = fill_cart(pl, {"PAP-A4-500": 1})
    quote = make_quote(pl, cart["id"]).json()
    monkeypatch.setattr(pl.service, "payment", Boom())
    r = do_checkout(pl, quote["quote_id"])
    api_error(r, 500, "INTERNAL_ERROR")
    assert "provider exploded" not in r.text
    assert counts(pl)["orders"] == 0 and stock_of(pl, pl.sku("PAP-A4-500")) == 25


# ------------------------------------------------------------------ concurrency
def _prepare_competing_carts(shop, n, stock=1, sku_short="PAP-A4-500"):
    sku = shop.sku(sku_short)
    shop.sql("UPDATE products SET stock_quantity = :n WHERE sku = :s", n=stock, s=sku)
    quotes = []
    for _ in range(n):
        # carts are created while stock is still sufficient for each of them individually
        shop.sql("UPDATE products SET stock_quantity = 100 WHERE sku = :s", s=sku)
        cart = fill_cart(shop, {sku_short: 1})
        quotes.append(make_quote(shop, cart["id"]).json()["quote_id"])
    shop.sql("UPDATE products SET stock_quantity = :n WHERE sku = :s", n=stock, s=sku)
    return sku, quotes


def test_two_checkouts_for_the_last_unit_only_one_wins(pl):
    sku, quotes = _prepare_competing_carts(pl, 2, stock=1)
    barrier = threading.Barrier(2)
    results = []

    def run(quote_id):
        barrier.wait()
        try:
            results.append(("ok", pl.service.checkout(pl.customer_id(), uuid.UUID(quote_id), new_key())))
        except AppError as exc:
            results.append(("err", exc.code))

    threads = [threading.Thread(target=run, args=(q,)) for q in quotes]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert sorted(r[0] for r in results) == ["err", "ok"]
    assert [r[1] for r in results if r[0] == "err"] == ["INSUFFICIENT_STOCK"]
    assert stock_of(pl, sku) == 0 and counts(pl)["orders"] == 1 and counts(pl)["mock_payments"] == 1


def test_many_parallel_checkouts_never_oversell(pl):
    sku, quotes = _prepare_competing_carts(pl, 8, stock=3)
    barrier = threading.Barrier(len(quotes))
    outcomes = []

    def run(quote_id):
        barrier.wait()
        try:
            pl.service.checkout(pl.customer_id(), uuid.UUID(quote_id), new_key())
            outcomes.append("ok")
        except AppError as exc:
            outcomes.append(exc.code)

    threads = [threading.Thread(target=run, args=(q,)) for q in quotes]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert outcomes.count("ok") == 3 and outcomes.count("INSUFFICIENT_STOCK") == 5
    assert stock_of(pl, sku) == 0 and counts(pl)["orders"] == 3


def test_parallel_duplicate_requests_with_same_key_create_one_order(pl):
    cart = fill_cart(pl, {"PAP-A4-500": 2})
    quote_id = make_quote(pl, cart["id"]).json()["quote_id"]
    key = new_key()
    barrier = threading.Barrier(6)
    results = []

    def run():
        barrier.wait()
        try:
            results.append(pl.service.checkout(pl.customer_id(), uuid.UUID(quote_id), key))
        except AppError as exc:
            results.append(exc.code)

    threads = [threading.Thread(target=run) for _ in range(6)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert all(not isinstance(r, str) for r in results), results
    assert len({r.order.order_id for r in results}) == 1
    assert sum(1 for r in results if not r.idempotent_replay) == 1
    assert counts(pl)["orders"] == 1 and stock_of(pl, pl.sku("PAP-A4-500")) == 23


def test_opposite_line_order_checkouts_do_not_deadlock(pl):
    """Products are locked in id order, so carts that list SKUs in opposite order cannot deadlock."""
    skus = ["PAP-A4-500", "PEN-BALL-BLUE-10", "BIND-A4-75-1"]
    quotes = []
    for order in (skus, list(reversed(skus)), skus, list(reversed(skus))):
        cart = create_cart(pl)
        for s in order:
            set_item(pl, cart["id"], pl.sku(s), 1)
        quotes.append(make_quote(pl, cart["id"]).json()["quote_id"])
    barrier = threading.Barrier(len(quotes))
    codes = []

    def run(q):
        barrier.wait()
        try:
            pl.service.checkout(pl.customer_id(), uuid.UUID(q), new_key())
            codes.append("ok")
        except AppError as exc:
            codes.append(exc.code)

    threads = [threading.Thread(target=run, args=(q,)) for q in quotes]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert codes == ["ok"] * 4
