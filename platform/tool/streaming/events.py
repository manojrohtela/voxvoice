"""Streaming events (M6.1B-4) — phase constants + their `EventType` mapping.
Reuses the existing `Event`/`EventBus`/`EventSource.TOOL` envelope architecture
unchanged; the only change to `core.events` is five additive `EventType` members.
"""

from __future__ import annotations

from enum import StrEnum

from core.events import EventType


class StreamingPhase(StrEnum):
    STREAM_STARTED = "stream_started"
    STREAM_CHUNK = "stream_chunk"
    STREAM_COMPLETED = "stream_completed"
    STREAM_CANCELLED = "stream_cancelled"
    STREAM_TIMED_OUT = "stream_timed_out"


PHASE_EVENT_TYPE: dict[StreamingPhase, EventType] = {
    StreamingPhase.STREAM_STARTED: EventType.TOOL_STREAM_STARTED,
    StreamingPhase.STREAM_CHUNK: EventType.TOOL_STREAM_CHUNK,
    StreamingPhase.STREAM_COMPLETED: EventType.TOOL_STREAM_COMPLETED,
    StreamingPhase.STREAM_CANCELLED: EventType.TOOL_STREAM_CANCELLED,
    StreamingPhase.STREAM_TIMED_OUT: EventType.TOOL_STREAM_TIMED_OUT,
}
