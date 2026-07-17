"""Internal Smart Turn events (M3.5).

Decision notifications later milestones consume (metrics, the event bus, coaching).
NOT the Event Bus (M4) — a synchronous, best-effort observer hook a bridge can
later forward into `core.events`. A clean extension point, nothing more.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from .decision import SmartTurnDecision


@dataclass(frozen=True, slots=True)
class SmartTurnEvent:
    """One committed Smart Turn decision, delivered to observers."""

    session_id: str
    previous: SmartTurnDecision | None
    decision: SmartTurnDecision
    confidence: float
    ts: float


# A Smart Turn observer receives one event. It must never raise into the engine.
SmartTurnObserver = Callable[[SmartTurnEvent], None]
