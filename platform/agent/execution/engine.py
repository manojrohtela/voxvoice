"""AgentExecutionEngine (M6.5B-3) — drives Bundle 1's goal-scoped
`AgentGoalState` FSM by composing Bundle 2's planning pipeline
(`plan_goal`/`delegate_plan`/`revise_plan`) with the Agent-owned
`AgentWorkflowPort` seam. **Never executes a Tool/Skill/Workflow directly**
— workflow dispatch always goes through the injected `AgentWorkflowPort`;
this engine never constructs or runs a `WorkflowGraph`.

**Goal recovery, honestly scoped to the frozen FSM**: Bundle 1's
`AgentGoalState` FSM (frozen, `agent.lifecycle`) has no dedicated
`RECOVERING` state — unlike `workflow.execution`'s own `WorkflowExecutionState`,
which does. This engine does not invent one (that would redesign Bundle 1's
already-approved architecture). Instead, "recovery" here means **session/
process continuity**: `recover()` re-hydrates from a persisted
`AgentGoalSnapshot` and continues driving the goal from wherever it was —
if `AWAITING_WORKFLOW`, that is exactly a `poll()` call; any other
non-terminal state is returned unchanged for the caller to continue
driving. It never restarts decomposition/delegation from `RECEIVED`.

**Handling workflow failure**: when the delegated workflow's status comes
back failed, the engine either revises the plan (if `plan.revision` is
under `max_revisions`) — firing `REVISE` then `DELEGATE` then `AWAIT` in one
call, re-delegating the revised plan and pausing again at
`AWAITING_WORKFLOW` — or, once the revision budget is exhausted, fires
`FAIL`, terminally failing the goal. This is the only place this engine
makes a policy decision; the decision itself (revise vs. fail) is a simple,
deterministic budget check, never a heuristic.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from typing import Any

from core.ports.context import ContextPort, ContextRequest
from core.ports.reasoning import ReasoningPort

from ..domain import AgentCapability, AgentDecision, AgentGoal, AgentGoalState
from ..extensions import AgentCapabilityResolver, AgentWorkflowPort
from ..lifecycle import AgentGoalTrigger, advance_goal_state
from ..planning import AgentPlan, delegate_plan, plan_goal, revise_plan
from .domain import AgentGoalExecution, AgentGoalResult, AgentGoalSnapshot
from .ids import execution_id_for

EngineOutcome = tuple[AgentGoalExecution, AgentGoalResult | None, AgentGoalSnapshot]


class AgentExecutionEngine:
    def __init__(
        self,
        *,
        workflow_port: AgentWorkflowPort | None = None,
        capability_resolver: AgentCapabilityResolver | None = None,
        reasoning_port: ReasoningPort | None = None,
        context_port: ContextPort | None = None,
        clock: Callable[[], float] | None = None,
        max_revisions: int = 1,
    ) -> None:
        import time

        self._workflow_port = workflow_port
        self._capability_resolver = capability_resolver
        self._reasoning_port = reasoning_port
        self._context_port = context_port
        self._clock = clock or time.time
        self._max_revisions = max_revisions

    # ── entry points ─────────────────────────────────────────────────────────
    def submit(
        self, goal: AgentGoal, *, capabilities: Sequence[AgentCapability],
        context_request: ContextRequest,
    ) -> EngineOutcome:
        now = self._clock()
        execution = AgentGoalExecution(
            execution_id=execution_id_for(goal.goal_id, now), goal_id=goal.goal_id,
            agent_id=goal.agent_id, conversation_id=goal.conversation_id,
            state=AgentGoalState.RECEIVED, started_at=now, updated_at=now,
        )
        execution = self._advance(execution, AgentGoalTrigger.DECOMPOSE)

        planning = plan_goal(
            goal, capabilities=capabilities, context_request=context_request,
            context_port=self._context_port, reasoning_port=self._reasoning_port,
            capability_resolver=self._capability_resolver, clock=self._clock,
        )
        execution = self._advance(execution, AgentGoalTrigger.DELEGATE)

        delegation = delegate_plan(planning.plan, workflow_port=self._workflow_port,
                                    clock=self._clock)
        delegated_id = next(iter(delegation.execution_ids.values()), None)
        execution = execution.with_delegation(
            delegated_id, planning.plan.plan_id, updated_at=self._clock(),
        )
        execution = self._advance(execution, AgentGoalTrigger.AWAIT)

        decisions = planning.decisions + delegation.decisions
        snapshot = self._snapshot(execution, plan=planning.plan, decisions=decisions)
        return execution, None, snapshot

    def poll(
        self, execution: AgentGoalExecution, snapshot: AgentGoalSnapshot, goal: AgentGoal,
        *, revision_context_request: ContextRequest | None = None,
    ) -> EngineOutcome:
        if execution.state is not AgentGoalState.AWAITING_WORKFLOW:
            raise ValueError(
                f"cannot poll a goal execution in state {execution.state.value!r}; "
                "only AWAITING_WORKFLOW may be polled"
            )
        status = self._workflow_port.status(execution.delegated_execution_id)

        if status == "completed":
            execution = self._advance(execution, AgentGoalTrigger.COMPLETE)
            result = self._build_result(execution, partial_success=True)
            return execution, result, self._snapshot(
                execution, plan=snapshot.plan, decisions=snapshot.decisions,
            )

        if status == "failed":
            plan = snapshot.plan
            if plan is not None and plan.revision < self._max_revisions:
                context_request = (
                    revision_context_request or self._fallback_context_request(goal)
                )
                return self._revise_and_redelegate(
                    execution, snapshot, goal, context_request=context_request,
                )
            execution = self._advance(execution, AgentGoalTrigger.FAIL)
            error = (
                f"delegated workflow execution {execution.delegated_execution_id!r} failed"
                f" after {plan.revision if plan else 0} revision(s)"
            )
            result = self._build_result(execution, partial_success=False, error=error)
            return execution, result, self._snapshot(
                execution, plan=plan, decisions=snapshot.decisions, pending_error=error,
            )

        # still running -- no state change, no result yet
        return execution, None, snapshot

    def send_signal(
        self, execution: AgentGoalExecution, name: str, payload: Mapping[str, Any],
    ) -> None:
        if execution.delegated_execution_id is None:
            raise ValueError(f"goal {execution.goal_id!r} has no delegated workflow to signal")
        self._workflow_port.signal(execution.delegated_execution_id, name, payload)

    def cancel(self, execution: AgentGoalExecution, snapshot: AgentGoalSnapshot) -> EngineOutcome:
        execution = self._advance(execution, AgentGoalTrigger.CANCEL)
        result = self._build_result(execution, partial_success=False, error="cancelled")
        return execution, result, self._snapshot(
            execution, plan=snapshot.plan, decisions=snapshot.decisions,
        )

    def recover(
        self, execution: AgentGoalExecution, snapshot: AgentGoalSnapshot, goal: AgentGoal,
        *, revision_context_request: ContextRequest | None = None,
    ) -> EngineOutcome:
        """Session/process continuity only — never restarts decomposition or
        delegation from `RECEIVED`. See this module's own docstring for why
        there is no dedicated `RECOVERING` FSM state to drive instead."""
        if execution.state is AgentGoalState.AWAITING_WORKFLOW:
            return self.poll(
                execution, snapshot, goal, revision_context_request=revision_context_request,
            )
        return execution, None, snapshot

    # ── internals ────────────────────────────────────────────────────────────
    def _revise_and_redelegate(
        self, execution: AgentGoalExecution, snapshot: AgentGoalSnapshot, goal: AgentGoal,
        *, context_request: ContextRequest,
    ) -> EngineOutcome:
        execution = self._advance(execution, AgentGoalTrigger.REVISE)
        revised_plan, revise_decision = revise_plan(
            snapshot.plan, goal, context_request=context_request, context_port=self._context_port,
            reasoning_port=self._reasoning_port, clock=self._clock,
        )
        execution = self._advance(execution, AgentGoalTrigger.DELEGATE)
        delegation = delegate_plan(revised_plan, workflow_port=self._workflow_port,
                                    clock=self._clock)
        delegated_id = next(iter(delegation.execution_ids.values()), None)
        execution = execution.with_delegation(
            delegated_id, revised_plan.plan_id, updated_at=self._clock(),
        )
        execution = self._advance(execution, AgentGoalTrigger.AWAIT)

        decisions = (*snapshot.decisions, revise_decision, *delegation.decisions)
        snapshot = self._snapshot(execution, plan=revised_plan, decisions=decisions)
        return execution, None, snapshot

    def _fallback_context_request(self, goal: AgentGoal) -> ContextRequest:
        return ContextRequest(session_id=goal.conversation_id, query=goal.description)

    def _advance(
        self, execution: AgentGoalExecution, trigger: AgentGoalTrigger,
    ) -> AgentGoalExecution:
        new_state = advance_goal_state(
            execution.state, trigger, machine_id=execution.execution_id, clock=self._clock,
        )
        return execution.with_state(new_state, updated_at=self._clock())

    def _build_result(
        self, execution: AgentGoalExecution, *, partial_success: bool, error: str | None = None,
    ) -> AgentGoalResult:
        return AgentGoalResult(
            execution_id=execution.execution_id, goal_id=execution.goal_id,
            final_state=execution.state, plan_id=execution.plan_id,
            delegated_execution_id=execution.delegated_execution_id,
            partial_success=partial_success, error=error, completed_at=execution.updated_at,
        )

    def _snapshot(
        self, execution: AgentGoalExecution, *, plan: AgentPlan | None,
        decisions: Sequence[AgentDecision], pending_error: str | None = None,
    ) -> AgentGoalSnapshot:
        return AgentGoalSnapshot(
            execution_id=execution.execution_id, state=execution.state, plan=plan,
            decisions=tuple(decisions), delegated_execution_id=execution.delegated_execution_id,
            pending_error=pending_error,
        )
