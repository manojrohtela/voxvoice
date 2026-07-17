"""Internal endpoint events (M3.3).

Endpoint-decision notifications that later milestones consume (metrics, the
interrupt engine, coaching) and that the coordinator uses to notify the Turn
Engine. This is NOT the Event Bus (M4) — a synchronous, best-effort observer hook
that a bridge can later forward into `core.events`. A clean extension point,
nothing more.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from .decision import EndpointDecision


@dataclass(frozen=True, slots=True)
class EndpointEvent:
    """One endpoint-decision transition, delivered to observers."""

    session_id: str
    turn_index: int
    previous: EndpointDecision | None  # None only for the initial CONTINUE_LISTENING record
    decision: EndpointDecision
    confidence: float
    ts: float


# An endpoint observer receives one event. It must never raise into the engine.
EndpointObserver = Callable[[EndpointEvent], None]
