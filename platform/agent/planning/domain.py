"""Agent planning domain model (M6.5B-2) — immutable value objects.

Per the approved M6.5A design (§4.5, §4.6): `PlanStep` is one data-only shape
with a `kind` discriminator — never a class hierarchy, the same "never a
class hierarchy" discipline `WorkflowNode` established. `AgentPlan` carries
a planner-computed `plan_hash`, never a caller-supplied one (mirrors
`WorkflowGraph.graph_hash`'s own discipline). `ResolvedCapability` is the
capability-resolution result shape `AgentCapabilityResolver` (Bundle 1)
returns.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from typing import Any

from ..domain import AgentCapability

__all__ = ["PlanStep", "AgentPlan", "ResolvedCapability"]


@dataclass(frozen=True, slots=True)
class PlanStep:
    step_id: str
    kind: str  # "delegate_workflow" | "await_result" | "revise"
    workflow_ref: str | None = None
    inputs: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class AgentPlan:
    plan_id: str
    goal_id: str
    steps: tuple[PlanStep, ...] = ()
    plan_hash: str = ""
    revision: int = 0
    created_at: float = 0.0

    def with_hash(self, plan_hash: str) -> AgentPlan:
        return replace(self, plan_hash=plan_hash)

    def revised(
        self, *, steps: tuple[PlanStep, ...], plan_id: str, created_at: float,
    ) -> AgentPlan:
        """Plan revision is decomposition run again with more context — never
        a bespoke retry heuristic (§5 of the M6.5A design). `plan_hash` is
        cleared; the caller (`revision.revise_plan`) always recomputes it."""
        return replace(
            self, plan_id=plan_id, steps=steps, revision=self.revision + 1,
            created_at=created_at, plan_hash="",
        )


@dataclass(frozen=True, slots=True)
class ResolvedCapability:
    requirement: AgentCapability
    resolved_ref: str | None
    available: bool
