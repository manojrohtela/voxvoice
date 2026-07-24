"""AgentPlanner orchestration (M6.5B-2) — composes goal decomposition,
capability resolution, workflow selection, and delegation into one
deterministic pipeline, and records the `AgentDecision` audit trail.

Planning is structure, never intelligence (§5, §11 item 2 of the M6.5A
design): every decision this module records is either a deterministic parse
of a `ReasoningPort` response, or a pure pass-through call to an injected
seam — no bespoke planning heuristic lives here. **No execution occurs
here** — `delegate_plan` only submits execution requests via
`AgentWorkflowPort`; it never constructs, runs, or awaits a `WorkflowGraph`.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass

from core.ports.context import ContextPort, ContextRequest
from core.ports.reasoning import ReasoningPort

from ..domain import AgentCapability, AgentDecision, AgentGoal
from ..extensions import AgentCapabilityResolver, AgentWorkflowPort
from .capability import resolve_capabilities
from .decisions import record_decision
from .decomposer import decompose_goal
from .delegation import delegate_step
from .domain import AgentPlan, ResolvedCapability

__all__ = ["PlanningResult", "DelegationResult", "plan_goal", "delegate_plan"]


@dataclass(frozen=True, slots=True)
class PlanningResult:
    plan: AgentPlan
    resolved_capabilities: tuple[ResolvedCapability, ...]
    decisions: tuple[AgentDecision, ...]


@dataclass(frozen=True, slots=True)
class DelegationResult:
    execution_ids: Mapping[str, str]  # step_id -> execution_id
    decisions: tuple[AgentDecision, ...]


def plan_goal(
    goal: AgentGoal, *, capabilities: Sequence[AgentCapability],
    context_request: ContextRequest, context_port: ContextPort,
    reasoning_port: ReasoningPort, capability_resolver: AgentCapabilityResolver,
    model: str | None = None, clock: Callable[[], float] | None = None,
) -> PlanningResult:
    """Resolves declared capabilities, then decomposes the goal into an
    `AgentPlan` — recording one `AgentDecision` per structural step taken.
    Never delegates (see `delegate_plan`); never executes anything."""
    the_clock = clock or (lambda: 0.0)
    sequence = 0
    decisions: list[AgentDecision] = []

    resolved = resolve_capabilities(capabilities, resolver=capability_resolver)
    decisions.append(record_decision(
        goal_id=goal.goal_id, kind="resolve_capability",
        payload=tuple((r.requirement.kind, r.requirement.tag, r.available) for r in resolved),
        sequence=sequence, clock=the_clock,
    ))
    sequence += 1

    plan, _assembled = decompose_goal(
        goal, context_request=context_request, context_port=context_port,
        reasoning_port=reasoning_port, model=model, clock=the_clock,
    )
    decisions.append(record_decision(
        goal_id=goal.goal_id, kind="decompose",
        payload=(plan.plan_id, len(plan.steps)), sequence=sequence, clock=the_clock,
    ))
    sequence += 1

    for step in plan.steps:
        if step.kind == "delegate_workflow" and step.workflow_ref:
            decisions.append(record_decision(
                goal_id=goal.goal_id, kind="select_workflow",
                payload=(step.step_id, step.workflow_ref), sequence=sequence, clock=the_clock,
            ))
            sequence += 1

    return PlanningResult(plan=plan, resolved_capabilities=resolved, decisions=tuple(decisions))


def delegate_plan(
    plan: AgentPlan, *, workflow_port: AgentWorkflowPort, clock: Callable[[], float] | None = None,
) -> DelegationResult:
    """Submits every `delegate_workflow` step's execution request via
    `AgentWorkflowPort.submit` — never constructs or executes a
    `WorkflowGraph`. Recording, not orchestrating: driving the goal FSM to
    `AWAITING_WORKFLOW` and polling/awaiting the result is Bundle 3's
    `AgentExecutionEngine` scope, not this bundle's."""
    the_clock = clock or (lambda: 0.0)
    execution_ids: dict[str, str] = {}
    decisions: list[AgentDecision] = []
    sequence = 0
    for step in plan.steps:
        if step.kind != "delegate_workflow":
            continue
        execution_id = delegate_step(step, workflow_port=workflow_port)
        execution_ids[step.step_id] = execution_id
        decisions.append(record_decision(
            goal_id=plan.goal_id, kind="delegate",
            payload=(step.step_id, execution_id), sequence=sequence, clock=the_clock,
        ))
        sequence += 1
    return DelegationResult(execution_ids=execution_ids, decisions=tuple(decisions))
