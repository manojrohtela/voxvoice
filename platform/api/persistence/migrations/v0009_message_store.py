"""Migration 0009 — conversation message store (M8.14).

Declarative schema only — **no application logic**. Additively creates the new
`cp_conversation_message` table (the durable message store backing the memory adapter). No
`ALTER`s on existing tables. **No vector column, no embeddings** (no RAG in M8.14).

On a fresh database `Database.create_all()` builds this from the current model definitions — so
this migration is a no-op there. It exists to document + apply the additive table for an
**existing** database. Idempotent: the table is created only if absent.
"""

from __future__ import annotations

from sqlalchemy import inspect

from ..database import Database
from ..models import ConversationMessageRow

VERSION = "0009"
DESCRIPTION = "cp_conversation_message table (durable message store)"


def upgrade(database: Database) -> None:
    existing_tables = set(inspect(database.engine).get_table_names())
    if "cp_conversation_message" not in existing_tables:
        ConversationMessageRow.__table__.create(database.engine, checkfirst=True)


def downgrade(database: Database) -> None:
    """No-op: the additive message store is harmless and kept for audit/retrieval."""
    return None
