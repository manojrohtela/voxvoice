"""Internal turn events (M3.1).

Turn-lifecycle notifications that later milestones consume (VAD, endpoint
detection, the interrupt engine, metrics). This is NOT the Event Bus (M4) — a
synchronous, best-effort observer hook that a bridge can later forward into
`core.events`. It is a clean extension point, nothing more.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from .phase import TurnPhase


@dataclass(frozen=True, slots=True)
class TurnEvent:
    """One turn-lifecycle transition, delivered to observers."""

    session_id: str
    turn_index: int
    previous: TurnPhase | None  # None only for the initial IDLE record
    phase: TurnPhase
    ts: float


# A turn observer receives one TurnEvent. It must never raise into the engine.
TurnObserver = Callable[[TurnEvent], None]
