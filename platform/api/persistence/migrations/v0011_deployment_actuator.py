"""Migration 0011 — Deployment Actuator schema (M8.16, ADR-0022).

Declarative schema only — **no application logic**. Additively creates `cp_deployment_execution`,
the durable System-of-Record for deployment execution state (one writer — the Actuator). No
`ALTER`s on existing tables. The immutable aggregate (job + transitions + approvals + rollbacks +
health + logs + result) persists in a portable JSON `data` column (the message-store precedent) —
no new column types, so it runs identically on SQLite and Postgres.

On a fresh database `Database.create_all()` builds this from the current model definitions — so
this migration is a no-op there. Idempotent: the table is created only if absent.
"""

from __future__ import annotations

from sqlalchemy import inspect

from ..database import Database
from ..models import DeploymentExecutionRow

VERSION = "0011"
DESCRIPTION = "cp_deployment_execution table (deployment execution System-of-Record)"


def upgrade(database: Database) -> None:
    existing = set(inspect(database.engine).get_table_names())
    if "cp_deployment_execution" not in existing:
        DeploymentExecutionRow.__table__.create(database.engine, checkfirst=True)


def downgrade(database: Database) -> None:
    """No-op: the additive deployment table is harmless and kept for audit/history."""
    return None
