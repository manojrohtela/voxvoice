"""Embedding pipeline (M5.2B-1) — embed chunks with content-hash dedup.

Embeds each chunk via the `EmbeddingRepository` (vectors keyed by the chunk **content
hash**, so identical chunks across versions share one vector) and advances the chunk
lifecycle to EMBEDDED. Deterministic. No ranking, no search (retrieval is M5.2B-2).
"""

from __future__ import annotations

from collections.abc import Callable, Sequence

from ..domain import Chunk
from ..lifecycle import ChunkTrigger, advance_chunk
from ..repositories.embedding_repository import EmbeddingRepository


class EmbeddingPipeline:
    def __init__(
        self,
        embedding_repo: EmbeddingRepository,
        *,
        vectorize: bool = True,
        clock: Callable[[], float] | None = None,
    ) -> None:
        self._embeddings = embedding_repo
        self._vectorize = vectorize
        self._clock = clock

    async def embed(self, chunks: Sequence[Chunk]) -> list[Chunk]:
        out: list[Chunk] = []
        seen: set[str] = set()  # content hashes embedded in this batch (dedup)
        for chunk in chunks:
            if self._vectorize:
                if chunk.content_hash not in seen:
                    await self._embeddings.index(
                        chunk.content_hash,
                        chunk.content,
                        metadata={"document_id": chunk.document_id, "version": chunk.version},
                    )
                    seen.add(chunk.content_hash)
                chunk = chunk.with_embedded(True)
            out.append(advance_chunk(chunk, ChunkTrigger.EMBED, clock=self._clock))
        return out
