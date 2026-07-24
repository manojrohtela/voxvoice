"""StreamingDiagnostics (M6.1B-4) — a read-only aggregator, mirrors
`tool.observability.ToolRuntimeDiagnostics`'s posture: never mutates the
registry/metrics it inspects.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field

from ..observability.config import RUNTIME_VERSION
from ..registry import ToolRegistry
from .metrics import StreamingMetrics
from .policies import StreamingPolicy


@dataclass(frozen=True, slots=True)
class StreamingDiagnostics:
    runtime_version: str
    policy_snapshot: Mapping[str, object] = field(default_factory=dict)
    registry_stats: Mapping[str, object] = field(default_factory=dict)
    metric_snapshot: Mapping[str, object] = field(default_factory=dict)
    health_summary: str = "unknown"


def build_streaming_diagnostics(
    *, registry: ToolRegistry, metrics: StreamingMetrics, policy: StreamingPolicy,
    healthy: bool = True,
) -> StreamingDiagnostics:
    names = registry.names()
    return StreamingDiagnostics(
        runtime_version=RUNTIME_VERSION,
        policy_snapshot={
            "max_buffered_chunks": policy.max_buffered_chunks,
            "timeout_seconds": policy.timeout.seconds, "emit_partial": policy.emit_partial,
        },
        registry_stats={"tools": names, "count": len(names)},
        metric_snapshot=metrics.snapshot(),
        health_summary="healthy" if healthy else "unhealthy",
    )
