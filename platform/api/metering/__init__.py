"""`api.metering` — the platform side of the metric-stream seam (M10.10, ADR-0039).

**The first `api → billing` edge in the repository.** It exists because ADR-0033 **MS4** makes
`MetricStreamPort` the *single* inbound path into Billing, **MS6** places the implementation
outside `platform/billing/**`, and the Epic 10 plan §M10.10 assigns it to additive new modules
under `platform/api/`. This package is those modules.

**Ownership & boundaries.** `api.metering → billing.foundation, core` (+ stdlib). It imports no
`runtime`, no `observability`, no `events`, no provider and no other `api` layer, and **nothing
in Billing imports it** — the edge is one-way, so the graph stays acyclic (**AR-15**). It is a new
package precisely so that every pre-existing file under `platform/api/` stays byte-for-byte
unchanged (**CV5**).

`platform/billing/**` is untouched by this package. Billing continues to **pull** through the
frozen port, never subscribes to the bus, never opens a connection and never holds a runtime
resource (MS2/MS5), and `NullMetricStream` remains its default — so a build that forgets to wire
this buffer ingests nothing rather than inventing usage (**CV10**).
"""

from __future__ import annotations

from .metric_buffer import (
    BUFFER_CAPACITY,
    METRIC_TURN_FILTER,
    PRODUCER,
    RAW_UNIT,
    BufferCounters,
    MetricTurnBuffer,
    canonical_payload_digest,
)

__all__ = [
    "BUFFER_CAPACITY",
    "METRIC_TURN_FILTER",
    "PRODUCER",
    "RAW_UNIT",
    "BufferCounters",
    "MetricTurnBuffer",
    "canonical_payload_digest",
]
