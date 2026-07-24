"""Goal decomposition (M6.5B-2) — an ordinary `ReasoningPort.run` call.

Per the M6.5A design (§5, §11 item 2): goal decomposition is Agent-structured
request/response handling over the already-frozen `core.ports.reasoning.
ReasoningPort` — no bespoke planning intelligence lives here. Agent Runtime
code never decides *what* the plan's steps should be; it only assembles a
budgeted context (via the already-frozen `core.ports.context.ContextPort` —
no duplicate seam), builds the `ReasoningRequest`, and deterministically
parses the returned `Decision(kind=FINAL)` payload into `PlanStep` objects.
Streamed non-FINAL decisions (TOKEN/TOOL_CALL/HANDOFF) are observed but
never acted on — Agent Runtime never executes a tool itself, and the last
FINAL decision's payload is the sole source of truth for step construction,
deterministic given the same decision sequence.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence

from core.ports.context import AssembledContext, ContextPort, ContextRequest
from core.ports.reasoning import Decision, DecisionKind, ReasoningPort, ReasoningRequest

from ..domain import AgentGoal
from ..ids import content_hash
from .domain import AgentPlan, PlanStep
from .hashing import compute_plan_hash
from .ids import plan_id_for, step_id_for


def context_ref_for(assembled: AssembledContext) -> str:
    """A content-hash reference to the assembled context — never the raw
    text itself. `ReasoningRequest.context_ref` is documented as "an opaque
    handle to an `AssembledContext`" (`core.ports.reasoning`); Agent Runtime
    derives that handle deterministically, since `ContextPort.assemble`
    returns the object itself rather than a persisted reference id."""
    item_reprs = tuple(f"{it.kind}|{it.text}|{it.score}" for it in assembled.items)
    return content_hash(
        assembled.request.session_id, assembled.used_tokens, assembled.truncated, *item_reprs,
    )


def parse_plan_steps(decisions: Sequence[Decision], *, goal_id: str) -> tuple[PlanStep, ...]:
    """Deterministic parse of the last `Decision(kind=FINAL)`'s payload into
    `PlanStep`s. Streamed non-FINAL decisions (TOKEN/TOOL_CALL/HANDOFF) are
    observed but never acted on — Agent Runtime never executes a tool
    itself; the last FINAL decision is the sole source of truth, so parsing
    is deterministic given the same decision sequence."""
    finals = [d for d in decisions if d.kind is DecisionKind.FINAL]
    if not finals:
        return ()
    raw_steps = finals[-1].payload.get("steps", ())
    steps: list[PlanStep] = []
    for i, raw in enumerate(raw_steps):
        steps.append(PlanStep(
            step_id=raw.get("step_id") or step_id_for(goal_id, i),
            kind=raw["kind"],
            workflow_ref=raw.get("workflow_ref"),
            inputs=dict(raw.get("inputs", {})),
        ))
    return tuple(steps)


def decompose_goal(
    goal: AgentGoal, *, context_request: ContextRequest, context_port: ContextPort,
    reasoning_port: ReasoningPort, model: str | None = None,
    clock: Callable[[], float] | None = None,
) -> tuple[AgentPlan, AssembledContext]:
    """Assembles a budgeted context, calls the injected `ReasoningPort`, and
    deterministically parses the result into an `AgentPlan`. Never a second
    inference engine — the content of the decomposition is entirely
    Reasoning Runtime's; this function only structures the request and
    parses the response."""
    now = clock() if clock is not None else 0.0
    assembled = context_port.assemble(context_request)
    request = ReasoningRequest(
        session_id=context_request.session_id, context_ref=context_ref_for(assembled),
        model=model, org_id=context_request.org_id,
    )
    decisions = list(reasoning_port.run(request))
    steps = parse_plan_steps(decisions, goal_id=goal.goal_id)
    plan = AgentPlan(
        plan_id=plan_id_for(goal.goal_id, 0, now), goal_id=goal.goal_id, steps=steps,
        revision=0, created_at=now,
    )
    return plan.with_hash(compute_plan_hash(plan)), assembled
