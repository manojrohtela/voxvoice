"""Streaming domain model (M6.1B-4) — deterministic, offline value objects.

Mirrors the established Epic 5 streaming shape (`provider.ProviderChunk`/
`StreamingMetadata`, `reasoning.StreamingSession`) — duplicated locally, not
imported (`tool` never imports a sibling runtime; see `tool/_run.py`'s own
precedent). `StreamedToolResult` **composes** the frozen `tool.domain.ToolResult`
rather than modifying it — no redesign of the M6.1B-1 core.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from ..domain import ToolResult

__all__ = [
    "ChunkKind",
    "ToolChunk",
    "StreamingMetadata",
    "Verdict",
    "StreamingSession",
    "StreamedToolResult",
]


class ChunkKind(StrEnum):
    PARTIAL = "partial"
    FINAL = "final"


@dataclass(frozen=True, slots=True)
class ToolChunk:
    """One streamed piece of a tool's output. Immutable. `done` marks the
    terminal chunk (mirrors `provider.ProviderChunk`)."""

    id: str
    index: int
    delta: Any
    kind: ChunkKind = ChunkKind.PARTIAL
    done: bool = False
    metadata: Mapping[str, str] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class StreamingMetadata:
    streamed: bool = False
    chunk_count: int = 0
    first_chunk_index: int = 0


@dataclass(frozen=True, slots=True)
class Verdict:
    """A local copy of `reasoning.policies.Verdict`'s two-field shape — no I/O,
    no side effects; the caller acts on it."""

    ok: bool
    reason: str = ""


@dataclass(frozen=True, slots=True)
class StreamingSession:
    """The deterministic record of one streaming pass (mirrors
    `reasoning.streaming.StreamingSession`)."""

    chunks: tuple[ToolChunk, ...] = ()
    partial_output: Any = None
    final: bool = False
    cancelled: bool = False
    backpressure_events: int = 0
    timed_out: bool = False
    metadata: StreamingMetadata = field(default_factory=StreamingMetadata)


@dataclass(frozen=True, slots=True)
class StreamedToolResult:
    """Composes the frozen `ToolResult` with the streaming session that produced
    it — never a replacement for `ToolResult`, never a new execution shape."""

    result: ToolResult
    session: StreamingSession
