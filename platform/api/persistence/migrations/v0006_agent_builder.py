"""Migration 0006 — Agent Builder schema (M8.10).

Declarative schema only — **no application logic**. Additively creates the two new Agent
Builder tables (`cp_agent`, `cp_agent_version`). No `ALTER`s on existing tables — this
milestone adds storage, it changes nothing existing.

On a fresh database `Database.create_all()` builds these from the current model definitions —
so this migration is a no-op there. It exists to document + apply the additive tables for an
**existing** database. Idempotent: each table is created only if absent. `cp_agent_version`
is append-only by contract (the store only ever inserts a row or flips a version's status to
superseded — it never deletes).
"""

from __future__ import annotations

from sqlalchemy import inspect

from ..database import Database
from ..models import AgentRow, AgentVersionRow

VERSION = "0006"
DESCRIPTION = "cp_agent + cp_agent_version tables (Agent Builder)"


def upgrade(database: Database) -> None:
    existing_tables = set(inspect(database.engine).get_table_names())
    if "cp_agent" not in existing_tables:
        AgentRow.__table__.create(database.engine, checkfirst=True)
    if "cp_agent_version" not in existing_tables:
        AgentVersionRow.__table__.create(database.engine, checkfirst=True)


def downgrade(database: Database) -> None:
    """No-op: agent versions are append-only and never dropped."""
    return None
