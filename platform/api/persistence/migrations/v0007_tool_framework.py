"""Migration 0007 — Tool Framework schema (M8.11).

Declarative schema only — **no application logic**. Additively creates the two new Tool
Framework tables (`cp_tool`, `cp_agent_tool`). No `ALTER`s on existing tables — this milestone
adds storage, it changes nothing existing.

On a fresh database `Database.create_all()` builds these from the current model definitions —
so this migration is a no-op there. It exists to document + apply the additive tables for an
**existing** database. Idempotent: each table is created only if absent.
"""

from __future__ import annotations

from sqlalchemy import inspect

from ..database import Database
from ..models import AgentToolRow, ToolRow

VERSION = "0007"
DESCRIPTION = "cp_tool + cp_agent_tool tables (Tool Framework)"


def upgrade(database: Database) -> None:
    existing_tables = set(inspect(database.engine).get_table_names())
    if "cp_tool" not in existing_tables:
        ToolRow.__table__.create(database.engine, checkfirst=True)
    if "cp_agent_tool" not in existing_tables:
        AgentToolRow.__table__.create(database.engine, checkfirst=True)


def downgrade(database: Database) -> None:
    """No-op: additive tables are harmless and kept for audit/isolation."""
    return None
