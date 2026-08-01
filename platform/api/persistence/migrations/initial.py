"""Migration 0001 — initial Customer-Platform schema (M8.4).

Declarative schema only — **no application logic**. Creates the eight `cp_*` Foundation
tables. Idempotent: re-applying against a database that already has the tables is a no-op
(`create_all` checks first).
"""

from __future__ import annotations

from sqlmodel import SQLModel

from ..database import Database
from ..models import CP_TABLES

VERSION = "0001"
DESCRIPTION = "initial customer-platform foundation schema (cp_* tables)"


def upgrade(database: Database) -> None:
    """Create the `cp_*` tables on `database`'s engine."""
    SQLModel.metadata.create_all(
        database.engine, tables=[t.__table__ for t in CP_TABLES]
    )


def downgrade(database: Database) -> None:
    """Drop the `cp_*` tables (test/teardown convenience; declarative)."""
    SQLModel.metadata.drop_all(
        database.engine, tables=[t.__table__ for t in CP_TABLES]
    )
