"""Migration 0038 — console agent lifecycle (M12.3).

Additively creates `dp_agent_state`. It alters nothing — in particular not the M11 `agent` table,
whose model is frozen (`E10-7`). Idempotent: created only if absent; a no-op on a database built by
`create_all` (the 0034 precedent).
"""

from __future__ import annotations

from sqlalchemy import inspect

from ..models import AgentStateRow

VERSION = "0038"
DESCRIPTION = "dp_agent_state (console archive/ownership for M11 agents)"

TABLES = (AgentStateRow,)


def upgrade(engine) -> None:
    existing = set(inspect(engine).get_table_names())
    for table in TABLES:
        if table.__tablename__ not in existing:
            table.__table__.create(engine, checkfirst=True)


def downgrade(engine) -> None:
    """No-op. Dropping the table would silently un-archive every archived agent."""
    return None
