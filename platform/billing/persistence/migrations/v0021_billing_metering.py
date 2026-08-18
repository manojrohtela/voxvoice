"""Migration 0021 — Metering Engine (M9.11).

Declarative schema only. Additively creates the seven `cp_billing_*` metering tables: the meter
registry, commercial usage, usage periods, windowed aggregate snapshots, quarantined events, usage
corrections, and append-only metering activity. `ALTER`s nothing, touches no Epic 5–8 table, and
touches no existing Billing table — including M9.2's `cp_billing_usage_record`,
`cp_billing_usage_aggregate`, and `cp_billing_usage_quarantine`, which stay exactly as they are.

The unique constraint on `(org_id, source_event_id, meter_code)` arrives with the table rather
than as a later addition, deliberately: a metering table that existed for even one deployment
without it could accumulate duplicate billable facts that no later migration could safely resolve.

Idempotent: each table is created only if absent.
"""

from __future__ import annotations

from sqlalchemy import inspect

from ..database import BillingDatabase
from ..metering_models import METERING_TABLES

VERSION = "0021"
DESCRIPTION = "cp_billing_* metering engine (meters, usage, periods, aggregates, quarantine)"

TABLES = METERING_TABLES


def upgrade(database: BillingDatabase) -> None:
    existing = set(inspect(database.engine).get_table_names())
    for table in TABLES:
        if table.__tablename__ not in existing:
            table.__table__.create(database.engine, checkfirst=True)


def downgrade(database: BillingDatabase) -> None:
    """No-op. Commercial usage is what an invoice was raised from; a schema rollback is never a
    licence to drop the record of what a customer consumed."""
    return None
