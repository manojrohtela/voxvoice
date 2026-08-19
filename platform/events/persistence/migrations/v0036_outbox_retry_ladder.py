"""Migration 0036 — Outbox retry ladder, dead-letter and quarantine (M10.7, ADR-0036 RL11).

Declarative schema only — **no application logic**. Two additive changes:

  1. `evt_outbox` gains the five retry/terminal columns (`attempt_count`, `next_attempt_at`,
     `dead_lettered_at`, `quarantined_at`, `quarantine_reason`) plus the index `due(now)` reads;
  2. `evt_outbox_failure` is created — the append-only per-attempt failure evidence.

`evt_event` is **untouched**. Giving append-only history an update path would contradict ED5, and
is the whole reason delivery state lives in a second table.

**This is the first `ALTER TABLE` in the chain.** `0034` and `0035` are create-only, so the
pattern is new and is deliberately kept minimal: `ADD COLUMN` with a default, one column at a
time, guarded by an existence check. No table is rebuilt, no data is copied, and no row is
rewritten — an existing `pending` row simply acquires `attempt_count = 0`, which is exactly what
it had implicitly. **No general schema-migration framework is introduced** (still no Alembic).

Continues the platform's single global chain: `0001`–`0012` Customer Platform, `0013`–`0033`
Billing, `0034`– events plane. Epic 9's certified migration head remains `0033` as historical
state; every Epic 9 assertion about it is scoped to `billing.persistence.migrations`, so this
migration is invisible to them (ADR-0034 DS8). Migrations `0001`–`0035` are not modified,
renumbered or backfilled.

On a fresh database `EventDatabase.create_all()` builds everything from the model definitions, so
this migration is a no-op there; it exists for databases already carrying `0001–0035`. Idempotent:
each column is added only if absent, and the table is created only if absent.
"""

from __future__ import annotations

from sqlalchemy import inspect, text

from ..database import EventDatabase
from ..models import OUTBOX_FAILURE_TABLES, OUTBOX_RETRY_COLUMNS

VERSION = "0036"
DESCRIPTION = (
    "evt_outbox retry/terminal columns + evt_outbox_failure "
    "(bounded retry ladder, dead-letter, quarantine)"
)

TABLES = OUTBOX_FAILURE_TABLES

#: The `evt_outbox` index `due(now)` and the future M10.8 recovery driver both read through.
#: `evt_`-prefixed, because certification control NS-2 checks Billing's index names against every
#: table registered in the process.
INDEXES: tuple[tuple[str, str, str], ...] = (
    ("ix_evt_outbox_next_attempt_at", "evt_outbox", "next_attempt_at"),
    ("ix_evt_outbox_state_next_attempt", "evt_outbox", "state, next_attempt_at"),
)


def upgrade(database: EventDatabase) -> None:
    inspector = inspect(database.engine)
    existing_tables = set(inspector.get_table_names())

    # 1. Additive columns on evt_outbox (only where the table already exists — on a fresh
    #    database `create_all` has already built it complete, and both paths must agree).
    if "evt_outbox" in existing_tables:
        present = {c["name"] for c in inspector.get_columns("evt_outbox")}
        with database.engine.begin() as connection:
            for name, ddl_type in OUTBOX_RETRY_COLUMNS:
                if name not in present:
                    connection.execute(
                        text(f"ALTER TABLE evt_outbox ADD COLUMN {name} {ddl_type}")
                    )

    # 2. The append-only failure-evidence table.
    for table in TABLES:
        if table.__tablename__ not in existing_tables:
            table.__table__.create(database.engine, checkfirst=True)

    # 3. Indexes, create-if-absent (a re-run must not raise).
    existing_indexes = {
        ix["name"]
        for table_name in ("evt_outbox",)
        if table_name in set(inspect(database.engine).get_table_names())
        for ix in inspect(database.engine).get_indexes(table_name)
    }
    with database.engine.begin() as connection:
        for name, table_name, columns in INDEXES:
            if name not in existing_indexes:
                connection.execute(
                    text(f"CREATE INDEX IF NOT EXISTS {name} ON {table_name} ({columns})")
                )


def downgrade(database: EventDatabase) -> None:
    """No-op, for the 0034/0035 reason applied to failure evidence: dropping these would destroy
    the record of which entries are still un-drained and *why* delivery failed — the durable
    predicate ED9 requires before retention may delete anything, and the failure history ED8
    requires a dead-lettered entry to keep (ADR-0032 ED5/ED8/ED9)."""
    return None
