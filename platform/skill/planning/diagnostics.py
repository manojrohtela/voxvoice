"""PlanningDiagnostics (M6.2B-2) — a read-only aggregator. Never mutates the
registry/repository it inspects; every value is already computed by its
owner, never recomputed here. Mirrors
`tool.observability.diagnostics.build_diagnostics`'s shape.
"""

from __future__ import annotations

from ..registry import SkillRegistry
from .config import RUNTIME_VERSION, PlanningConfiguration
from .domain import PlanningDiagnostics


def build_diagnostics(
    *,
    config: PlanningConfiguration,
    registry: SkillRegistry,
    plan_count: int = 0,
    replay_count: int = 0,
    healthy: bool = True,
) -> PlanningDiagnostics:
    names = registry.names()
    return PlanningDiagnostics(
        runtime_version=RUNTIME_VERSION, config_fingerprint=config.fingerprint(),
        registry_stats={"skills": names, "count": len(names)},
        planning_stats={"plans": plan_count},
        replay_stats={"replays": replay_count},
        health_summary="healthy" if healthy else "unhealthy",
    )
