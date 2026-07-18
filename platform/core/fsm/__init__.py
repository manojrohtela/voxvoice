"""core.fsm — the reusable deterministic state-machine framework (M5.0).

Every runtime module's lifecycle / decision machine is built on this instead of a
hand-written transition table. Deterministic, validated, observable, replayable, and
async-ready. Owns FSM mechanics only — no domain logic.
"""

from .machine import (
    DuplicateTransition,
    FSMError,
    GuardRejected,
    InvalidTransition,
    StateMachine,
    StateMachineDefinition,
    TerminalStateError,
    Transition,
    TransitionEvent,
    TransitionObserver,
    TransitionRecord,
    define,
)

__all__ = [
    "StateMachine",
    "StateMachineDefinition",
    "Transition",
    "TransitionRecord",
    "TransitionEvent",
    "TransitionObserver",
    "define",
    "FSMError",
    "InvalidTransition",
    "GuardRejected",
    "TerminalStateError",
    "DuplicateTransition",
]
