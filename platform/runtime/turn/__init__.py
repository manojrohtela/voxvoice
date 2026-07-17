"""runtime.turn — Turn Engine (Turn Intelligence foundation; Constitution §A4.2).

The turn-lifecycle subsystem. `TurnEngine` owns turn state for a session;
`TurnCoordinator` is the Runtime Pipeline's integration point; `TurnLifecycle` is
one turn's deterministic state machine over `TurnPhase`; `TurnContext`/`TurnTiming`
carry per-turn data + phase-entry timestamps; `TurnEvent` is the internal
notification extension point (not the Event Bus).

M3.1 is the framework only — no VAD, endpoint detection, or interruptions (those
plug in at M3.2+). `TurnPhase` is a runtime-internal, per-turn lifecycle distinct
from the Constitution's finer `core.states.TurnState` (reconciliation pending,
DL-0012).
"""

from .context import TurnContext, TurnTiming
from .coordinator import TurnCoordinator
from .engine import TurnEngine
from .events import TurnEvent, TurnObserver
from .lifecycle import TurnLifecycle
from .phase import TERMINAL_TURN_PHASES, TURN_TRANSITIONS, TurnPhase

__all__ = [
    "TurnEngine",
    "TurnCoordinator",
    "TurnLifecycle",
    "TurnContext",
    "TurnTiming",
    "TurnEvent",
    "TurnObserver",
    "TurnPhase",
    "TURN_TRANSITIONS",
    "TERMINAL_TURN_PHASES",
]
