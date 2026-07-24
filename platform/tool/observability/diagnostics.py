"""ToolRuntimeDiagnostics (M6.1B-3) — a read-only aggregator. Never mutates the
registry/catalog/repository it inspects; every value is already computed by its
owner (registry/catalog/governance/repository/metrics), never recomputed here.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field

from ..catalog import ToolCapabilityCatalog
from ..registry import ToolRegistry
from .config import RUNTIME_VERSION, ObservabilityConfiguration
from .metrics import ToolRuntimeMetrics


@dataclass(frozen=True, slots=True)
class ToolRuntimeDiagnostics:
    runtime_version: str
    config_fingerprint: str
    registry_stats: Mapping[str, object] = field(default_factory=dict)
    capability_stats: Mapping[str, object] = field(default_factory=dict)
    governance_stats: Mapping[str, object] = field(default_factory=dict)
    repository_stats: Mapping[str, object] = field(default_factory=dict)
    replay_stats: Mapping[str, object] = field(default_factory=dict)
    metric_snapshot: Mapping[str, object] = field(default_factory=dict)
    health_summary: str = "unknown"


def build_diagnostics(
    *,
    config: ObservabilityConfiguration,
    registry: ToolRegistry,
    catalog: ToolCapabilityCatalog,
    metrics: ToolRuntimeMetrics,
    governance_stats: Mapping[str, object] | None = None,
    repository_result_count: int = 0,
    replay_count: int = 0,
    audit_count: int = 0,
    healthy: bool = True,
) -> ToolRuntimeDiagnostics:
    names = registry.names()
    snapshot = metrics.snapshot()
    return ToolRuntimeDiagnostics(
        runtime_version=RUNTIME_VERSION, config_fingerprint=config.fingerprint(),
        registry_stats={"tools": names, "count": len(names)},
        capability_stats={"catalog": catalog.catalog()},
        governance_stats=dict(governance_stats or {}),
        repository_stats={"results": repository_result_count},
        replay_stats={
            "replays": replay_count, "audits": audit_count,
            "replay_success": snapshot["replay_success"],
            "replay_mismatch": snapshot["replay_mismatch"],
        },
        metric_snapshot=snapshot,
        health_summary="healthy" if healthy else "unhealthy",
    )
