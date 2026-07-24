"""ToolStreamingController (M6.1B-4) — deterministic chunk-lifecycle orchestration.

Mirrors `reasoning.streaming.StreamingController` exactly, plus cancellation:
checks the injected `ToolCancellationToken` **between every chunk** (mirroring
`PipelineCancellation`'s "checked between every stage" contract at chunk
granularity) and stops deterministically — no partial re-entry, no further
chunks pulled once cancelled. No networking, no threads, no wall-clock waiting
(`elapsed_s` is caller-supplied, exactly like `StreamingController.run`).
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator

from .cancellation import ToolCancellationToken
from .domain import ChunkKind, StreamingMetadata, StreamingSession, ToolChunk, Verdict
from .policies import ToolStreamingPolicyEngine


class ToolStreamingController:
    def __init__(self, policy_engine: ToolStreamingPolicyEngine | None = None) -> None:
        self._pe = policy_engine or ToolStreamingPolicyEngine()

    def run(
        self,
        chunks: Iterable[ToolChunk],
        *,
        cancellation: ToolCancellationToken | None = None,
        elapsed_s: float = 0.0,
    ) -> StreamingSession:
        buffered: list[ToolChunk] = []
        backpressure = 0
        final = False
        cancelled = False

        for c in chunks:
            if cancellation is not None and cancellation.is_cancelled():
                cancelled = True
                break
            buffered.append(c)
            verdict = self._pe.backpressure_ok(len(buffered))
            if not verdict.ok:
                backpressure += 1
            if c.kind is ChunkKind.FINAL or c.done:
                final = True

        timed_out = (not cancelled) and self._pe.timeout_exceeded(elapsed_s)
        partial_output = buffered[-1].delta if buffered else None
        meta = StreamingMetadata(
            streamed=True, chunk_count=len(buffered),
            first_chunk_index=buffered[0].index if buffered else 0,
        )
        return StreamingSession(
            chunks=tuple(buffered), partial_output=partial_output, final=final,
            cancelled=cancelled, backpressure_events=backpressure, timed_out=timed_out,
            metadata=meta,
        )

    def partials(self, chunks: Iterable[ToolChunk]) -> Iterator[object]:
        """Yield each chunk's delta in order — deterministic partial output view
        for a live consumer (gateway-compatible; never re-derived from state)."""
        for c in chunks:
            yield c.delta

    def backpressure_verdict(self, buffered: int) -> Verdict:
        return self._pe.backpressure_ok(buffered)
