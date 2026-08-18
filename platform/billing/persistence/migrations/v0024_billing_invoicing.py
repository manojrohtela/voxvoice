"""Migration 0024 — Invoice Engine (M9.14).

Declarative schema only. Additively creates the seven `cp_billing_*` invoice tables: commercial
invoices, document snapshots, revision history, the number allocation ledger, the per-series
counter, rendered documents, and invoice activity. `ALTER`s nothing, touches no Epic 5–8 table, and
touches no existing Billing table — including M9.2's `cp_billing_invoice`, `cp_billing_credit_note`,
and `cp_billing_run`, which stay exactly as they are.

Every constraint arrives **with** its table rather than as a later addition, and for the numbering
ledger that ordering is load-bearing in a way it is not elsewhere. A number table that existed for
even one deployment without its `(org, series, sequence)` primary key could accumulate two documents
carrying one legal invoice number — and once both have reached a customer, no later migration can
choose between them. A constraint that arrives second is a constraint that arrives too late.

Idempotent: each table is created only if absent.
"""

from __future__ import annotations

from sqlalchemy import inspect

from ..database import BillingDatabase
from ..invoice_models import INVOICE_TABLES

VERSION = "0024"
DESCRIPTION = (
    "cp_billing_* invoice engine (documents, snapshots, revisions, number ledger, "
    "series counter, renderings, activity)"
)

TABLES = INVOICE_TABLES


def upgrade(database: BillingDatabase) -> None:
    existing = set(inspect(database.engine).get_table_names())
    for table in TABLES:
        if table.__tablename__ not in existing:
            table.__table__.create(database.engine, checkfirst=True)


def downgrade(database: BillingDatabase) -> None:
    """No-op, and for the same reason migration 0023's is.

    A schema rollback is never a licence to discard a document a customer holds, a tax authority has
    seen, or an auditor has examined — nor the number series that proves none is missing. ADR-0023
    IN2: an issued invoice is immutable, and that applies to the schema that holds it."""
    return None
