"""Deterministic `AgentPlan` hashing (M6.5B-2) — mirrors
`workflow.graph.compute_graph_hash`'s discipline: always planner-computed,
never trusted from a caller-supplied value.
"""

from __future__ import annotations

from ..ids import content_hash
from .domain import AgentPlan, PlanStep


def _step_repr(s: PlanStep) -> str:
    return f"{s.step_id}|{s.kind}|{s.workflow_ref}|{sorted(s.inputs.items())}"


def compute_plan_hash(plan: AgentPlan) -> str:
    step_hashes = sorted(_step_repr(s) for s in plan.steps)
    return content_hash(plan.goal_id, plan.revision, *step_hashes)
