"""EmbeddingRepository — chunk embeddings over a `VectorStore` + `Embedder` (M5.2B-1).

Owns the **write path** of the semantic index: embed chunk text and upsert/remove
vectors, keyed by the chunk's content hash so identical chunks **dedup** to one vector.
`search` is deliberately absent in the core milestone — semantic retrieval is M5.2B-2.
Depends only on the storage/embedder contracts.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from core.providers.stores import Embedder, VectorStore


class EmbeddingRepository:
    def __init__(self, vector_store: VectorStore, embedder: Embedder) -> None:
        self._store = vector_store
        self._embedder = embedder

    @property
    def dimensions(self) -> int:
        return self._embedder.dimensions

    async def index(
        self, vector_id: str, text: str, *, metadata: Mapping[str, object] | None = None
    ) -> Sequence[float]:
        """Embed `text` and upsert its vector under `vector_id` (the chunk content hash).
        Returns the vector."""
        vector = self._embedder.embed(text)
        await self._store.upsert(vector_id, vector, metadata=metadata)
        return vector

    async def remove(self, vector_id: str) -> None:
        await self._store.remove(vector_id)  # idempotent
