"""Chunk pipeline (M5.2B-1) — deterministic sliding-window chunking.

`SlidingWindowChunker` is the local default `ChunkStrategy` (word windows with overlap,
deterministic offsets). `ChunkPipeline` turns a strategy's `ChunkSpec`s into `Chunk`s,
assigning content-hash identities and prev/next relationships. Semantic/hierarchical
strategies are additive (new `ChunkStrategy` implementations); the pipeline is unchanged.
"""

from __future__ import annotations

from collections.abc import Sequence

from ..domain import Chunk, ChunkState
from ..ids import chunk_id_for, content_hash
from ..ports import ChunkSpec, ChunkStrategy


class SlidingWindowChunker(ChunkStrategy):
    name = "sliding_window"

    def __init__(self, *, size: int = 64, overlap: int = 16) -> None:
        if size <= 0 or not (0 <= overlap < size):
            raise ValueError("invalid sliding-window config")
        self._size = size
        self._overlap = overlap

    def chunk(self, text: str) -> Sequence[ChunkSpec]:
        words = text.split()
        if not words:
            return ()
        step = self._size - self._overlap
        specs: list[ChunkSpec] = []
        # precompute char offsets of each word (first occurrence scan is deterministic)
        offsets: list[int] = []
        cursor = 0
        for w in words:
            idx = text.find(w, cursor)
            idx = idx if idx >= 0 else cursor
            offsets.append(idx)
            cursor = idx + len(w)
        for i in range(0, len(words), step):
            window = words[i : i + self._size]
            if not window:
                break
            start = offsets[i]
            end = offsets[min(i + len(window), len(words)) - 1] + len(window[-1])
            specs.append(
                ChunkSpec(content=" ".join(window), start=start, end=end, token_count=len(window))
            )
            if i + self._size >= len(words):
                break
        return specs


class ChunkPipeline:
    def __init__(self, strategy: ChunkStrategy) -> None:
        self._strategy = strategy

    def chunk(self, text: str, *, document_id: str, version: int) -> list[Chunk]:
        specs = self._strategy.chunk(text)
        chunks: list[Chunk] = []
        for index, spec in enumerate(specs):
            chash = content_hash(spec.content)
            chunks.append(
                Chunk(
                    id=chunk_id_for(document_id, version, index, spec.content),
                    document_id=document_id,
                    version=version,
                    index=index,
                    content=spec.content,
                    content_hash=chash,
                    start=spec.start,
                    end=spec.end,
                    token_count=spec.token_count,
                    strategy=self._strategy.name,
                    state=ChunkState.CREATED,
                )
            )
        # link prev/next relationships
        linked: list[Chunk] = []
        from dataclasses import replace

        for i, c in enumerate(chunks):
            prev_id = chunks[i - 1].id if i > 0 else None
            next_id = chunks[i + 1].id if i < len(chunks) - 1 else None
            linked.append(replace(c, prev_id=prev_id, next_id=next_id))
        return linked
