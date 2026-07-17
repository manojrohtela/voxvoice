"""Internal voice-activity events (M3.2).

Activity-state notifications that later milestones consume (endpoint detection,
the interrupt engine, metrics) and that the coordinator forwards to the Turn
Engine. This is NOT the Event Bus (M4) — a synchronous, best-effort observer
hook, forwardable into `core.events` later. A clean extension point, nothing more.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from .state import VoiceActivityState


@dataclass(frozen=True, slots=True)
class VoiceActivityEvent:
    """One voice-activity transition, delivered to observers."""

    session_id: str
    previous: VoiceActivityState | None  # None only for the initial SILENCE record
    state: VoiceActivityState
    confidence: float
    ts: float


# A voice-activity observer receives one event. It must never raise into the engine.
VoiceActivityObserver = Callable[[VoiceActivityEvent], None]
