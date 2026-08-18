"""Migration 0034 — Durable event log (M10.2).

Declarative schema only — **no application logic**. Additively creates `evt_event`, the
durable append-only record of `core.events.Event` envelopes. It `ALTER`s nothing, touches
no Epic 5–9 table, and touches no migration at or before `0033`.

This is the **first Epic 10 migration** and it continues the platform's single global
chain: the Customer Platform owns `0001`–`0012`, Billing owns `0013`–`0033`, the events
plane starts at `0034`. Epic 9's certified migration head remains `0033` as historical
state — every Epic 9 assertion about it is scoped to `billing.persistence.migrations` and
to the `billing/persistence/migrations` directory, so this migration is invisible to them
(ADR-0034 DS8).

On a fresh database `EventDatabase.create_all()` builds this from the model definitions, so
this migration is a no-op there; it exists for databases that already carry `0001–0033`.
Idempotent: the table is created only if absent.
"""

from __future__ import annotations

from sqlalchemy import inspect

from ..database import EventDatabase
from ..models import EVENT_TABLES

VERSION = "0034"
DESCRIPTION = "evt_event table (durable append-only event log)"

TABLES = EVENT_TABLES


def upgrade(database: EventDatabase) -> None:
    existing = set(inspect(database.engine).get_table_names())
    for table in TABLES:
        if table.__tablename__ not in existing:
            table.__table__.create(database.engine, checkfirst=True)


def downgrade(database: EventDatabase) -> None:
    """No-op. The event log is the durable record of what the runtime did; a schema
    rollback is never a licence to drop the history other milestones re-derive usage from
    (ADR-0032 ED5/ED9)."""
    return None
