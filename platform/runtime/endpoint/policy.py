"""EndpointPolicy — the configurable rules that shape endpoint decisions (M3.3).

Nothing about endpointing is hardcoded (Constitution: no silent config; AR-… no
magic thresholds). Every knob lives here and is injected into the `EndpointEngine`.
Durations are expressed as *observation frames* (deterministic, unit-testable);
one frame maps to one provider observation (an STT partial/segment or a Voice
Activity sample). A real streaming profile binds a frame to a wall-clock ms via
the provider's frame size — kept out of the deterministic core on purpose.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class EndpointPolicy:
    """Rules for when a caller's turn is 'truly complete'. All knobs configurable.

    Defaults reproduce the pre-M3.3 behaviour of the local profile: a completed
    transcript segment (an STT Final) immediately confirms the turn — no silence
    hold, no minimum-silence, no timeout — so conversations behave exactly as
    before while the *decision* now flows through this framework.
    """

    #: M11.8 (ADR-0047 ME1): the turn is ended by **audio** (a detected end-of-speech), not by a
    #: transcript segment. When True, `note_segment` records speech evidence but never confirms —
    #: otherwise an STT Final and the audio boundary would each finish the same turn, which is the
    #: duplicate `USER_FINISHED` this flag exists to prevent. Default False keeps the M3.3
    #: segment-driven behaviour of every existing profile exactly as it was.
    audio_driven: bool = False

    min_speech_frames: int = 1  # minimum speech observations before an endpoint may confirm
    min_silence_frames: int = 0  # minimum silence observations required to confirm via silence
    silence_hold_frames: int = 0  # silence to hold after a potential endpoint before confirming
    confidence_threshold: float = 0.0  # minimum confidence to accept an endpoint signal
    max_turn_frames: int = 0  # 0 disables; else force TIMEOUT_ENDPOINT after this many frames

    @property
    def confirms_on_segment(self) -> bool:
        """True when a completed transcript segment can confirm immediately (no
        silence hold / minimum-silence gate) — the default local behaviour."""
        if self.audio_driven:
            return False
        return self.silence_hold_frames <= 0 and self.min_silence_frames <= 0
