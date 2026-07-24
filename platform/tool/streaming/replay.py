"""Streaming replay compatibility (M6.1B-4).

Reuses `tool.observability`'s `ReplayRecord`/`ReplayComparison`/`ExecutionTrace`
shapes and builders **unmodified** — a streamed execution's `ToolResult` is
exactly the same shape a synchronous execution produces (`StreamedToolResult`
composes it, never replaces it), so `observability.trace.execution_trace_for`
and `observability.replay.replay_record_for` already work for it verbatim.
Only the *validation* side needs a streaming-aware engine, since replaying a
stream means re-streaming through `EchoStreamingAdapter`, never `EchoToolAdapter`.
"""

from __future__ import annotations

from ..ids import content_hash, result_hash_for
from ..observability.domain import ReplayComparison, ReplayRecord
from ..observability.trace import execution_trace_for
from ..serializer import ToolSerializer
from .adapters import EchoStreamingAdapter
from .controller import ToolStreamingController
from .domain import StreamedToolResult

__all__ = ["execution_trace_for", "StreamingReplayEngine"]


class StreamingReplayEngine:
    """The streaming analog of `tool.observability.replay_engine.ReplayEngine`:
    deterministic replay validation solely against `EchoStreamingAdapter` —
    never a live registry lookup or vendor SDK."""

    def __init__(
        self, *, serializer: ToolSerializer | None = None,
        controller: ToolStreamingController | None = None,
    ) -> None:
        self._serializer = serializer or ToolSerializer()
        self._controller = controller or ToolStreamingController()
        self._adapter = EchoStreamingAdapter()

    def validate(self, record: ReplayRecord) -> ReplayComparison:
        request = self._serializer.request_from_dict(record.request_document)
        chunks = self._adapter.stream(request)
        session = self._controller.run(chunks)
        output_hash = result_hash_for(request.request_hash, session.partial_output)
        replay_hash = content_hash(request.request_hash, output_hash, "completed")

        mismatches: list[str] = []
        if output_hash != record.execution_trace.output_hash:
            mismatches.append("output_hash")
        if replay_hash != record.replay_hash:
            mismatches.append("replay_hash")

        return ReplayComparison(
            matches=not mismatches, expected_hash=record.replay_hash, actual_hash=replay_hash,
            mismatches=tuple(mismatches),
            reason="" if not mismatches else f"mismatch in {', '.join(mismatches)}",
        )


def streamed_replay_record_for(
    request, streamed: StreamedToolResult, *, now: float = 0.0,
    serializer: ToolSerializer | None = None,
) -> ReplayRecord:
    """Builds an `ExecutionTrace` + `ReplayRecord` for a streamed execution,
    reusing `observability`'s own builders verbatim over `streamed.result`."""
    from ..observability.replay import replay_record_for

    trace = execution_trace_for(
        request, streamed.result, metadata={"streamed": "true",
                                            "chunk_count": str(len(streamed.session.chunks))},
    )
    return replay_record_for(request, streamed.result, trace, now=now, serializer=serializer)
