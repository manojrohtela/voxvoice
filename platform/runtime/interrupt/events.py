"""Internal interrupt events (M3.4).

Interrupt-decision notifications that later milestones consume (metrics, the event
bus, coaching). This is NOT the Event Bus (M4) — a synchronous, best-effort
observer hook that a bridge can later forward into `core.events`. A clean extension
point, nothing more.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from .decision import InterruptDecision


@dataclass(frozen=True, slots=True)
class InterruptEvent:
    """One interrupt-decision transition, delivered to observers."""

    session_id: str
    previous: InterruptDecision | None  # None only for the initial IDLE record
    decision: InterruptDecision
    confidence: float
    ts: float


# An interrupt observer receives one event. It must never raise into the engine.
InterruptObserver = Callable[[InterruptEvent], None]
