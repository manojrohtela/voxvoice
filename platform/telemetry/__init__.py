"""telemetry — the observability foundation (M5.0).

Deterministic, in-process, stdlib-only metrics + tracing + structured logging, with
OpenTelemetry / Prometheus **extension points** (no exporters yet). `Telemetry`
satisfies `core.module.Meter` structurally, so any RuntimeModule can use it as its
meter without `core` depending on `telemetry` (dependency inversion). A floor
utility: imports nothing above it.
"""

from .exporters import MetricsExporter, SpanExporter
from .logging import StructuredLogger
from .metrics import Histogram, Telemetry
from .tracing import Span, Tracer

__all__ = [
    "Telemetry",
    "Histogram",
    "Tracer",
    "Span",
    "StructuredLogger",
    "MetricsExporter",
    "SpanExporter",
]
