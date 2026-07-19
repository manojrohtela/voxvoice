"""ChunkRepository — chunk records over a `DocumentStore` (M5.2B-1).

The only code binding the chunk collection. Translates `Chunk` ↔ storage document.
Attribute queries only (by document_id/version/state); no ranking or similarity (the
Retrieval Engine, M5.2B-2, owns that).
"""

from __future__ import annotations

from collections.abc import Sequence

from core.providers.stores import DocumentStore

from ..config import CollectionsConfig
from ..domain import Chunk
from ..serializer import KnowledgeSerializer


class ChunkRepository:
    def __init__(
        self,
        document_store: DocumentStore,
        serializer: KnowledgeSerializer,
        *,
        collections: CollectionsConfig | None = None,
    ) -> None:
        self._store = document_store
        self._serializer = serializer
        self._c = collections or CollectionsConfig()

    async def add(self, chunk: Chunk) -> None:
        doc = self._serializer.chunk_to_document(chunk)
        await self._store.upsert(self._c.chunks, chunk.id, doc)

    async def add_many(self, chunks: Sequence[Chunk]) -> None:
        for c in chunks:
            await self.add(c)

    async def get(self, chunk_id: str) -> Chunk | None:
        d = await self._store.get(self._c.chunks, chunk_id)
        return self._serializer.chunk_from_document(d) if d is not None else None

    async def update(self, chunk: Chunk) -> None:
        await self.add(chunk)

    async def for_document(
        self, document_id: str, *, version: int | None = None
    ) -> Sequence[Chunk]:
        filter: dict[str, object] = {"document_id": document_id}
        if version is not None:
            filter["version"] = version
        rows = await self._store.query(self._c.chunks, filter)
        chunks = [self._serializer.chunk_from_document(r) for r in rows]
        chunks.sort(key=lambda c: (c.version, c.index))
        return chunks

    async def remove(self, chunk_id: str) -> None:
        await self._store.delete(self._c.chunks, chunk_id)

    async def remove_for_document(self, document_id: str) -> None:
        for c in await self.for_document(document_id):
            await self._store.delete(self._c.chunks, c.id)
