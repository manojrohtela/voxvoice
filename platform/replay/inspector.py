"""SessionInspector + RuntimeStateSnapshot (M4.3).

Reconstructs the runtime's *observable* state at any point in a recorded session by
folding the events up to a cursor — **deterministic and visualization-only** (it
executes no Runtime logic; it reads recorded facts). This powers the Runtime
Inspector: turn phase, voice activity, endpoint / interrupt / smart-turn decisions,
provider status, connection status — all as of a chosen event index.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import asdict, dataclass, field

from core.events import Event, EventType


@dataclass(slots=True)
class RuntimeStateSnapshot:
    at_index: int = 0
    at_ts: float | None = None
    connection: str = "idle"
    turn_phase: str = "idle"
    turn_index: int | None = None
    voice_activity: str = "silence"
    endpoint: str = "listening"
    interrupt: str = "idle"
    smart_turn: str | None = None
    providers: dict[str, str] = field(default_factory=dict)  # capability → status
    last_error: str | None = None

    def to_dict(self) -> dict:
        return asdict(self)


class SessionInspector:
    def state_at(self, events: Sequence[Event], index: int) -> RuntimeStateSnapshot:
        """Fold events[:index] into the observable runtime state at that cursor."""
        index = max(0, min(index, len(events)))
        s = RuntimeStateSnapshot(at_index=index)
        for e in events[:index]:
            s.at_ts = e.ts
            t = e.type
            if t is EventType.SESSION_STARTED:
                s.connection = "connected"
            elif t is EventType.SESSION_ENDED:
                s.connection = "ended"
            elif t is EventType.TURN_STARTED:
                s.turn_phase = "active"
                s.turn_index = e.turn
            elif t is EventType.ASSISTANT_STARTED_SPEAKING:
                s.turn_phase = "assistant_speaking"
            elif t is EventType.ASSISTANT_STOPPED_SPEAKING:
                s.turn_phase = "active"
            elif t is EventType.TURN_COMPLETED:
                s.turn_phase = "completed"
            elif t is EventType.TURN_CANCELLED:
                s.turn_phase = "cancelled"
            elif t is EventType.SPEECH_STARTED:
                s.voice_activity = "speaking"
            elif t is EventType.SPEECH_STOPPED:
                s.voice_activity = "silence"
            elif t is EventType.ENDPOINT_DETECTED:
                s.endpoint = "detected"
            elif t is EventType.ENDPOINT_CANCELLED:
                s.endpoint = "cancelled"
            elif t is EventType.INTERRUPT_DETECTED:
                s.interrupt = "potential"
            elif t is EventType.INTERRUPT_CONFIRMED:
                s.interrupt = "confirmed"
            elif t is EventType.INTERRUPT_CANCELLED:
                s.interrupt = "cancelled"
            elif t is EventType.SMART_TURN_DECISION:
                s.smart_turn = e.data.get("decision")
            elif t is EventType.PROVIDER_CONNECTED:
                s.providers[e.data.get("capability", "?")] = "connected"
            elif t is EventType.PROVIDER_DISCONNECTED:
                s.providers[e.data.get("capability", "?")] = "disconnected"
            elif t is EventType.RUNTIME_ERROR:
                s.last_error = e.data.get("reason")
        return s
