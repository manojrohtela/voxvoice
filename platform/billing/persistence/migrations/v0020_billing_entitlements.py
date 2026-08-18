"""Migration 0020 — Entitlement Engine (M9.10).

Declarative schema only. Additively creates the five `cp_billing_*` entitlement tables: the gate
registry, bundles, per-account profiles, immutable published versions, and append-only entitlement
activity. `ALTER`s nothing, touches no Epic 5–8 table, and touches no existing Billing table —
including M9.2's `cp_billing_entitlement_grant` and `cp_billing_entitlement_snapshot`, which stay
exactly as they are.

Idempotent: each table is created only if absent.
"""

from __future__ import annotations

from sqlalchemy import inspect

from ..database import BillingDatabase
from ..entitlement_models import ENTITLEMENT_TABLES

VERSION = "0020"
DESCRIPTION = "cp_billing_* entitlement engine (definitions, bundles, profiles, versions, activity)"

TABLES = ENTITLEMENT_TABLES


def upgrade(database: BillingDatabase) -> None:
    existing = set(inspect(database.engine).get_table_names())
    for table in TABLES:
        if table.__tablename__ not in existing:
            table.__table__.create(database.engine, checkfirst=True)


def downgrade(database: BillingDatabase) -> None:
    """No-op. A published entitlement snapshot is what an enforcing plane acted on; a schema
    rollback is never a licence to drop the record of what a customer was entitled to."""
    return None
