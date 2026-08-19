"""Migration 0035 — Durable delivery outbox (M10.6, ADR-0035 DW5).

Declarative schema only — **no application logic**. Additively creates `evt_outbox`, the
mutable per-event delivery state that sits beside the append-only `evt_event`. It `ALTER`s
nothing — `evt_event` is untouched, because giving append-only history an update path would
contradict ED5, which is the whole reason this is a second table.

Continues the platform's single global chain: `0001`–`0012` Customer Platform, `0013`–`0033`
Billing, `0034`–  events plane. Epic 9's certified migration head remains `0033` as
historical state; every Epic 9 assertion about it is scoped to `billing.persistence.migrations`,
so this migration is invisible to them (ADR-0034 DS8). Migrations `0001`–`0034` are not
modified, renumbered or backfilled.

On a fresh database `EventDatabase.create_all()` builds this from the model definitions, so
this migration is a no-op there; it exists for databases that already carry `0001–0034`.
Idempotent: the table is created only if absent.

The `state` column is a plain string, so **M10.7** can add `retrying`, `dead_lettered` and
`quarantined` without a schema migration and without rewriting an M10.6 row (AR-32).
"""

from __future__ import annotations

from sqlalchemy import inspect

from ..database import EventDatabase
from ..models import OUTBOX_TABLES

VERSION = "0035"
DESCRIPTION = "evt_outbox table (durable per-event delivery state: PENDING/DELIVERED)"

TABLES = OUTBOX_TABLES


def upgrade(database: EventDatabase) -> None:
    existing = set(inspect(database.engine).get_table_names())
    for table in TABLES:
        if table.__tablename__ not in existing:
            table.__table__.create(database.engine, checkfirst=True)


def downgrade(database: EventDatabase) -> None:
    """No-op, for the 0034 reason applied to delivery state: dropping the outbox would
    destroy the record of which events are still un-drained — the durable predicate ED9
    requires before retention may delete anything (ADR-0032 ED5/ED9)."""
    return None
