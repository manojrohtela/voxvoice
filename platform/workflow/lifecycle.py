"""Workflow descriptor lifecycle (M6.4B-1) — built on `core.fsm`.

The descriptor lifecycle only: DRAFT -> REGISTERED -> ACTIVE -> DEPRECATED ->
RETIRED, direct RETIRE edges from REGISTERED and ACTIVE — identical shape to
Tool's/Skill's/Hook's own descriptor lifecycle. Per the M6.4A design (§6.1),
Workflow Runtime will eventually restore Tool Runtime's dual-FSM shape with a
second, execution lifecycle -- but that FSM is explicit Bundle 2 scope
("no execution" is an explicit M6.4B-1 constraint) and is not defined here.
"""

from __future__ import annotations

from collections.abc import Callable
from enum import StrEnum

from core.fsm import StateMachine, StateMachineDefinition

from .domain import WorkflowDescriptor, WorkflowLifecycleState


class WorkflowTrigger(StrEnum):
    REGISTER = "register"
    ACTIVATE = "activate"
    DEPRECATE = "deprecate"
    RETIRE = "retire"


def _workflow_def() -> StateMachineDefinition[WorkflowLifecycleState, WorkflowTrigger]:
    s = WorkflowLifecycleState
    t = WorkflowTrigger
    d: StateMachineDefinition[WorkflowLifecycleState, WorkflowTrigger] = StateMachineDefinition()
    d.add(s.DRAFT, t.REGISTER, s.REGISTERED)
    d.add(s.REGISTERED, t.ACTIVATE, s.ACTIVE)
    d.add(s.ACTIVE, t.DEPRECATE, s.DEPRECATED)
    d.add(s.DEPRECATED, t.RETIRE, s.RETIRED)
    d.add(s.REGISTERED, t.RETIRE, s.RETIRED)
    d.add(s.ACTIVE, t.RETIRE, s.RETIRED)
    return d


WORKFLOW_LIFECYCLE = _workflow_def()


def advance_workflow(
    descriptor: WorkflowDescriptor, trigger: WorkflowTrigger,
    *, clock: Callable[[], float] | None = None,
) -> WorkflowDescriptor:
    fsm: StateMachine[WorkflowLifecycleState, WorkflowTrigger] = StateMachine(
        WORKFLOW_LIFECYCLE, descriptor.state, machine_id=descriptor.qualified_name, clock=clock,
    )
    return descriptor.with_state(fsm.fire(trigger))
