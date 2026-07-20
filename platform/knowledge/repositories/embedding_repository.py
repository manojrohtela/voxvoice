"""EmbeddingRepository — chunk embeddings over a `VectorStore` + `Embedder` (M5.2B-1
write path; M5.2B-2 adds the read path).

Owns the semantic index: embed chunk text and upsert/remove vectors, keyed by the chunk's
content hash so identical chunks **dedup** to one vector. `search` (M5.2B-2) embeds a
query and returns the nearest content-hash vectors — the Retrieval Engine maps those back
to chunks. Depends only on the storage/embedder contracts (the anti-corruption boundary).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from core.providers.stores import Embedder, VectorMatch, VectorStore


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

    async def search(self, text: str, *, k: int = 100) -> Sequence[VectorMatch]:
        """Embed `text` and return the nearest vectors (each id is a chunk content hash,
        score is cosine similarity). Deterministic for a given store + embedder."""
        query = self._embedder.embed(text)
        return await self._store.search(query, k=k)

    async def remove(self, vector_id: str) -> None:
        await self._store.remove(vector_id)  # idempotent
