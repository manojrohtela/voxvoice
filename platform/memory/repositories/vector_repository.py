"""VectorRepository — embeddings over a `VectorStore` + `Embedder` (M5.1B-1).

Owns the **write path** of the semantic index: embed text and upsert/remove vectors.
Search is deliberately **not** exposed here — semantic retrieval + ranking is the
Retrieval Engine's job (M5.1B-2, non-goal now). Depends only on the storage/embedder
contracts, never a concrete provider.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from core.providers.stores import Embedder, VectorStore


class VectorRepository:
    def __init__(self, store: VectorStore, embedder: Embedder) -> None:
        self._store = store
        self._embedder = embedder

    @property
    def dimensions(self) -> int:
        return self._embedder.dimensions

    async def index(
        self, memory_id: str, text: str, *, metadata: Mapping[str, object] | None = None
    ) -> Sequence[float]:
        """Embed `text` and upsert its vector under `memory_id`. Returns the vector."""
        vector = self._embedder.embed(text)
        await self._store.upsert(memory_id, vector, metadata=metadata)
        return vector

    async def remove(self, memory_id: str) -> None:
        await self._store.remove(memory_id)  # idempotent
