"""Migration 0014 — Billing revenue records (M9.4).

Declarative schema only. Creates the tables that record *what was consumed and what is
owed*: usage (records, aggregates, quarantine), the append-only revenue ledger, credits,
invoices, the gapless invoice-number sequence, credit notes, and billing runs.

Every table here is append-only or issue-once at the domain level (ADR-0023 RS1/IN2); the
repositories expose no update path for them. Additive and idempotent.
"""

from __future__ import annotations

from sqlalchemy import inspect

from ..database import BillingDatabase
from ..models import (
    BillingCreditGrantRow,
    BillingCreditMovementRow,
    BillingCreditNoteRow,
    BillingInvoiceRow,
    BillingInvoiceSequenceRow,
    BillingLedgerEntryRow,
    BillingRunRow,
    BillingUsageAggregateRow,
    BillingUsageQuarantineRow,
    BillingUsageRecordRow,
)

VERSION = "0014"
DESCRIPTION = "cp_billing_* revenue records (usage, ledger, credits, invoices, runs)"

TABLES = (
    BillingUsageRecordRow,
    BillingUsageAggregateRow,
    BillingUsageQuarantineRow,
    BillingLedgerEntryRow,
    BillingCreditGrantRow,
    BillingCreditMovementRow,
    BillingInvoiceRow,
    BillingInvoiceSequenceRow,
    BillingCreditNoteRow,
    BillingRunRow,
)


def upgrade(database: BillingDatabase) -> None:
    existing = set(inspect(database.engine).get_table_names())
    for table in TABLES:
        if table.__tablename__ not in existing:
            table.__table__.create(database.engine, checkfirst=True)


def downgrade(database: BillingDatabase) -> None:
    """No-op, and deliberately so: the ledger and issued invoices are legal records under a
    retention obligation. A schema rollback is never a licence to delete them."""
    return None
