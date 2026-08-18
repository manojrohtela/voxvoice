"""Migration 0029 — Billing Reconciliation & Financial Integrity (M9.19).

Declarative schema only. Additively creates the five `cp_billing_*` integrity tables: runs,
findings, repair requests, resolutions, and the audit trail. `ALTER`s nothing, touches no Epic 5–8
table, and touches no existing Billing table — including every table M9.19 reads from and writes to
none of: `cp_billing_ledger_entry`, `cp_billing_invoice_snapshot`, `cp_billing_payment_intent`,
`cp_billing_tax_assessment` and `cp_billing_recognition_entry`.

ADR-0023 **PY5** stays true and gains a durable home: a divergence is recorded, surfaced, and
resolved by a named actor with evidence. These tables are where that record lives.

**Nothing is seeded.** No run, no finding, no relationship configuration. A fresh installation
reconciles nothing until somebody asks it to — the same posture 0025 takes on tax rates, 0026 on
payment routes, 0027 on collection strategies and 0028 on recognition policies. A seeded run would
assert that somebody had checked the books on an installation where nobody had.

Idempotent: each table is created only if absent.
"""

from __future__ import annotations

from sqlalchemy import inspect

from ..database import BillingDatabase
from ..reconciliation_models import RECONCILIATION_TABLES

VERSION = "0029"
DESCRIPTION = (
    "cp_billing_* reconciliation & financial integrity (runs, findings, repair requests, "
    "resolutions, audit trail)"
)

TABLES = RECONCILIATION_TABLES


def upgrade(database: BillingDatabase) -> None:
    existing = set(inspect(database.engine).get_table_names())
    for table in TABLES:
        if table.__tablename__ not in existing:
            table.__table__.create(database.engine, checkfirst=True)


def downgrade(database: BillingDatabase) -> None:
    """No-op, for the same reason migrations 0023–0028 are.

    A schema rollback is never a licence to discard the record that the books were found to
    disagree. These rows are the evidence that a divergence existed, who was told, what was asked
    of whom, and on what grounds it was declared resolved. Dropping them would leave the corrected
    figures in place with no trace of the correction — which is precisely the state an audit reads
    as concealment rather than as a rollback."""
    return None
