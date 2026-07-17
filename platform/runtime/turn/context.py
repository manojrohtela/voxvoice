"""TurnContext + TurnTiming (M3.1).

The per-turn data record: identity, current phase, and the timestamps of each
phase entry. Timing is captured but never *processed* here (no analytics — M3.x /
later). `TurnContext` is the object future subsystems read to reason about a turn.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .phase import TurnPhase


@dataclass(slots=True)
class TurnTiming:
    """Phase-entry timestamps for one turn (monotonic-agnostic wall clock). The
    raw map is the source of truth; the named properties expose the six timestamps
    the runtime cares about. Finer splits (e.g. speaking-start vs speaking-end)
    arrive with real streaming timing at M3.2+."""

    entered_at: dict[TurnPhase, float] = field(default_factory=dict)

    def record(self, phase: TurnPhase, ts: float) -> None:
        # Only the first entry into a phase is recorded (deterministic).
        self.entered_at.setdefault(phase, ts)

    @property
    def turn_started_at(self) -> float | None:
        return self.entered_at.get(TurnPhase.LISTENING) or self.entered_at.get(TurnPhase.IDLE)

    @property
    def first_audio_at(self) -> float | None:
        return self.entered_at.get(TurnPhase.USER_SPEAKING)

    @property
    def user_finished_at(self) -> float | None:
        return self.entered_at.get(TurnPhase.USER_FINISHED)

    @property
    def assistant_started_at(self) -> float | None:
        return self.entered_at.get(TurnPhase.ASSISTANT_THINKING)

    @property
    def assistant_speaking_at(self) -> float | None:
        return self.entered_at.get(TurnPhase.ASSISTANT_SPEAKING)

    @property
    def completed_at(self) -> float | None:
        return self.entered_at.get(TurnPhase.TURN_COMPLETED) or self.entered_at.get(
            TurnPhase.TURN_CANCELLED
        )


@dataclass(slots=True)
class TurnContext:
    """The state + timing of one turn. `phase` is advanced by the TurnLifecycle."""

    session_id: str
    turn_index: int
    phase: TurnPhase = TurnPhase.IDLE
    timing: TurnTiming = field(default_factory=TurnTiming)
