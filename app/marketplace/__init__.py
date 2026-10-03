"""Marketplace API (contract: docs/contracts/marketplace-api.md in the HackYeah repository).

A separate process with its own PostgreSQL schema (``marketplace``) and its own runtime role. It aggregates
offers of several merchants; the proxy-server calls it on behalf of the purchasing agent.
"""

from __future__ import annotations

# The marketplace lives in the shared ``shops`` schema (one offers table per merchant + the ``shops.offers`` view);
# merchant profiles are ``warehouse.suppliers``. Orders and idempotency records are the only tables created here.
MARKETPLACE_SCHEMA = "shops"
SUPPLIERS_SCHEMA = "warehouse"
MARKETPLACE_ROLE = "marketplace_rt"
MARKETPLACE_DEV_PORT = 8010
# Schema revision this code expects (/health/ready checks it; a test keeps it equal to the Alembic head).
MARKETPLACE_HEAD_REVISION = "m0003"
