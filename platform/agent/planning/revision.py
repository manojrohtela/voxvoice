"""Plan revision (M6.5B-2) — decomposition run again with more context,
never a bespoke retry heuristic (§5 of the M6.5A design). On a delegated
workflow's partial/failed result, the caller assembles a fresh
`ContextRequest` (now including the prior outcome) and this module re-runs
the same deterministic decompose-then-parse pipeline `decomposer.py` uses,
producing a new `AgentPlan` with `revision = previous.revision + 1`.
"""

from __future__ import annotations

from collections.abc import Callable

from core.ports.context import ContextPort, ContextRequest
from core.ports.reasoning import ReasoningPort, ReasoningRequest

from ..domain import AgentDecision, AgentGoal
from .decisions import record_decision
from .decomposer import context_ref_for, parse_plan_steps
from .domain import AgentPlan
from .hashing import compute_plan_hash
from .ids import plan_id_for


def revise_plan(
    plan: AgentPlan, goal: AgentGoal, *, context_request: ContextRequest,
    context_port: ContextPort, reasoning_port: ReasoningPort, model: str | None = None,
    clock: Callable[[], float] | None = None,
) -> tuple[AgentPlan, AgentDecision]:
    now = clock() if clock is not None else 0.0
    assembled = context_port.assemble(context_request)
    request = ReasoningRequest(
        session_id=context_request.session_id, context_ref=context_ref_for(assembled),
        model=model, org_id=context_request.org_id,
    )
    decisions = list(reasoning_port.run(request))
    steps = parse_plan_steps(decisions, goal_id=goal.goal_id)
    new_plan_id = plan_id_for(goal.goal_id, plan.revision + 1, now)
    revised = plan.revised(steps=steps, plan_id=new_plan_id, created_at=now)
    revised = revised.with_hash(compute_plan_hash(revised))
    decision = record_decision(
        goal_id=goal.goal_id, kind="revise_plan",
        payload=(revised.plan_id, revised.revision), sequence=0, clock=clock,
    )
    return revised, decision
