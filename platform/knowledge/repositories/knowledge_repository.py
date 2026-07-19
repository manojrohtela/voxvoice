"""KnowledgeRepository (M5.2B-1) — the coordinating facade.

The domain-level API the runtime talks to: it composes the Document/Chunk/Embedding
repositories, the version manager, and the index so the runtime never wires them
individually. It performs no storage itself — pure delegation/coordination.
"""

from __future__ import annotations

from collections.abc import Sequence

from ..domain import Chunk, Document, DocumentVersion
from ..index import KnowledgeIndex
from ..version import VersionManager
from .chunk_repository import ChunkRepository
from .document_repository import DocumentRepository
from .embedding_repository import EmbeddingRepository


class KnowledgeRepository:
    def __init__(
        self,
        *,
        documents: DocumentRepository,
        chunks: ChunkRepository,
        embeddings: EmbeddingRepository,
        versions: VersionManager,
        index: KnowledgeIndex,
    ) -> None:
        self.documents = documents
        self.chunks = chunks
        self.embeddings = embeddings
        self.versions = versions
        self.index = index

    # ── document reads ───────────────────────────────────────────────────────
    async def get_document(self, document_id: str) -> Document | None:
        return await self.documents.get(document_id)

    async def list_documents(self, *, org_id: str | None = None) -> Sequence[Document]:
        # scope is a nested dict in the stored doc, so filter for org match post-fetch
        docs = await self.documents.query({})
        if org_id is None:
            return docs
        return [d for d in docs if d.scope.org_id == org_id]

    async def get_chunks(self, document_id: str, *, version: int | None = None) -> Sequence[Chunk]:
        return await self.chunks.for_document(document_id, version=version)

    async def versions_of(self, document_id: str) -> Sequence[DocumentVersion]:
        return await self.versions.history(document_id)

    async def document_count(self) -> int:
        return len(await self.documents.query({}))
