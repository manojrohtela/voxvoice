"""Planning statistics (M6.5B-2) — pure aggregation over already-generated
`AgentPlan` sequences. No I/O, no side effects.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field

from .domain import AgentPlan


@dataclass(frozen=True, slots=True)
class PlanningStatistics:
    plan_count: int = 0
    avg_step_count: float = 0.0
    per_step_kind: dict[str, int] = field(default_factory=dict)


def compute_planning_statistics(plans: Sequence[AgentPlan]) -> PlanningStatistics:
    per_kind: dict[str, int] = {}
    total_steps = 0
    for p in plans:
        for s in p.steps:
            per_kind[s.kind] = per_kind.get(s.kind, 0) + 1
        total_steps += len(p.steps)
    count = len(plans)
    return PlanningStatistics(
        plan_count=count, avg_step_count=(total_steps / count) if count else 0.0,
        per_step_kind=per_kind,
    )
