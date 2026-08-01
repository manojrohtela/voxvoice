"""Migration 0004 — Project Lifecycle schema (M8.8).

Declarative schema only — **no application logic**. Additively introduces the M8.8 columns
on the existing `cp_project` table (`description`, `icon`, `status`, `updated_at`, `metadata`)
and creates the new `cp_project_membership` table.

On a fresh database `Database.create_all()` builds these from the current model definitions —
so this migration is a no-op there. It exists to document + apply the additive changes for an
**existing** database. Idempotent: each `ALTER` is guarded against the column already existing,
and the new table is created only if absent.
"""

from __future__ import annotations

from sqlalchemy import inspect, text

from ..database import Database
from ..models import ProjectMembershipRow

VERSION = "0004"
DESCRIPTION = "additive project-lifecycle columns + cp_project_membership table"

_ADDITIONS = {
    "cp_project": {
        "description": "VARCHAR DEFAULT ''",
        "icon": "VARCHAR DEFAULT ''",
        "status": "VARCHAR DEFAULT 'active'",
        "updated_at": "FLOAT DEFAULT 0.0",
        "metadata": "JSON",
    },
}


def upgrade(database: Database) -> None:
    inspector = inspect(database.engine)
    existing_tables = set(inspector.get_table_names())
    # 1. additive columns on cp_project (guarded).
    with database.engine.begin() as conn:
        for table, columns in _ADDITIONS.items():
            if table not in existing_tables:
                continue  # 0001 not yet applied; create_all will build current columns
            present = {c["name"] for c in inspect(database.engine).get_columns(table)}
            for name, ddl in columns.items():
                if name not in present:
                    conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {name} {ddl}"))
    # 2. new cp_project_membership table (created only if absent).
    if "cp_project_membership" not in existing_tables:
        ProjectMembershipRow.__table__.create(database.engine, checkfirst=True)


def downgrade(database: Database) -> None:
    """No-op: SQLite cannot drop columns portably; the additive schema is harmless."""
    return None
