"""EchoStreamingAdapter (M6.1B-4) — the deterministic offline default streaming
adapter, mirroring `stores.echo_adapter.EchoToolAdapter`'s role for synchronous
execution. Implements the frozen `tool.extensions.StreamingAdapter` seam
directly (reserved at M6.1B-1, unbuilt until now) — no external SDK, no
networking, no randomness, no wall-clock reads.
"""

from __future__ import annotations

from collections.abc import Iterator

from ..domain import ToolRequest
from ..extensions import StreamingAdapter
from .domain import ChunkKind, ToolChunk


class EchoStreamingAdapter(StreamingAdapter):
    """Streams the request's arguments back one key at a time (sorted, for
    determinism), then a `FINAL` chunk carrying the fully-assembled echo."""

    name = "echo_stream"
    capabilities = frozenset({"echo_stream"})

    def stream(self, request: ToolRequest) -> Iterator[ToolChunk]:
        items = sorted(request.arguments.items())
        acc: dict[str, object] = {}
        index = 0
        for key, value in items:
            acc[key] = value
            yield ToolChunk(
                id=f"{request.request_id}:{index}", index=index, delta=dict(acc),
                kind=ChunkKind.PARTIAL,
            )
            index += 1
        yield ToolChunk(
            id=f"{request.request_id}:{index}", index=index,
            delta={"echo": dict(acc)}, kind=ChunkKind.FINAL, done=True,
        )
