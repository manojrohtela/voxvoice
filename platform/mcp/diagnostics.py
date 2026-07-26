"""MCPDiagnostics (M6.8B-2) — MCP-local diagnostics aggregation.

Aggregates the MCP Runtime's own statistics + metrics snapshot into a single
diagnostics object. Local only — never another capability's state (Platform
Integration references this via `trace_id` correlation, never recomputes it).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .config import RUNTIME_VERSION
from .domain import MCPRuntimeStatistics
from .observability import MCPMetricsSnapshot


@dataclass(frozen=True, slots=True)
class MCPRuntimeDiagnostics:
    runtime_version: str
    healthy: bool
    statistics: MCPRuntimeStatistics
    metrics: MCPMetricsSnapshot
    health_summary: str = "ok"
    extra: dict[str, Any] = field(default_factory=dict)


def build_diagnostics(
    *,
    statistics: MCPRuntimeStatistics,
    metrics: MCPMetricsSnapshot,
    healthy: bool = True,
) -> MCPRuntimeDiagnostics:
    return MCPRuntimeDiagnostics(
        runtime_version=RUNTIME_VERSION,
        healthy=healthy,
        statistics=statistics,
        metrics=metrics,
        health_summary="ok" if healthy else "degraded",
    )
