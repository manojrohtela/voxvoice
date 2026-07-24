"""Skill lifecycle (M6.2B-1) — built on `core.fsm`.

Exactly ONE deterministic state machine, dogfooding the shared FSM framework
(no hand-written transition logic): DRAFT -> REGISTERED -> ACTIVE ->
DEPRECATED -> RETIRED, with direct RETIRE edges from REGISTERED and ACTIVE (a
skill may be withdrawn before or after activation without an intermediate
hop) — identical shape to `tool.lifecycle`'s descriptor FSM (§10 of the
M6.2A design). Illegal moves raise typed `FSMError`s.

There is no second, execution-lifecycle FSM here (unlike Tool Runtime's dual
FSM) — a Skill never executes, so there is nothing for a second FSM to track.
This is a deliberate, documented asymmetry, not an omission.
"""

from __future__ import annotations

from collections.abc import Callable
from enum import StrEnum

from core.fsm import StateMachine, StateMachineDefinition

from .domain import SkillDescriptor, SkillLifecycleState


class SkillTrigger(StrEnum):
    REGISTER = "register"
    ACTIVATE = "activate"
    DEPRECATE = "deprecate"
    RETIRE = "retire"


def _skill_def() -> StateMachineDefinition[SkillLifecycleState, SkillTrigger]:
    d: StateMachineDefinition[SkillLifecycleState, SkillTrigger] = StateMachineDefinition()
    d.add(SkillLifecycleState.DRAFT, SkillTrigger.REGISTER, SkillLifecycleState.REGISTERED)
    d.add(SkillLifecycleState.REGISTERED, SkillTrigger.ACTIVATE, SkillLifecycleState.ACTIVE)
    d.add(SkillLifecycleState.ACTIVE, SkillTrigger.DEPRECATE, SkillLifecycleState.DEPRECATED)
    d.add(SkillLifecycleState.DEPRECATED, SkillTrigger.RETIRE, SkillLifecycleState.RETIRED)
    d.add(SkillLifecycleState.REGISTERED, SkillTrigger.RETIRE, SkillLifecycleState.RETIRED)
    d.add(SkillLifecycleState.ACTIVE, SkillTrigger.RETIRE, SkillLifecycleState.RETIRED)
    return d


SKILL_LIFECYCLE = _skill_def()


def advance_skill(
    descriptor: SkillDescriptor, trigger: SkillTrigger,
    *, clock: Callable[[], float] | None = None,
) -> SkillDescriptor:
    """Validate `trigger` against the descriptor's state; return it in the new state."""
    fsm: StateMachine[SkillLifecycleState, SkillTrigger] = StateMachine(
        SKILL_LIFECYCLE, descriptor.state, machine_id=descriptor.qualified_name, clock=clock,
    )
    return descriptor.with_state(fsm.fire(trigger))
