"""Migration 0008 — Conversation Runtime schema (M8.12).

Declarative schema only — **no application logic**. Additively creates the new
`cp_conversation_session` table (session + explicit conversation state as JSON). No `ALTER`s on
existing tables — this milestone adds storage, it changes nothing existing. **No message-history
table and no vector storage** (out of scope for M8.12).

On a fresh database `Database.create_all()` builds this from the current model definitions — so
this migration is a no-op there. It exists to document + apply the additive table for an
**existing** database. Idempotent: the table is created only if absent.
"""

from __future__ import annotations

from sqlalchemy import inspect

from ..database import Database
from ..models import ConversationSessionRow

VERSION = "0008"
DESCRIPTION = "cp_conversation_session table (Conversation Runtime)"


def upgrade(database: Database) -> None:
    existing_tables = set(inspect(database.engine).get_table_names())
    if "cp_conversation_session" not in existing_tables:
        ConversationSessionRow.__table__.create(database.engine, checkfirst=True)


def downgrade(database: Database) -> None:
    """No-op: the additive session table is harmless and kept for audit/isolation."""
    return None
