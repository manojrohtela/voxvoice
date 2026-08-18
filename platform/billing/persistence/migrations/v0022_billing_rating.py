"""Migration 0022 — Rating Engine (M9.12).

Declarative schema only. Additively creates the five `cp_billing_*` rating tables: rating runs,
rated charges, rating snapshots, minimum commitments, and append-only rating activity. `ALTER`s
nothing, touches no Epic 5–8 table, and touches no existing Billing table — including M9.2's
`cp_billing_charge_line`, which stays exactly as it is.

The unique constraint on `(org_id, subscription_id, period_start, period_end, basis_digest)`
arrives with the table rather than as a later addition, deliberately: a rating table that existed
for even one deployment without it could accumulate two commercial figures for one month, and no
later migration could safely choose between them once one of them had reached an invoice.

Idempotent: each table is created only if absent.
"""

from __future__ import annotations

from sqlalchemy import inspect

from ..database import BillingDatabase
from ..rating_models import RATING_TABLES

VERSION = "0022"
DESCRIPTION = "cp_billing_* rating engine (runs, charges, snapshots, commitments, activity)"

TABLES = RATING_TABLES


def upgrade(database: BillingDatabase) -> None:
    existing = set(inspect(database.engine).get_table_names())
    for table in TABLES:
        if table.__tablename__ not in existing:
            table.__table__.create(database.engine, checkfirst=True)


def downgrade(database: BillingDatabase) -> None:
    """No-op. A rating run is what an invoice was raised from; a schema rollback is never a
    licence to drop the record of what a customer was charged."""
    return None
