"""StreamingMetrics (M6.1B-4) — in-memory counters only. No Prometheus, no
OpenTelemetry, no exporters. Mirrors `tool.observability.ToolRuntimeMetrics`'s
basic-counter posture, scoped to the streaming lifecycle.
"""

from __future__ import annotations

from .domain import StreamingSession


class StreamingMetrics:
    def __init__(self) -> None:
        self._started = 0
        self._completed = 0
        self._cancelled = 0
        self._timed_out = 0
        self._total_chunks = 0
        self._backpressure_events = 0

    def record_start(self) -> None:
        self._started += 1

    def record_session(self, session: StreamingSession) -> None:
        if session.cancelled:
            self._cancelled += 1
        elif session.timed_out:
            self._timed_out += 1
        else:
            self._completed += 1
        self._total_chunks += len(session.chunks)
        self._backpressure_events += session.backpressure_events

    @property
    def average_chunks_per_stream(self) -> float:
        total = self._completed + self._cancelled + self._timed_out
        return self._total_chunks / total if total else 0.0

    def snapshot(self) -> dict[str, object]:
        return {
            "streams_started": self._started, "streams_completed": self._completed,
            "streams_cancelled": self._cancelled, "streams_timed_out": self._timed_out,
            "total_chunks": self._total_chunks, "backpressure_events": self._backpressure_events,
            "average_chunks_per_stream": self.average_chunks_per_stream,
        }
