"""ChunkRepository — chunk records over a `DocumentStore` (M5.2B-1; M5.2B-2 adds the
hot-chunk cache + retrieval reads).

The only code binding the chunk collection. Translates `Chunk` ↔ storage document.
Attribute queries only (by document_id/version/state/content_hash); no ranking or
similarity (the Retrieval Engine, M5.2B-2, owns that). An optional repository-owned
`KnowledgeCache` fronts point reads (read-through) and writes (write-through) — the
runtime never sees it, so a Redis backend swaps in below the repository boundary.
"""

from __future__ import annotations

from collections.abc import Sequence

from core.providers.stores import DocumentStore

from ..cache import KnowledgeCache
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
        cache: KnowledgeCache | None = None,
    ) -> None:
        self._store = document_store
        self._serializer = serializer
        self._c = collections or CollectionsConfig()
        self._cache = cache  # repository-owned; None → disabled

    @property
    def cache(self) -> KnowledgeCache | None:
        return self._cache

    async def add(self, chunk: Chunk) -> None:
        doc = self._serializer.chunk_to_document(chunk)
        await self._store.upsert(self._c.chunks, chunk.id, doc)
        if self._cache is not None:
            self._cache.put(chunk.id, doc)  # write-through

    async def add_many(self, chunks: Sequence[Chunk]) -> None:
        for c in chunks:
            await self.add(c)

    async def get(self, chunk_id: str) -> Chunk | None:
        if self._cache is not None:
            cached = self._cache.get(chunk_id)  # read-through
            if cached is not None:
                return self._serializer.chunk_from_document(cached)
        d = await self._store.get(self._c.chunks, chunk_id)
        if d is None:
            return None
        if self._cache is not None:
            self._cache.put(chunk_id, d)
        return self._serializer.chunk_from_document(d)

    async def get_many(self, chunk_ids: Sequence[str]) -> list[Chunk]:
        out: list[Chunk] = []
        for cid in chunk_ids:
            c = await self.get(cid)
            if c is not None:
                out.append(c)
        return out

    async def by_content_hashes(self, hashes: Sequence[str]) -> list[Chunk]:
        """Chunks whose content hash is in `hashes` (maps semantic matches → chunks)."""
        out: list[Chunk] = []
        for h in set(hashes):
            rows = await self._store.query(self._c.chunks, {"content_hash": h})
            out.extend(self._serializer.chunk_from_document(r) for r in rows)
        return out

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
        if self._cache is not None:
            self._cache.invalidate(chunk_id)

    async def remove_for_document(self, document_id: str) -> None:
        for c in await self.for_document(document_id):
            await self._store.delete(self._c.chunks, c.id)
            if self._cache is not None:
                self._cache.invalidate(c.id)
