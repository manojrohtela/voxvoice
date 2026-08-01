"""Knowledge Tool domain model (M8.15) — additive.

The knowledge **assets** — Collection → Document → Chunk — plus the retrieval result shapes
(`RetrievedChunk` / `ContextPackage`). Knowledge is a **Tool Framework capability**: these are
authoring/management records the control plane owns; retrieval is executed by the Knowledge Tool
(via the existing Tool Executor), and the Runtime only ever receives a Tool Result. Pure data,
frozen + slotted. **No embeddings/vector logic here** (those are adapters); a chunk merely carries
its (deterministic) embedding vector + status once generated.

A "Knowledge Base" is modelled as the project-scoped `base` namespace a Collection belongs to
(kept lightweight — one aggregate per level would add three repos for no behavioural gain).
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from enum import StrEnum
from typing import Any


class KnowledgeStatus(StrEnum):
    """Collection lifecycle. `DELETED`/`ARCHIVED` retain the row (audit/isolation)."""

    ACTIVE = "active"
    ARCHIVED = "archived"
    DELETED = "deleted"


class DocumentStatus(StrEnum):
    ACTIVE = "active"
    ARCHIVED = "archived"
    DELETED = "deleted"


class EmbeddingStatus(StrEnum):
    PENDING = "pending"
    GENERATED = "generated"
    FAILED = "failed"


class IndexStatus(StrEnum):
    UNINDEXED = "unindexed"
    INDEXED = "indexed"


@dataclass(frozen=True, slots=True)
class KnowledgeCollection:
    """A retrievable collection within a project (the Knowledge Base namespace is `base`).
    Carries its embedding provider/model + chunking config so retrieval embeds consistently."""

    id: str
    org_id: str
    workspace_id: str
    project_id: str
    name: str
    base: str = "default"
    description: str = ""
    status: KnowledgeStatus = KnowledgeStatus.ACTIVE
    version: int = 1
    embedding_provider: str = "local"
    embedding_model: str = "local-embed"
    chunk_size: int = 800
    chunk_overlap: int = 100
    created_by: str = ""
    created_at: float = 0.0
    updated_at: float = 0.0
    metadata: dict[str, Any] = field(default_factory=dict)

    def with_updates(self, **changes: Any) -> KnowledgeCollection:
        allowed = {
            "name", "description", "status", "version", "embedding_provider",
            "embedding_model", "chunk_size", "chunk_overlap", "updated_at", "metadata",
        }
        return replace(self, **{k: v for k, v in changes.items() if k in allowed and v is not None})


@dataclass(frozen=True, slots=True)
class KnowledgeDocument:
    """One uploaded document (text only — no OCR/image parsing). Versioned; checksummed for
    duplicate detection; MIME + size validated on upload."""

    id: str
    org_id: str
    workspace_id: str
    project_id: str
    collection_id: str
    name: str
    mime_type: str = "text/plain"
    size_bytes: int = 0
    checksum: str = ""
    content: str = ""
    version: int = 1
    status: DocumentStatus = DocumentStatus.ACTIVE
    created_by: str = ""
    created_at: float = 0.0
    updated_at: float = 0.0
    metadata: dict[str, Any] = field(default_factory=dict)

    def with_updates(self, **changes: Any) -> KnowledgeDocument:
        allowed = {
            "name", "mime_type", "size_bytes", "checksum", "content", "version",
            "status", "updated_at", "metadata",
        }
        return replace(self, **{k: v for k, v in changes.items() if k in allowed and v is not None})


@dataclass(frozen=True, slots=True)
class KnowledgeChunk:
    """One deterministic chunk of a document. Carries its checksum + (once generated) its
    embedding vector + embedding/index status. Ordered by `index`."""

    id: str
    org_id: str
    collection_id: str
    document_id: str
    index: int
    text: str
    checksum: str = ""
    embedding: tuple[float, ...] = ()
    embedding_status: EmbeddingStatus = EmbeddingStatus.PENDING
    index_status: IndexStatus = IndexStatus.UNINDEXED
    created_at: float = 0.0
    metadata: dict[str, Any] = field(default_factory=dict)

    def with_embedding(self, embedding: tuple[float, ...]) -> KnowledgeChunk:
        return replace(
            self, embedding=embedding, embedding_status=EmbeddingStatus.GENERATED
        )

    def indexed(self) -> KnowledgeChunk:
        return replace(self, index_status=IndexStatus.INDEXED)

    def as_view(self) -> dict[str, Any]:
        return {
            "id": self.id, "document_id": self.document_id, "index": self.index,
            "text": self.text, "checksum": self.checksum,
            "embedding_status": self.embedding_status.value,
            "index_status": self.index_status.value,
            "dimensions": len(self.embedding), "metadata": self.metadata,
        }


@dataclass(frozen=True, slots=True)
class RetrievedChunk:
    """One chunk selected by retrieval, with its similarity score."""

    chunk_id: str
    document_id: str
    index: int
    text: str
    score: float
    metadata: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "chunk_id": self.chunk_id, "document_id": self.document_id, "index": self.index,
            "text": self.text, "score": round(self.score, 6), "metadata": self.metadata,
        }


@dataclass(frozen=True, slots=True)
class ContextPackage:
    """The Knowledge Tool's result — the ranked/filtered chunk selection returned to the Runtime
    as a Tool Result (the Runtime sees only this, never the retrieval internals)."""

    query: str
    collection_id: str
    chunks: tuple[RetrievedChunk, ...] = ()
    total_candidates: int = 0
    threshold: float = 0.0
    metadata: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "query": self.query, "collection_id": self.collection_id,
            "chunks": [c.as_dict() for c in self.chunks],
            "total_candidates": self.total_candidates, "threshold": self.threshold,
            "context": "\n\n".join(c.text for c in self.chunks),
            "metadata": self.metadata,
        }
