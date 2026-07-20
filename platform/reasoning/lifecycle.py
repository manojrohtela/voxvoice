"""Reasoning session lifecycle (M5.5B-1) — built on `core.fsm`.

One deterministic state machine for the inference lifecycle, dogfooding the shared FSM
framework (no hand-written transition logic). Illegal moves raise typed `FSMError`s.

REQUESTED → STARTED → STREAMING → COMPLETED
                    ↘ (STARTED/STREAMING) → TOOL_WAIT → STREAMING   (tool loop, M5.5B-2)
Any active state → FAILED / CANCELLED; COMPLETED → EXPIRED.

The core drives REQUESTED→STARTED→STREAMING→COMPLETED (+ FAILED/EXPIRED); TOOL_WAIT +
CANCELLED transitions are defined now (the FSM is complete) but exercised by the M5.5B-2
tool/cancellation engines.
"""

from __future__ import annotations

from collections.abc import Callable
from enum import StrEnum

from core.fsm import StateMachine, StateMachineDefinition

from .domain import InferenceState, ResponsePackage


class InferenceTrigger(StrEnum):
    START = "start"
    STREAM = "stream"
    TOOL_WAIT = "tool_wait"
    RESUME = "resume"
    COMPLETE = "complete"
    FAIL = "fail"
    CANCEL = "cancel"
    EXPIRE = "expire"


def _def() -> StateMachineDefinition[InferenceState, InferenceTrigger]:
    d: StateMachineDefinition[InferenceState, InferenceTrigger] = StateMachineDefinition()
    d.add(InferenceState.REQUESTED, InferenceTrigger.START, InferenceState.STARTED)
    d.add(InferenceState.STARTED, InferenceTrigger.STREAM, InferenceState.STREAMING)
    d.add(InferenceState.STREAMING, InferenceTrigger.COMPLETE, InferenceState.COMPLETED)
    # tool loop (M5.5B-2)
    d.add(InferenceState.STREAMING, InferenceTrigger.TOOL_WAIT, InferenceState.TOOL_WAIT)
    d.add(InferenceState.STARTED, InferenceTrigger.TOOL_WAIT, InferenceState.TOOL_WAIT)
    d.add(InferenceState.TOOL_WAIT, InferenceTrigger.RESUME, InferenceState.STREAMING)
    # single-shot (no streaming) completes directly from STARTED
    d.add(InferenceState.STARTED, InferenceTrigger.COMPLETE, InferenceState.COMPLETED)
    # failure / cancellation from any active state
    for st in (InferenceState.REQUESTED, InferenceState.STARTED, InferenceState.STREAMING,
               InferenceState.TOOL_WAIT):
        d.add(st, InferenceTrigger.FAIL, InferenceState.FAILED)
        d.add(st, InferenceTrigger.CANCEL, InferenceState.CANCELLED)
    d.add(InferenceState.COMPLETED, InferenceTrigger.EXPIRE, InferenceState.EXPIRED)
    return d


REASONING_LIFECYCLE = _def()


def advance(
    package: ResponsePackage, trigger: InferenceTrigger, *, clock: Callable[[], float] | None = None
) -> ResponsePackage:
    """Validate `trigger` against the package's state; return it in the new state."""
    fsm: StateMachine[InferenceState, InferenceTrigger] = StateMachine(
        REASONING_LIFECYCLE, package.state, machine_id=package.id, clock=clock
    )
    return package.with_state(fsm.fire(trigger))
