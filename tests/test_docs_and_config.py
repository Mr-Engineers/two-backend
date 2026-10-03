"""Documentation / configuration artefacts must stay consistent with the code."""

from __future__ import annotations

import re
from pathlib import Path

from app.seed import data
from app.seed.docs import DOC_PATH, render_seed_markdown
from app.shops import SHOPS

ROOT = Path(__file__).resolve().parent.parent


def test_seed_data_doc_is_up_to_date():
    assert DOC_PATH.read_text(encoding="utf-8") == render_seed_markdown(), (
        "docs/seed-data.md is stale: run `python scripts/generate_seed_docs.py`"
    )


def test_seed_data_doc_lists_every_sku_with_origin():
    text = DOC_PATH.read_text(encoding="utf-8")
    for shop_id in SHOPS:
        for product in data.products_for(shop_id):
            assert f"`{product.sku}`" in text


def test_env_example_has_no_secrets():
    text = (ROOT / ".env.example").read_text(encoding="utf-8")
    assert "shk_" not in text
    for line in text.splitlines():
        if line.startswith(("DATABASE_URL", "MIGRATION_DATABASE_URL", "MARKETPLACE_DATABASE_URL")):
            value = line.split("=", 1)[1].split("#", 1)[0].strip()
            assert value == "", line
        if re.match(r"(SHOP_\w+_DB_PASSWORD|MARKETPLACE_DB_PASSWORD|MARKETPLACE_\w+_TOKEN|DEMO_API_KEY_\w+)=", line):
            assert line.split("=", 1)[1].strip() == "", line


def test_requirements_are_pinned():
    for name in ("requirements.txt", "requirements-dev.txt"):
        for line in (ROOT / name).read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith(("#", "-r")):
                assert "==" in line, f"{name}: {line} is not pinned"
