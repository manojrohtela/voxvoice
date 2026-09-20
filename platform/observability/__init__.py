"""observability — metrics, tracing, cost, logging config (Constitution §A15/§A11).

Makes invariant I1 (latency is a feature) and I2 (conversations are observable)
real: per-turn latency taps feeding `metric.turn`, tracing per turn, the
per-session cost meter (which doubles as the billing meter), and the structured
logging + secret-redaction sink configuration.

**M10.9 (ADR-0038)** makes this a real producer: `TurnLatencyTap` records the six ratified
stage boundaries through one **injected monotonic** clock and emits exactly one `metric.turn`
per terminal turn, through the ordinary `EventPublisher` path. It is the **sole producer** of
runtime `metric.*` (ADR-0033 MS8, AR-2).

Still boundary-only for everything else: tracing, the per-session cost meter and logging
configuration are **not** built here yet, and this package is deliberately not a tracing or
logging framework. Imports `core` and stdlib only.
"""

from .turn_latency import (
    ADDITIVE_INTERVALS,
    AUDIO_BOUNDARIES,
    KNOWN_BOUNDARIES,
    LIFECYCLE_MARKERS,
    STAGE_BOUNDARIES,
    STAGE_INTERVALS,
    TERMINAL_STATUSES,
    MetricPublisher,
    TurnLatencyTap,
)

__all__ = [
    "TurnLatencyTap",
    "MetricPublisher",
    "ADDITIVE_INTERVALS",
    "AUDIO_BOUNDARIES",
    "STAGE_BOUNDARIES",
    "LIFECYCLE_MARKERS",
    "KNOWN_BOUNDARIES",
    "STAGE_INTERVALS",
    "TERMINAL_STATUSES",
]
