"""Migration 0012 — Observability alert rules (M8.17).

Declarative schema only — **no application logic**. Additively creates `cp_alert_rule`, the **only**
durable Observability table (alert-rule configuration). Metrics, logs, traces, health, usage and
cost are *projections* of the append-only Audit stream (M8.9) and hold no rows of their own — so
Observability adds exactly one table. No `ALTER`s on existing tables.

On a fresh database `Database.create_all()` builds this from the model definitions — so this
migration is a no-op there. Idempotent: the table is created only if absent.
"""

from __future__ import annotations

from sqlalchemy import inspect

from ..database import Database
from ..models import AlertRuleRow

VERSION = "0012"
DESCRIPTION = "cp_alert_rule table (Observability alert-rule configuration)"


def upgrade(database: Database) -> None:
    existing = set(inspect(database.engine).get_table_names())
    if "cp_alert_rule" not in existing:
        AlertRuleRow.__table__.create(database.engine, checkfirst=True)


def downgrade(database: Database) -> None:
    """No-op: the additive alert-rule table is harmless and kept."""
    return None
