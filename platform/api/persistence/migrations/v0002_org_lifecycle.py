"""Migration 0002 — Organization Lifecycle columns (M8.6).

Declarative schema only — **no application logic**. Additively introduces the M8.6 columns
on the existing `cp_organization` (`status`, `timezone`, `language`, `logo_url`,
`updated_at`) and `cp_membership` (`invited_role`, `expires_at`) tables.

On a fresh database `Database.create_all()` (migration `0001`) already builds these columns
from the current model definitions — so this migration is a no-op there. It exists to
document + apply the additive `ALTER TABLE` for an **existing** `0001` database. Idempotent:
each `ALTER` is guarded against the column already existing.
"""

from __future__ import annotations

from sqlalchemy import inspect, text

from ..database import Database

VERSION = "0002"
DESCRIPTION = "additive org-lifecycle columns on cp_organization + cp_membership"

_ADDITIONS = {
    "cp_organization": {
        "status": "VARCHAR DEFAULT 'active'",
        "timezone": "VARCHAR DEFAULT 'UTC'",
        "language": "VARCHAR DEFAULT 'en'",
        "logo_url": "VARCHAR",
        "updated_at": "FLOAT DEFAULT 0.0",
    },
    "cp_membership": {
        "invited_role": "VARCHAR DEFAULT 'member'",
        "expires_at": "FLOAT DEFAULT 0.0",
    },
}


def upgrade(database: Database) -> None:
    inspector = inspect(database.engine)
    existing_tables = set(inspector.get_table_names())
    with database.engine.begin() as conn:
        for table, columns in _ADDITIONS.items():
            if table not in existing_tables:
                continue  # 0001 not yet applied; create_all will build current columns
            present = {c["name"] for c in inspect(database.engine).get_columns(table)}
            for name, ddl in columns.items():
                if name not in present:
                    conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {name} {ddl}"))


def downgrade(database: Database) -> None:
    """No-op: SQLite cannot drop columns portably; the additive columns are harmless."""
    return None
