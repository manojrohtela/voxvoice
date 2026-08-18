"""Migration 0026 — Payment Engine (M9.16).

Declarative schema only. Additively creates the eight `cp_billing_*` payment tables: routes,
intents, attempts, provider operations, provider events, refunds, reconciliation findings, and the
audit trail. `ALTER`s nothing, touches no Epic 5–8 table, and touches no existing Billing table —
including M9.2's `cp_billing_payment`, `cp_billing_payment_instrument`, `cp_billing_refund`,
`cp_billing_dunning`, and `cp_billing_reconciliation`, which stay exactly as they are.

**Nothing is seeded.** No provider, no route, no credential, no endpoint. A fresh installation
routes nowhere and can collect nothing at all — the same posture migration 0025 takes on tax rates,
and for the same reason: a provider named in a migration would make this plane the owner of a
commercial relationship it has no authority over, and it would still be here when the contract
ends.

Idempotent: each table is created only if absent.
"""

from __future__ import annotations

from sqlalchemy import inspect

from ..database import BillingDatabase
from ..payment_models import PAYMENT_TABLES

VERSION = "0026"
DESCRIPTION = (
    "cp_billing_* payment engine (routes, intents, attempts, provider operations, provider "
    "events, refunds, reconciliation findings, audit trail)"
)

TABLES = PAYMENT_TABLES


def upgrade(database: BillingDatabase) -> None:
    existing = set(inspect(database.engine).get_table_names())
    for table in TABLES:
        if table.__tablename__ not in existing:
            table.__table__.create(database.engine, checkfirst=True)


def downgrade(database: BillingDatabase) -> None:
    """No-op, for the same reason migrations 0023–0025 are.

    A schema rollback is never a licence to discard the record of money that actually moved — nor
    the operation keys that prove which movements were ours, which is the only evidence that
    distinguishes a duplicate charge from a legitimate second payment. ADR-0023 PY4: the provider
    is the authority for money movement and Billing is the authority for revenue state; dropping
    these tables would leave the second with nothing to say."""
    return None
