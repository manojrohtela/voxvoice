"""Unified streaming (M5.6B-1) — provider-neutral streaming models. No networking.

Every adapter's native stream is already translated (inside the adapter) into the internal
`ProviderChunk` sequence (§10 of the M5.6A design); `UnifiedStreamingAssembler` here just
pulls that sequence, tracks the cumulative partial text, and marks completion — the
deterministic core mechanics every provider's stream is normalized through. Backpressure,
timeout hooks, and cancellation are M5.6B-2 intelligence (`StreamingController` extends this
additively); the core produces exactly: chunk sequence, streaming metadata, completion.
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator

from .domain import ChunkKind, ProviderChunk, StreamingMetadata


class UnifiedStreamingAssembler:
    """Deterministic chunk-lifecycle assembly. No backpressure/timeout policy (M5.6B-2)."""

    def pull(self, chunks: Iterable[ProviderChunk]) -> tuple[ProviderChunk, ...]:
        """Materialize the adapter's chunk stream (already provider-neutral)."""
        return tuple(chunks)

    def partials(self, chunks: Iterable[ProviderChunk]) -> Iterator[str]:
        """Yield the cumulative text after each chunk — deterministic partial responses."""
        acc = ""
        for c in chunks:
            acc += c.delta
            yield acc

    def metadata_for(
        self, chunks: tuple[ProviderChunk, ...], *, streamed: bool = True
    ) -> StreamingMetadata:
        return StreamingMetadata(
            streamed=streamed, chunk_count=len(chunks),
            first_chunk_index=chunks[0].index if chunks else 0,
        )

    def is_complete(self, chunks: tuple[ProviderChunk, ...]) -> bool:
        return bool(chunks) and (chunks[-1].kind is ChunkKind.FINAL or chunks[-1].done)
