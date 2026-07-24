"""Planning statistics (M6.2B-2) — pure aggregation over already-computed
`SkillPlan` sequences. No I/O, no side effects. Mirrors
`tool.observability.statistics.compute_statistics`'s shape.
"""

from __future__ import annotations

from collections.abc import Sequence

from .domain import PlanningStatistics, SkillPlan


def compute_statistics(plans: Sequence[SkillPlan]) -> PlanningStatistics:
    per_root: dict[str, int] = {}
    per_namespace: dict[str, int] = {}
    total_steps = 0
    total_caps = 0

    for p in plans:
        per_root[p.qualified_name] = per_root.get(p.qualified_name, 0) + 1
        per_namespace[p.namespace] = per_namespace.get(p.namespace, 0) + 1
        total_steps += len(p.steps)
        total_caps += len(p.capability_requirements)

    count = len(plans)
    return PlanningStatistics(
        per_root=per_root, per_namespace=per_namespace, plan_count=count,
        avg_steps=(total_steps / count) if count else 0.0,
        avg_capabilities=(total_caps / count) if count else 0.0,
    )
