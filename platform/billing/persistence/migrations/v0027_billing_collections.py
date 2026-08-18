"""Migration 0027 — Dunning & Collections (M9.17).

Declarative schema only. Additively creates the six `cp_billing_*` collections tables: policy
versions, cases, actions, attempts, decisions, and the audit trail. `ALTER`s nothing, touches no
Epic 5–8 table, and touches no existing Billing table — including M9.2's `cp_billing_dunning`,
which keeps every row, its unique constraint, and its meaning. The richer aggregates land beside
it, as M9.13–M9.16's did beside theirs.

**Nothing is seeded.** No policy, no default strategy, no notifier, no schedule. A fresh
installation collects nothing until an operator publishes a strategy, which is the same posture
migration 0025 takes on tax rates and 0026 takes on payment routes, and for the same reason: a
dunning schedule shipped in a migration would start chasing real customers on an installation
nobody had configured, and it would still be doing it long after the person who deployed it had
moved on.

Idempotent: each table is created only if absent.
"""

from __future__ import annotations

from sqlalchemy import inspect

from ..collection_models import COLLECTION_TABLES
from ..database import BillingDatabase

VERSION = "0027"
DESCRIPTION = (
    "cp_billing_* dunning & collections (policy versions, cases, actions, attempts, decisions, "
    "audit trail)"
)

TABLES = COLLECTION_TABLES


def upgrade(database: BillingDatabase) -> None:
    existing = set(inspect(database.engine).get_table_names())
    for table in TABLES:
        if table.__tablename__ not in existing:
            table.__table__.create(database.engine, checkfirst=True)


def downgrade(database: BillingDatabase) -> None:
    """No-op, for the same reason migrations 0023–0026 are.

    A schema rollback is never a licence to discard the record of what a customer was told and
    when. A collection case is the evidence that a notice went out, that a retry was requested,
    and — after M9.16-R1 — that a recovery was *held* rather than retried because a payment outcome
    was unknown. Dropping these tables would leave a dispute with no answer to "why was I chased
    four times?", which is precisely the question this milestone exists to be able to answer."""
    return None
