"""Migration 0023 — Credit & Revenue Ledger (M9.13).

Declarative schema only. Additively creates the seven `cp_billing_*` ledger tables: journal entries,
accounting periods, balance checkpoints, commercial credits, credit applications, ledger activity,
and the per-account sequence counter. `ALTER`s nothing, touches no Epic 5–8 table, and touches no
existing Billing table — including M9.2's `cp_billing_ledger_entry`, `cp_billing_credit_grant`, and
`cp_billing_credit_movement`, which stay exactly as they are.

Every constraint arrives **with** its table rather than as a later addition, and that ordering is
deliberate for the ledger in a way it is not elsewhere. A journal-entry table that existed for even
one deployment without its exactly-once primary key could accumulate two postings for one cause —
and once one of them has reached a statement, no later migration can safely choose between them.
A constraint that arrives second is a constraint that arrives too late.

Idempotent: each table is created only if absent.
"""

from __future__ import annotations

from sqlalchemy import inspect

from ..database import BillingDatabase
from ..ledger_models import LEDGER_TABLES

VERSION = "0023"
DESCRIPTION = (
    "cp_billing_* credit & revenue ledger (entries, periods, checkpoints, credits, "
    "applications, activity, sequence)"
)

TABLES = LEDGER_TABLES


def upgrade(database: BillingDatabase) -> None:
    existing = set(inspect(database.engine).get_table_names())
    for table in TABLES:
        if table.__tablename__ not in existing:
            table.__table__.create(database.engine, checkfirst=True)


def downgrade(database: BillingDatabase) -> None:
    """No-op, and more emphatically here than anywhere else in the chain.

    A schema rollback is never a licence to discard the record of what a customer was charged, what
    they paid, or what an auditor has already examined. ADR-0023 RS1: the ledger is append-only, and
    that applies to the schema that holds it."""
    return None
