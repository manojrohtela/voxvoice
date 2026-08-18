"""Migration 0018 — Billing Accounts (M9.8).

Declarative schema only. Additively creates the three `cp_billing_*` account tables: the
commercial account book, append-only account activity, and the per-organization account-number
sequence. `ALTER`s nothing, touches no Epic 5–8 table, and touches no existing Billing table —
including M9.2's `cp_billing_account`, which stays exactly as it is.

Idempotent: each table is created only if absent.
"""

from __future__ import annotations

from sqlalchemy import inspect

from ..account_models import ACCOUNT_TABLES
from ..database import BillingDatabase

VERSION = "0018"
DESCRIPTION = "cp_billing_* billing accounts (commercial accounts, activity, number sequence)"

TABLES = ACCOUNT_TABLES


def upgrade(database: BillingDatabase) -> None:
    existing = set(inspect(database.engine).get_table_names())
    for table in TABLES:
        if table.__tablename__ not in existing:
            table.__table__.create(database.engine, checkfirst=True)


def downgrade(database: BillingDatabase) -> None:
    """No-op. Revenue outlives the customer record (ADR-0023 RS4); a schema rollback is never
    a licence to drop the commercial record a retained invoice was raised against."""
    return None
