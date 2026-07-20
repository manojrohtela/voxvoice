"""Streaming Controller (M5.5B-2) — deterministic streaming orchestration.

Orchestrates the **chunk lifecycle** of a response stream: opens a streaming session, tracks
partial responses, applies backpressure (a buffered-chunk cap → verdict, never a blocking
sleep), evaluates deterministic timeout hooks (clock-based, no wall-clock waiting), and marks
the final chunk. It owns **orchestration only** — it never opens a socket or talks to a
provider SDK (the chunks arrive from the injected `ModelProvider` via the engine). Its output
is replay- and gateway-compatible: a `StreamingMetadata` + `StreamingSession` record + trace
events, all deterministic for a given chunk sequence.
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field

from .domain import ChunkKind, ResponseChunk, StreamingMetadata
from .policies import ReasoningPolicyEngine, Verdict


@dataclass(frozen=True, slots=True)
class StreamingSession:
    """The deterministic record of one streaming pass."""

    chunks: tuple[ResponseChunk, ...] = ()
    partial_text: str = ""
    final: bool = False
    backpressure_events: int = 0
    timed_out: bool = False
    metadata: StreamingMetadata = field(default_factory=StreamingMetadata)


class StreamingController:
    """Deterministic chunk-lifecycle orchestrator. No networking, no threads."""

    def __init__(self, policy_engine: ReasoningPolicyEngine | None = None) -> None:
        self._pe = policy_engine or ReasoningPolicyEngine()

    # ── streaming session over a materialized chunk stream ────────────────────
    def run(
        self, chunks: Iterable[ResponseChunk], *, elapsed_s: float = 0.0
    ) -> StreamingSession:
        """Drive the chunk lifecycle deterministically. `elapsed_s` lets the caller feed a
        deterministic clock delta for the timeout hook (no wall-clock waiting here)."""
        timeout = self._pe.policies.streaming.timeout.seconds
        buffered: list[ResponseChunk] = []
        backpressure = 0
        final = False
        for c in chunks:
            buffered.append(c)
            verdict = self._pe.backpressure_ok(len(buffered))
            if not verdict.ok:
                backpressure += 1
            if c.kind is ChunkKind.FINAL or c.done:
                final = True
        timed_out = timeout > 0 and elapsed_s > timeout
        partial = "".join(c.delta for c in buffered)
        meta = StreamingMetadata(
            streamed=True,
            chunk_count=len(buffered),
            first_chunk_index=buffered[0].index if buffered else 0,
        )
        return StreamingSession(
            chunks=tuple(buffered), partial_text=partial, final=final,
            backpressure_events=backpressure, timed_out=timed_out, metadata=meta,
        )

    # ── incremental partial view (for a live consumer / gateway) ──────────────
    def partials(self, chunks: Iterable[ResponseChunk]) -> Iterator[str]:
        """Yield the cumulative text after each chunk — deterministic partial responses."""
        acc = ""
        for c in chunks:
            acc += c.delta
            yield acc

    def backpressure_verdict(self, buffered: int) -> Verdict:
        return self._pe.backpressure_ok(buffered)
