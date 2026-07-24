"""Streaming serialization (M6.1B-4) — deterministic, round-trippable, no I/O.
Versioned (`schema_version`); reuses `tool.serializer.ToolSerializer` for the
embedded `ToolResult` rather than re-deriving that mapping.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from ..serializer import ToolSerializer
from .domain import (
    ChunkKind,
    StreamedToolResult,
    StreamingMetadata,
    StreamingSession,
    ToolChunk,
)

SCHEMA_VERSION = "1"


class StreamingSerializer:
    def __init__(self, tool_serializer: ToolSerializer | None = None) -> None:
        self._tool = tool_serializer or ToolSerializer()

    # ── ToolChunk ─────────────────────────────────────────────────────────────
    def chunk_to_dict(self, c: ToolChunk) -> dict[str, Any]:
        return {
            "id": c.id, "index": c.index, "delta": c.delta, "kind": c.kind.value,
            "done": c.done, "metadata": dict(c.metadata),
        }

    def chunk_from_dict(self, d: Mapping[str, Any]) -> ToolChunk:
        return ToolChunk(
            id=d["id"], index=d["index"], delta=d.get("delta"),
            kind=ChunkKind(d.get("kind", ChunkKind.PARTIAL.value)), done=d.get("done", False),
            metadata=dict(d.get("metadata", {})),
        )

    # ── StreamingMetadata ─────────────────────────────────────────────────────
    def stream_metadata_to_dict(self, m: StreamingMetadata) -> dict[str, Any]:
        return {"streamed": m.streamed, "chunk_count": m.chunk_count,
                "first_chunk_index": m.first_chunk_index}

    def stream_metadata_from_dict(self, d: Mapping[str, Any]) -> StreamingMetadata:
        return StreamingMetadata(
            streamed=d.get("streamed", False), chunk_count=d.get("chunk_count", 0),
            first_chunk_index=d.get("first_chunk_index", 0),
        )

    # ── StreamingSession ──────────────────────────────────────────────────────
    def session_to_dict(self, s: StreamingSession) -> dict[str, Any]:
        return {
            "schema_version": SCHEMA_VERSION,
            "chunks": [self.chunk_to_dict(c) for c in s.chunks],
            "partial_output": s.partial_output, "final": s.final, "cancelled": s.cancelled,
            "backpressure_events": s.backpressure_events, "timed_out": s.timed_out,
            "metadata": self.stream_metadata_to_dict(s.metadata),
        }

    def session_from_dict(self, d: Mapping[str, Any]) -> StreamingSession:
        return StreamingSession(
            chunks=tuple(self.chunk_from_dict(c) for c in d.get("chunks", ())),
            partial_output=d.get("partial_output"), final=d.get("final", False),
            cancelled=d.get("cancelled", False),
            backpressure_events=d.get("backpressure_events", 0),
            timed_out=d.get("timed_out", False),
            metadata=self.stream_metadata_from_dict(d.get("metadata", {})),
        )

    # ── StreamedToolResult ────────────────────────────────────────────────────
    def streamed_result_to_document(self, s: StreamedToolResult) -> dict[str, Any]:
        return {
            "schema_version": SCHEMA_VERSION,
            "result": self._tool.result_to_document(s.result),
            "session": self.session_to_dict(s.session),
        }

    def streamed_result_from_document(self, d: Mapping[str, Any]) -> StreamedToolResult:
        return StreamedToolResult(
            result=self._tool.result_from_document(d["result"]),
            session=self.session_from_dict(d["session"]),
        )
