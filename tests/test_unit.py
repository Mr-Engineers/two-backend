"""Pure unit tests (no database)."""

import json
import logging

import pytest
from pydantic import TypeAdapter, ValidationError

from app.config import Settings, normalize_database_url
from app.countries import ISO_3166_1_ALPHA2, CountryCode
from app.logging_setup import JsonFormatter, redact, redact_text
from app.schemas.common import minor_to_decimal
from app.security import generate_api_key, hash_api_key
from app.seed import data
from app.services.payment import MockPaymentProvider
from app.shops import SHOPS


def test_iso_country_list_is_complete_and_validated():
    assert len(ISO_3166_1_ALPHA2) == 249
    adapter = TypeAdapter(CountryCode)
    assert adapter.validate_python("RU") == "RU"
    for bad in ("XX", "pl", "POL", "R", "", "ZZ", "UK"):
        with pytest.raises(ValidationError):
            adapter.validate_python(bad)


@pytest.mark.parametrize(
    "minor,currency,expected",
    [(2490, "PLN", "24.90"), (590, "EUR", "5.90"), (59000, "RUB", "590.00"), (5, "PLN", "0.05"), (0, "EUR", "0.00")],
)
def test_minor_to_decimal(minor, currency, expected):
    assert minor_to_decimal(minor, currency) == expected


def test_decimal_conversion_uses_controlled_rounding_not_float():
    assert data.to_minor("24.90", "PLN") == 2490
    assert data.to_minor("0.285", "EUR") == 29  # half-up, exact Decimal arithmetic
    assert data.to_minor("1.005", "EUR") == 101  # float would give 100
    assert data.to_minor("590.00", "RUB") == 59000


def test_api_keys_are_random_and_stored_as_hash_only():
    k1, k2 = generate_api_key(), generate_api_key()
    assert k1 != k2 and k1.startswith("shk_") and len(k1) > 40
    digest = hash_api_key(k1)
    assert len(digest) == 64 and k1 not in digest and digest == hash_api_key(k1)


def test_database_url_normalisation_accepts_supabase_format():
    assert normalize_database_url("postgresql://u:p@h:5432/d") == "postgresql+psycopg://u:p@h:5432/d"
    assert normalize_database_url("postgres://u:p@h/d").startswith("postgresql+psycopg://")
    with pytest.raises(ValueError):
        normalize_database_url("mysql://u:p@h/d")


def test_settings_require_shop_and_database(monkeypatch):
    for name in ("SHOP_ID", "DATABASE_URL"):
        monkeypatch.delenv(name, raising=False)
    with pytest.raises(ValidationError):
        Settings(_env_file=None)
    with pytest.raises(ValidationError):
        Settings(shop_id="shop-xx", database_url="postgresql://u:p@h/d", _env_file=None)  # not in the fixed list


def test_production_requires_tls_in_database_url():
    with pytest.raises(ValidationError):
        Settings(shop_id="shop-pl", database_url="postgresql://u:p@h/d", app_env="production", _env_file=None)
    Settings(shop_id="shop-pl", database_url="postgresql://u:p@h/d?sslmode=verify-full", app_env="production", _env_file=None)


def test_mock_payment_outcome_is_server_side_only():
    assert MockPaymentProvider("approve").charge(amount_gross_minor=1, currency="PLN", reference="x").approved
    assert not MockPaymentProvider("decline").charge(amount_gross_minor=1, currency="PLN", reference="x").approved
    with pytest.raises(ValueError):
        MockPaymentProvider("maybe")


def test_log_redaction():
    key = generate_api_key()
    text = f"Authorization: Bearer {key} url postgresql://shop_pl_rt:s3cret@db:5432/x"
    cleaned = redact_text(text)
    assert key not in cleaned and "s3cret" not in cleaned
    assert redact({"line1": "Secret St 1", "city": "X", "ok": 1})["line1"] == "[REDACTED]"
    record = logging.LogRecord("t", logging.INFO, __file__, 1, "msg %s", (key,), None)
    record.shipping_address = {"city": "Warszawa"}
    out = json.loads(JsonFormatter().format(record))
    assert key not in json.dumps(out) and out["shipping_address"] == "[REDACTED]"


def test_seed_dataset_is_internally_consistent():
    for shop_id, shop in SHOPS.items():
        products = data.products_for(shop_id)
        assert len(products) >= 20
        assert len({p.sku for p in products}) == len(products)
        assert all(p.country_of_origin in ISO_3166_1_ALPHA2 for p in products)
        assert all(p.unit_gross_minor > 0 and p.stock_quantity >= 0 and p.units_per_pack > 0 for p in products)
        assert all(p.currency == shop.currency for p in products)
        assert len(data.categories_for(shop_id)) >= 6
        assert any(p.stock_quantity == 0 for p in products)
        assert any(0 < p.stock_quantity <= 5 for p in products)
        assert any(not p.active for p in products)
    canonical = [{p.canonical_item_code for p in data.products_for(s)} for s in SHOPS]
    assert len(canonical[0] & canonical[1] & canonical[2]) >= 8
