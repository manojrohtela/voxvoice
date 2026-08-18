"""Migration 0028 — Revenue Recognition & Deferred Revenue (M9.18).

Declarative schema only. Additively creates the five `cp_billing_*` recognition tables: policy
versions, schedules, entries, adjustments, and the audit trail. `ALTER`s nothing, touches no
Epic 5–8 table, and touches no existing Billing table — including `cp_billing_ledger_entry`, which
keeps sole ownership of financial recording, and `cp_billing_invoice_snapshot`, which keeps sole
ownership of document content. ADR-0023 RS6 stays true: recognition remains a **projection over the
ledger**, and this migration creates no second set of books.

**Nothing is seeded.** No policy, no default method, no schedule. A fresh installation recognizes
nothing until an operator publishes a strategy — the same posture 0025 takes on tax rates, 0026 on
payment routes and 0027 on collection strategies, and for the same reason: a recognition method
shipped in a migration would start deciding when revenue is earned on an installation nobody had
configured, and that decision ends up in a filed number.

Idempotent: each table is created only if absent.
"""

from __future__ import annotations

from sqlalchemy import inspect

from ..database import BillingDatabase
from ..revenue_models import REVENUE_TABLES

VERSION = "0028"
DESCRIPTION = (
    "cp_billing_* revenue recognition (policy versions, schedules, entries, adjustments, "
    "audit trail)"
)

TABLES = REVENUE_TABLES


def upgrade(database: BillingDatabase) -> None:
    existing = set(inspect(database.engine).get_table_names())
    for table in TABLES:
        if table.__tablename__ not in existing:
            table.__table__.create(database.engine, checkfirst=True)


def downgrade(database: BillingDatabase) -> None:
    """No-op, for the same reason migrations 0023–0027 are.

    A schema rollback is never a licence to discard the record of *when revenue was earned and on
    what evidence*. These rows are what a recognition posted into a filed period was derived from —
    the document revision, the snapshot digest, the policy version, the allocation. Dropping them
    would leave a statutory figure with a ledger entry and no explanation, which is the one state
    an audit cannot accept."""
    return None
