"""Tracing primitives (M5.0) — spans + a tracer, with replay correlation.

Deterministic, in-process, stdlib-only. A `Span` carries `correlation_id` /
`session_id` so traces line up with recorded events (Replay correlation). No exporter
here — `exporters.py` holds the OpenTelemetry extension point; the default is in-
memory collection.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True)
class Span:
    name: str
    span_id: str
    trace_id: str
    start: float
    end: float | None = None
    parent_id: str | None = None
    correlation_id: str | None = None
    session_id: str | None = None
    attributes: dict[str, Any] = field(default_factory=dict)
    events: list[tuple[float, str]] = field(default_factory=list)

    @property
    def duration(self) -> float:
        return (self.end - self.start) if self.end is not None else 0.0

    def set(self, key: str, value: Any) -> None:
        self.attributes[key] = value

    def add_event(self, name: str, *, now: float) -> None:
        self.events.append((now, name))


class Tracer:
    """Creates spans + collects finished ones (an exporter can drain them)."""

    def __init__(
        self,
        *,
        clock: Callable[[], float] | None = None,
        id_factory: Callable[[], str] | None = None,
    ) -> None:
        import uuid

        self._clock = clock or time.perf_counter
        self._id = id_factory or (lambda: uuid.uuid4().hex[:16])
        self._finished: list[Span] = []

    @property
    def finished(self) -> tuple[Span, ...]:
        return tuple(self._finished)

    @contextmanager
    def span(
        self,
        name: str,
        *,
        trace_id: str | None = None,
        parent_id: str | None = None,
        correlation_id: str | None = None,
        session_id: str | None = None,
    ):
        s = Span(
            name=name,
            span_id=self._id(),
            trace_id=trace_id or self._id(),
            start=self._clock(),
            parent_id=parent_id,
            correlation_id=correlation_id,
            session_id=session_id,
        )
        try:
            yield s
        finally:
            s.end = self._clock()
            self._finished.append(s)
