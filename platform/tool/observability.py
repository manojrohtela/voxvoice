"""Tool observability (M6.1B-1) — basic counters only. No metrics engine, no
dashboards (an explicit non-goal here; a full observability engine is M6.1B-3).

The canonical meter-key constants; `ToolStatistics` (the read-side snapshot) lives
in `domain`.
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
