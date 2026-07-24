"""Agent lifecycle FSMs (M6.5B-1) — built on `core.fsm`.

**Two distinct FSMs, per the M6.5A design's own dual-FSM restoration
(§7.1):**

1. The **descriptor** lifecycle: DRAFT -> REGISTERED -> ACTIVE ->
   DEPRECATED -> RETIRED, direct RETIRE edges — identical shape to Tool's/
   Skill's/Hook's/Workflow's own descriptor lifecycle. Drives `AgentRegistry`.

2. The **goal-scoped execution** lifecycle (`AgentGoalState`) — restoring
   the dual-FSM shape a second time (after Workflow Runtime), but attached
   to `AgentGoal`, never to the Agent identity: RECEIVED -> DECOMPOSING ->
   DELEGATING -> AWAITING_WORKFLOW -> {REVISING -> DELEGATING (loop),
   COMPLETED, FAILED}, with CANCEL reachable from any non-terminal state to
   ABANDONED. **Structure only** — `advance_goal_state` operates on a bare
   `AgentGoalState` value (there is no stateful `AgentGoal` record with a
   `state` field in this bundle to advance); no engine, no planner, no
   execution calls this function anywhere in this bundle. Wiring it to a
   live, stateful goal record is explicit Bundle 3 scope (the
   `AgentExecutionEngine`).
"""

from __future__ import annotations

from collections.abc import Callable
from enum import StrEnum

from core.fsm import StateMachine, StateMachineDefinition

from .domain import AgentDescriptor, AgentGoalState, AgentLifecycleState


class AgentTrigger(StrEnum):
    REGISTER = "register"
    ACTIVATE = "activate"
    DEPRECATE = "deprecate"
    RETIRE = "retire"


def _agent_def() -> StateMachineDefinition[AgentLifecycleState, AgentTrigger]:
    s = AgentLifecycleState
    t = AgentTrigger
    d: StateMachineDefinition[AgentLifecycleState, AgentTrigger] = StateMachineDefinition()
    d.add(s.DRAFT, t.REGISTER, s.REGISTERED)
    d.add(s.REGISTERED, t.ACTIVATE, s.ACTIVE)
    d.add(s.ACTIVE, t.DEPRECATE, s.DEPRECATED)
    d.add(s.DEPRECATED, t.RETIRE, s.RETIRED)
    d.add(s.REGISTERED, t.RETIRE, s.RETIRED)
    d.add(s.ACTIVE, t.RETIRE, s.RETIRED)
    return d


AGENT_LIFECYCLE = _agent_def()


def advance_agent(
    descriptor: AgentDescriptor, trigger: AgentTrigger,
    *, clock: Callable[[], float] | None = None,
) -> AgentDescriptor:
    fsm: StateMachine[AgentLifecycleState, AgentTrigger] = StateMachine(
        AGENT_LIFECYCLE, descriptor.state, machine_id=descriptor.qualified_name, clock=clock,
    )
    return descriptor.with_state(fsm.fire(trigger))


class AgentGoalTrigger(StrEnum):
    DECOMPOSE = "decompose"
    DELEGATE = "delegate"
    AWAIT = "await"
    REVISE = "revise"
    COMPLETE = "complete"
    FAIL = "fail"
    CANCEL = "cancel"


def _agent_goal_def() -> StateMachineDefinition[AgentGoalState, AgentGoalTrigger]:
    s = AgentGoalState
    t = AgentGoalTrigger
    d: StateMachineDefinition[AgentGoalState, AgentGoalTrigger] = StateMachineDefinition()
    d.add(s.RECEIVED, t.DECOMPOSE, s.DECOMPOSING)
    d.add(s.DECOMPOSING, t.DELEGATE, s.DELEGATING)
    d.add(s.DELEGATING, t.AWAIT, s.AWAITING_WORKFLOW)
    d.add(s.AWAITING_WORKFLOW, t.REVISE, s.REVISING)
    d.add(s.REVISING, t.DELEGATE, s.DELEGATING)
    d.add(s.AWAITING_WORKFLOW, t.COMPLETE, s.COMPLETED)
    d.add(s.AWAITING_WORKFLOW, t.FAIL, s.FAILED)
    for src in (
        s.RECEIVED, s.DECOMPOSING, s.DELEGATING, s.AWAITING_WORKFLOW, s.REVISING,
    ):
        d.add(src, t.CANCEL, s.ABANDONED)
    return d


AGENT_GOAL_LIFECYCLE = _agent_goal_def()


def advance_goal_state(
    state: AgentGoalState, trigger: AgentGoalTrigger,
    *, machine_id: str = "", clock: Callable[[], float] | None = None,
) -> AgentGoalState:
    """Structure only (§7.1 of the M6.5A design) — advances a bare
    `AgentGoalState` value; no stateful goal record exists in this bundle to
    carry the result. Bundle 3's `AgentExecutionEngine` is the first real
    caller."""
    fsm: StateMachine[AgentGoalState, AgentGoalTrigger] = StateMachine(
        AGENT_GOAL_LIFECYCLE, state, machine_id=machine_id, clock=clock,
    )
    return fsm.fire(trigger)
