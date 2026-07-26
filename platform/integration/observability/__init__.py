"""integration.observability — telemetry *aggregation* only (M6.7B-2).

Per the M6.7A design §14: Integration mints one `trace_id`, and every
capability owns its own execution trace / `*Statistics` / structured events.
This package aggregates *references* to that existing telemetry and correlates
it by the shared `trace_id` — it never constructs a capability span, never
recomputes a metric, and never introduces a new counter/gauge (§14 "Avoiding
duplicate telemetry"). The one genuinely new measurement is the
composition-root's *own* added routing latency (`PipelineRoutedReasoningPort`),
which no capability owns — instrumented here, per this bundle's Latency
requirement.
"""

from .latency import LatencyRecorder, RoutingLatencySample, RoutingLatencyStats
from .telemetry import PlatformCorrelation, PlatformMetrics, PlatformSpan, PlatformTrace

__all__ = [
    "PlatformTrace",
    "PlatformSpan",
    "PlatformMetrics",
    "PlatformCorrelation",
    "LatencyRecorder",
    "RoutingLatencyStats",
    "RoutingLatencySample",
]
