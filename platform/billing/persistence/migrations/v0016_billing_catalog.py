"""Migration 0016 — Commercial Catalog (M9.6).

Declarative schema only. Additively creates the six `cp_billing_*` catalog tables: products,
editions, bundles, feature groups, immutable catalog versions, and append-only catalog
activity. `ALTER`s nothing, touches no Epic 5–8 table, and touches no existing Billing table.

Idempotent: each table is created only if absent.
"""

from __future__ import annotations

from sqlalchemy import inspect

from ..catalog_models import CATALOG_TABLES
from ..database import BillingDatabase

VERSION = "0016"
DESCRIPTION = "cp_billing_* commercial catalog (products, editions, bundles, versions, activity)"

TABLES = CATALOG_TABLES


def upgrade(database: BillingDatabase) -> None:
    existing = set(inspect(database.engine).get_table_names())
    for table in TABLES:
        if table.__tablename__ not in existing:
            table.__table__.create(database.engine, checkfirst=True)


def downgrade(database: BillingDatabase) -> None:
    """No-op. Catalog versions are the snapshots subscriptions and invoices reference; a
    schema rollback is never a licence to make a two-year-old invoice unexplainable."""
    return None
