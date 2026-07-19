"""VectorRepository — embeddings over a `VectorStore` + `Embedder` (M5.1B-1/-2).

Owns the semantic index: embed text and upsert/remove vectors (write path, M5.1B-1),
plus `search` (read path, added in M5.1B-2 for the Retrieval Engine). Depends only on
the storage/embedder contracts, never a concrete provider. Scope isolation is enforced
by the Retrieval Engine intersecting these matches with the scoped candidate set — the
vector store itself is scope-agnostic.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from core.providers.stores import Embedder, VectorMatch, VectorStore


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

    async def search(self, text: str, *, k: int = 50) -> Sequence[VectorMatch]:
        """Embed `text` and return the k nearest stored vectors (id + score). The
        caller enforces scope isolation by intersecting ids with its candidate set."""
        vector = self._embedder.embed(text)
        return await self._store.search(vector, k=k)

    async def remove(self, memory_id: str) -> None:
        await self._store.remove(memory_id)  # idempotent
