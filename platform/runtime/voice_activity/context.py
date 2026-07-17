"""VoiceActivityContext + VoiceActivityTiming (M3.2).

Per-session voice-activity data: identity, the current activity state, and the
most-recent entry timestamp of each state. Because the model is cyclic (many
utterances per session), timing records the *latest* entry into each state
(overwrite), not the first — the object a future endpoint/metrics subsystem reads
to reason about the current cycle. Timing is captured, never processed here.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .state import VoiceActivityState


@dataclass(slots=True)
class VoiceActivityTiming:
    """Latest entry timestamp per activity state (wall clock)."""

    entered_at: dict[VoiceActivityState, float] = field(default_factory=dict)

    def record(self, state: VoiceActivityState, ts: float) -> None:
        # Cyclic model → record the latest entry into a state (overwrite).
        self.entered_at[state] = ts

    @property
    def speech_started_at(self) -> float | None:
        return self.entered_at.get(VoiceActivityState.SPEECH_STARTING)

    @property
    def speaking_at(self) -> float | None:
        return self.entered_at.get(VoiceActivityState.SPEAKING)

    @property
    def speech_ending_at(self) -> float | None:
        return self.entered_at.get(VoiceActivityState.SPEECH_ENDING)

    @property
    def silence_confirmed_at(self) -> float | None:
        return self.entered_at.get(VoiceActivityState.SILENCE_CONFIRMED)


@dataclass(slots=True)
class VoiceActivityContext:
    """The activity state + timing for one session. `state` is advanced only by the
    VoiceActivityEngine (its sole writer)."""

    session_id: str
    state: VoiceActivityState = VoiceActivityState.SILENCE
    timing: VoiceActivityTiming = field(default_factory=VoiceActivityTiming)
