"""Hook lifecycle (M6.3B) — built on `core.fsm`.

Exactly ONE deterministic state machine: DRAFT -> REGISTERED -> ACTIVE ->
DEPRECATED -> RETIRED, with direct RETIRE edges from REGISTERED and ACTIVE —
identical shape to Tool's and Skill's own descriptor lifecycle. There is no
second, execution-lifecycle FSM here — a hook never executes a multi-phase
action, it only returns one verdict per event.
"""

from __future__ import annotations

from collections.abc import Callable
from enum import StrEnum

from core.fsm import StateMachine, StateMachineDefinition

from .domain import HookDescriptor, HookLifecycleState


class HookTrigger(StrEnum):
    REGISTER = "register"
    ACTIVATE = "activate"
    DEPRECATE = "deprecate"
    RETIRE = "retire"


def _hook_def() -> StateMachineDefinition[HookLifecycleState, HookTrigger]:
    d: StateMachineDefinition[HookLifecycleState, HookTrigger] = StateMachineDefinition()
    d.add(HookLifecycleState.DRAFT, HookTrigger.REGISTER, HookLifecycleState.REGISTERED)
    d.add(HookLifecycleState.REGISTERED, HookTrigger.ACTIVATE, HookLifecycleState.ACTIVE)
    d.add(HookLifecycleState.ACTIVE, HookTrigger.DEPRECATE, HookLifecycleState.DEPRECATED)
    d.add(HookLifecycleState.DEPRECATED, HookTrigger.RETIRE, HookLifecycleState.RETIRED)
    d.add(HookLifecycleState.REGISTERED, HookTrigger.RETIRE, HookLifecycleState.RETIRED)
    d.add(HookLifecycleState.ACTIVE, HookTrigger.RETIRE, HookLifecycleState.RETIRED)
    return d


HOOK_LIFECYCLE = _hook_def()


def advance_hook(
    descriptor: HookDescriptor, trigger: HookTrigger,
    *, clock: Callable[[], float] | None = None,
) -> HookDescriptor:
    fsm: StateMachine[HookLifecycleState, HookTrigger] = StateMachine(
        HOOK_LIFECYCLE, descriptor.state, machine_id=descriptor.qualified_name, clock=clock,
    )
    return descriptor.with_state(fsm.fire(trigger))
