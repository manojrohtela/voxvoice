"""Planning observability (M6.2B-2) — a pure `SkillPlan` projection, plus a
concrete, in-memory default implementation of Bundle 1's
`SkillObservabilitySink` interface (`extensions.py`, untouched). Owns only
planning traces — there is no `skill.execution.*` trace, since a Skill never
executes (§14 of the M6.2A design).
"""

from __future__ import annotations

from ..extensions import SkillObservabilitySink
from .domain import SkillPlan, SkillPlanningTrace
from .ids import trace_id_for


def planning_trace_for(plan: SkillPlan, *, now: float = 0.0) -> SkillPlanningTrace:
    return SkillPlanningTrace(
        trace_id=trace_id_for(plan.plan_id), plan_id=plan.plan_id,
        qualified_name=plan.qualified_name, step_count=len(plan.steps),
        capability_count=len(plan.capability_requirements), created_at=now,
    )


class InMemoryPlanningObservabilitySink(SkillObservabilitySink):
    """The default, in-memory recorder — never a real telemetry backend
    (that remains a future, additive extension, not this bundle's scope)."""

    def __init__(self) -> None:
        self._records: list[object] = []

    def record(self, event: object) -> None:
        self._records.append(event)

    def all(self) -> tuple[object, ...]:
        return tuple(self._records)

    def count(self) -> int:
        return len(self._records)
