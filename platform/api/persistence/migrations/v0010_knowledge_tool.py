"""Migration 0010 — Knowledge Tool schema (M8.15).

Declarative schema only — **no application logic**. Additively creates the three knowledge tables
(`cp_knowledge_collection`, `cp_knowledge_document`, `cp_knowledge_chunk`). No `ALTER`s on
existing tables. The chunk `embedding` is a **portable JSON list** (no dedicated vector column
type) — the vector store adapter searches over these persisted vectors; no external index is
required in this build.

On a fresh database `Database.create_all()` builds these from the current model definitions — so
this migration is a no-op there. Idempotent: each table is created only if absent.
"""

from __future__ import annotations

from sqlalchemy import inspect

from ..database import Database
from ..models import (
    KnowledgeChunkRow,
    KnowledgeCollectionRow,
    KnowledgeDocumentRow,
)

VERSION = "0010"
DESCRIPTION = "cp_knowledge_collection + cp_knowledge_document + cp_knowledge_chunk tables"


def upgrade(database: Database) -> None:
    existing = set(inspect(database.engine).get_table_names())
    for table, model in (
        ("cp_knowledge_collection", KnowledgeCollectionRow),
        ("cp_knowledge_document", KnowledgeDocumentRow),
        ("cp_knowledge_chunk", KnowledgeChunkRow),
    ):
        if table not in existing:
            model.__table__.create(database.engine, checkfirst=True)


def downgrade(database: Database) -> None:
    """No-op: the additive knowledge tables are harmless and kept for audit/retrieval."""
    return None
