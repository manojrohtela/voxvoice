"""Migration 0019 — Subscription Engine (M9.9).

Declarative schema only. Additively creates the four `cp_billing_*` subscription tables: the
subscription book, immutable subscription versions, append-only subscription activity, and the
per-organization subscription-number sequence. `ALTER`s nothing, touches no Epic 5–8 table, and
touches no existing Billing table — including M9.2's `cp_billing_subscription`, which stays
exactly as it is.

Idempotent: each table is created only if absent.
"""

from __future__ import annotations

from sqlalchemy import inspect

from ..database import BillingDatabase
from ..subscription_models import SUBSCRIPTION_TABLES

VERSION = "0019"
DESCRIPTION = "cp_billing_* subscription engine (agreements, versions, activity, numbers)"

TABLES = SUBSCRIPTION_TABLES


def upgrade(database: BillingDatabase) -> None:
    existing = set(inspect(database.engine).get_table_names())
    for table in TABLES:
        if table.__tablename__ not in existing:
            table.__table__.create(database.engine, checkfirst=True)


def downgrade(database: BillingDatabase) -> None:
    """No-op. A subscription version is what an invoice was raised against; a schema rollback
    is never a licence to drop the record of what a customer agreed to."""
    return None
