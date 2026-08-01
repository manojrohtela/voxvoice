"""Migration 0005 — durable Audit & Activity Platform (M8.9).

Declarative schema only — **no application logic**. Creates the new append-only
`cp_audit_event` table (the durable audit stream). No `ALTER`s — this milestone adds storage,
it changes nothing existing.

On a fresh database `Database.create_all()` builds this table from the current model
definitions — so this migration is a no-op there. It exists to document + apply the additive
table for an **existing** database. Idempotent: the table is created only if absent. The table
is append-only by contract (the store never issues UPDATE/DELETE); no schema mechanism is
required to enforce that here.
"""

from __future__ import annotations

from sqlalchemy import inspect

from ..database import Database
from ..models import AuditEventRow

VERSION = "0005"
DESCRIPTION = "append-only cp_audit_event table (durable audit stream)"


def upgrade(database: Database) -> None:
    existing_tables = set(inspect(database.engine).get_table_names())
    if "cp_audit_event" not in existing_tables:
        AuditEventRow.__table__.create(database.engine, checkfirst=True)


def downgrade(database: Database) -> None:
    """No-op: audit is append-only and never dropped (Constitution §A5/§A11.2)."""
    return None
