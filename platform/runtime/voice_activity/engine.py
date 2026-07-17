"""VoiceActivityEngine — the session-scoped owner of voice-activity state (M3.2).

Turns a provider's raw per-frame verdicts (`VoiceActivitySample`) into the
deterministic `VoiceActivityState` model, applying hysteresis (consecutive-frame
thresholds) so brief noise/gaps do not flip the state. It is the SOLE writer of
voice-activity state. It owns NO turns (the Turn Engine does), no conversation
state (the Orchestrator does), and never runs a provider (the coordinator calls
`provider.detect`, then feeds the sample here).

Framework only (M3.2): the model describes acoustic activity, not semantic or
adaptive endpointing (M3.3+). A single speech/silence frame can cascade through
`SPEECH_STARTING`→`SPEAKING` (or `SPEECH_ENDING`→`SILENCE_CONFIRMED`) when the
threshold is 1 — deterministic and each step is recorded/emitted.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from dataclasses import dataclass

from ..errors import InvalidVoiceActivityTransition
from .context import VoiceActivityContext
from .events import VoiceActivityEvent, VoiceActivityObserver
from .state import VA_TRANSITIONS, VoiceActivityState

log = logging.getLogger("voxvoice.runtime.voice_activity")


@dataclass(frozen=True, slots=True)
class VoiceActivityConfig:
    """Hysteresis thresholds (consecutive frames). Both are clamped to >= 1 by the
    engine. Defaults of 1 make a single frame confirm activity — richer providers
    that emit hard boundaries want 1; noisy raw detectors raise these."""

    speech_start_frames: int = 1  # speech frames to confirm SPEAKING
    silence_end_frames: int = 1  # silence frames to confirm end-of-utterance


class VoiceActivityEngine:
    def __init__(
        self,
        session_id: str,
        *,
        config: VoiceActivityConfig | None = None,
        on_event: VoiceActivityObserver | None = None,
        clock: Callable[[], float] | None = None,
    ) -> None:
        cfg = config or VoiceActivityConfig()
        self._start_frames = max(1, cfg.speech_start_frames)
        self._end_frames = max(1, cfg.silence_end_frames)
        self._on_event = on_event
        self._clock = clock or time.time
        self._context = VoiceActivityContext(session_id=session_id)
        self._speech_run = 0
        self._silence_run = 0
        # Record (and announce) the initial SILENCE state; not a transition.
        self._record(previous=None, state=self._context.state, confidence=1.0)

    # ── read-only exposure ──────────────────────────────────────────────────
    @property
    def context(self) -> VoiceActivityContext:
        return self._context

    @property
    def state(self) -> VoiceActivityState:
        return self._context.state

    # ── the deterministic activity model (sole writer of state) ─────────────
    def observe(self, sample) -> VoiceActivityState | None:
        """Feed one provider verdict. Returns the resulting state if this sample
        caused a net change, else None (sustained silence/speech)."""
        # SILENCE_CONFIRMED is momentary — normalize back to SILENCE before the
        # next cycle so the model is ready for the next utterance. This internal
        # reset is not a caller-visible change, so `entry` is captured after it.
        if self._context.state is VoiceActivityState.SILENCE_CONFIRMED:
            self._transition(VoiceActivityState.SILENCE, sample.confidence)
        entry = self._context.state
        if sample.speech:
            self._on_speech(sample.confidence)
        else:
            self._on_silence(sample.confidence)
        return self._context.state if self._context.state is not entry else None

    def _on_speech(self, confidence: float) -> None:
        state = self._context.state
        if state is VoiceActivityState.SILENCE:
            self._speech_run = 1
            self._silence_run = 0
            self._transition(VoiceActivityState.SPEECH_STARTING, confidence)
            if self._speech_run >= self._start_frames:
                self._transition(VoiceActivityState.SPEAKING, confidence)
        elif state is VoiceActivityState.SPEECH_STARTING:
            self._speech_run += 1
            if self._speech_run >= self._start_frames:
                self._transition(VoiceActivityState.SPEAKING, confidence)
        elif state is VoiceActivityState.SPEECH_ENDING:
            # Speech resumed before the end was confirmed.
            self._silence_run = 0
            self._transition(VoiceActivityState.SPEAKING, confidence)
        # SPEAKING → sustained speech, no transition.

    def _on_silence(self, confidence: float) -> None:
        state = self._context.state
        if state is VoiceActivityState.SPEECH_STARTING:
            # False start — never reached SPEAKING.
            self._speech_run = 0
            self._transition(VoiceActivityState.SILENCE, confidence)
        elif state is VoiceActivityState.SPEAKING:
            self._silence_run = 1
            self._speech_run = 0
            self._transition(VoiceActivityState.SPEECH_ENDING, confidence)
            if self._silence_run >= self._end_frames:
                self._transition(VoiceActivityState.SILENCE_CONFIRMED, confidence)
        elif state is VoiceActivityState.SPEECH_ENDING:
            self._silence_run += 1
            if self._silence_run >= self._end_frames:
                self._transition(VoiceActivityState.SILENCE_CONFIRMED, confidence)
        # SILENCE → sustained silence, no transition.

    def _transition(self, target: VoiceActivityState, confidence: float) -> None:
        """Advance to `target`. Raises on any illegal move — never silent. This is
        an internal guard: the model above only ever proposes legal edges, so a
        raise here means a coding error in the model, surfaced deterministically."""
        current = self._context.state
        if target not in VA_TRANSITIONS[current]:
            raise InvalidVoiceActivityTransition(
                f"illegal voice-activity transition {current.value} -> {target.value}"
            )
        self._context.state = target
        self._record(previous=current, state=target, confidence=confidence)

    def _record(
        self,
        *,
        previous: VoiceActivityState | None,
        state: VoiceActivityState,
        confidence: float,
    ) -> None:
        ts = self._clock()
        self._context.timing.record(state, ts)
        if self._on_event is None:
            return
        event = VoiceActivityEvent(
            session_id=self._context.session_id,
            previous=previous,
            state=state,
            confidence=confidence,
            ts=ts,
        )
        try:
            self._on_event(event)
        except Exception as exc:  # noqa: BLE001 — observability is best-effort
            log.warning("voice-activity observer raised (ignored): %s", exc)
