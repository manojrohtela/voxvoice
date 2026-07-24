"""Legacy basic counters (M6.1B-1) — kept verbatim; `ToolRuntime` (frozen) still
imports `Metric` from this package's top level. No behaviour change.
"""

from __future__ import annotations


class Metric:
    RECEIVED = "tool.received"
    VALIDATING = "tool.validating"
    EXECUTING = "tool.executing"
    COMPLETED = "tool.completed"
    FAILED = "tool.failed"
    CANCELLED = "tool.cancelled"
    EXPIRED = "tool.expired"
    REQUEST_LATENCY = "tool.request.latency"
