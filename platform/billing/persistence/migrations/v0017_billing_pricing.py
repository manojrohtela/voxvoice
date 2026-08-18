"""Migration 0017 — Pricing Engine (M9.7).

Declarative schema only. Additively creates the seven `cp_billing_*` pricing tables:
authoring price books, immutable price-book versions, discount policies, promotions, coupons,
immutable org-wide pricing versions, and append-only pricing activity. `ALTER`s nothing,
touches no Epic 5–8 table, and touches no existing Billing table — including the M9.6 catalog
tables, which pricing references by version number rather than by foreign key.

Idempotent: each table is created only if absent.
"""

from __future__ import annotations

from sqlalchemy import inspect

from ..database import BillingDatabase
from ..pricing_models import PRICING_TABLES

VERSION = "0017"
DESCRIPTION = "cp_billing_* pricing engine (books, versions, discounts, promotions, coupons)"

TABLES = PRICING_TABLES


def upgrade(database: BillingDatabase) -> None:
    existing = set(inspect(database.engine).get_table_names())
    for table in TABLES:
        if table.__tablename__ not in existing:
            table.__table__.create(database.engine, checkfirst=True)


def downgrade(database: BillingDatabase) -> None:
    """No-op. A published price is what an invoice was rated against; a schema rollback is
    never a licence to make a two-year-old charge unexplainable."""
    return None
