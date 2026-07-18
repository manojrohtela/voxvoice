"""Exporter extension points (M5.0) — interfaces only, no exporters.

The seams a future observability stack plugs into: OpenTelemetry (spans/metrics) and
Prometheus (metrics scrape). Defining them now keeps `telemetry` transport-neutral
and lets a deployment wire an exporter with no change to the modules producing the
data. No implementation lands at M5.0 (non-goal).
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from .metrics import Telemetry
from .tracing import Span


class MetricsExporter(ABC):
    """Drains a `Telemetry` snapshot to a metrics backend (Prometheus/OTel/StatsD)."""

    name: str

    @abstractmethod
    def export(self, telemetry: Telemetry) -> None: ...


class SpanExporter(ABC):
    """Ships finished spans to a tracing backend (OpenTelemetry/Jaeger/…)."""

    name: str

    @abstractmethod
    def export(self, spans: tuple[Span, ...]) -> None: ...
