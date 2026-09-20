"""Audio-driven endpoint detection — the M11.8 half of ADR-0047 **ME1**.

**VAD is speech detection; endpointing decides when a speech turn ends.** M11.7 made the first
statement real (a provider reports, per frame, whether speech is present). This module makes the
second one real: it accumulates those verdicts over *audio duration* and decides, deterministically,
when an utterance began and when it ended.

## Why this is a separate thing from the VAD provider

A provider answers one question about one window. Endpointing is a temporal policy over many
windows — *how much* speech before an utterance has begun, *how much* silence before it has ended —
and that policy belongs to the runtime, identically for every provider (AR-21). Nothing here knows
what a model is: the input is `speech: bool` plus the frame's duration in milliseconds.

## The rule this exists to enforce

**One frame of silence is not the end of a turn.** Real speech contains stops, plosives and VAD
false negatives. An end-of-utterance is declared only after `trailing_silence_ms` of *continuous*
silence, and an utterance begins only after `min_speech_ms` of speech evidence, so a single stray
frame in either direction changes nothing.

## Timing is audio time, never wall clock

Every threshold is accumulated from the duration each frame *carries*, so behaviour is identical
whether audio arrives in real time, in a burst, or in a test that feeds ten frames synchronously.
Nothing here sleeps, reads a clock, or awaits.

Because the thresholds are crossed *after* the fact, both boundaries are reported with the
`backdate_ms` that recovers the real instant: an utterance began `speech_ms` ago when its start is
declared, and ended `silence_ms` ago when its end is declared. The caller uses that to stamp
`speech_start` / `speech_end` at the moment the audio says they happened rather than the moment the
policy noticed — which is what makes `voice_to_voice_ms` a truthful measurement (ADR-0047 ME4).

## What it does not do

It owns no turn (the Turn Engine does), makes no endpoint *decision* record (the `EndpointEngine`
does), calls no provider, starts no task, and never invokes an LLM or TTS. It is a pure state
machine over booleans and durations: O(1) state, no buffering of audio, no queue.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from core.audio import AudioEncoding, AudioFrame


class AudioEndpointState(StrEnum):
    """Where the detector is within one utterance cycle."""

    IDLE = "idle"  # no speech evidence yet (silence before first speech)
    SPEECH_PENDING = "speech_pending"  # speech seen, not yet enough to declare a start
    SPEAKING = "speaking"  # utterance in progress (continued speech)
    TRAILING_SILENCE = "trailing_silence"  # silence after speech, hold not yet satisfied


#: Legal detector transitions. Cyclic — one cycle per utterance, no terminal state.
AUDIO_ENDPOINT_TRANSITIONS: dict[AudioEndpointState, frozenset[AudioEndpointState]] = {
    AudioEndpointState.IDLE: frozenset({AudioEndpointState.SPEECH_PENDING}),
    AudioEndpointState.SPEECH_PENDING: frozenset(
        {AudioEndpointState.SPEAKING, AudioEndpointState.IDLE}
    ),
    # SPEAKING → IDLE is the `max_utterance_ms` guard closing a turn that never stopped talking:
    # it ends the utterance without any trailing silence, which is the one path to an end that
    # does not pass through the hold.
    AudioEndpointState.SPEAKING: frozenset(
        {AudioEndpointState.TRAILING_SILENCE, AudioEndpointState.IDLE}
    ),
    AudioEndpointState.TRAILING_SILENCE: frozenset(
        {AudioEndpointState.SPEAKING, AudioEndpointState.IDLE}
    ),
}


class AudioEndpointKind(StrEnum):
    """What one observation produced."""

    NONE = "none"  # nothing crossed a threshold
    SPEECH_START = "speech_start"  # an utterance began
    SPEECH_END = "speech_end"  # an utterance ended (trailing silence satisfied)


@dataclass(frozen=True, slots=True)
class AudioEndpointSignal:
    """One detector output. `backdate_ms` is how far in the *past* the boundary really was."""

    kind: AudioEndpointKind
    backdate_ms: float = 0.0

    def __bool__(self) -> bool:
        return self.kind is not AudioEndpointKind.NONE


NO_SIGNAL = AudioEndpointSignal(AudioEndpointKind.NONE)


@dataclass(frozen=True, slots=True)
class AudioEndpointPolicy:
    """Endpointing thresholds, in **milliseconds of audio**.

    These are **implementation defaults pending evaluation**, not tuned production values, and no
    accuracy or latency claim is attached to them (M11.13 owns measurement):

    * `min_speech_ms = 120` — about six 20 ms transport frames, or four 32 ms Silero windows.
      Long enough that a click, a cough onset or one false-positive window cannot open a turn.
    * `trailing_silence_ms = 240` — the end-of-utterance hold. Chosen to sit inside Constitution
      §A8's *"endpoint decision ≤ 250 ms after true end-of-speech"* budget, which is the only
      number the repository actually states. Conversational systems commonly hold longer
      (0.5–0.8 s) to tolerate mid-sentence pauses; raising it would trade §A8 compliance for
      fewer premature endpoints, and that trade needs data this milestone does not have (R-49).
    * `max_utterance_ms = 30_000` — a guard, not a policy: a turn that never ends is closed so a
      session cannot be held open forever. `0` disables it.

    A pre-roll is deliberately **absent**: nothing gates audio on the endpoint decision, so no
    speech is lost while evidence accrues (see the module docs of `runtime/pipeline`).
    """

    min_speech_ms: float = 120.0
    trailing_silence_ms: float = 240.0
    max_utterance_ms: float = 30_000.0

    def __post_init__(self) -> None:
        # AR-25 in miniature: an impossible value is refused here rather than silently coerced.
        if self.min_speech_ms <= 0:
            raise ValueError(f"min_speech_ms must be positive, got {self.min_speech_ms}")
        if self.trailing_silence_ms <= 0:
            raise ValueError(
                f"trailing_silence_ms must be positive, got {self.trailing_silence_ms}"
            )
        if self.max_utterance_ms < 0:
            raise ValueError(f"max_utterance_ms must be >= 0, got {self.max_utterance_ms}")


def frame_duration_ms(frame: AudioFrame, *, fallback_ms: float) -> float:
    """How much audio time one frame carries.

    Computed from the frame itself when it declares a real format (`PCM16`, mono or multi-channel):
    bytes ÷ (2 × channels × rate). An `OPAQUE` frame declares no format — the local profile's
    text-carrying frames are the standing example — so its duration is not derivable and the
    caller's `fallback_ms` stands in. No resampling, no reinterpretation, no mutation of the frame
    (AR-41: the `AudioNormalizer` remains the only component that converts audio).
    """
    if frame.encoding is AudioEncoding.PCM16 and frame.sample_rate > 0 and frame.channels > 0:
        return len(frame.data) / (2 * frame.channels) / frame.sample_rate * 1000
    return fallback_ms


class AudioEndpointDetector:
    """Turns per-frame speech verdicts into `speech_start` / `speech_end` boundaries.

    Deterministic and side-effect free: one observation in, one signal out. The caller decides what
    a boundary *means* (tap a metric, notify the Turn Engine); this only decides *when*.
    """

    __slots__ = ("_policy", "_state", "_speech_ms", "_silence_ms", "_utterance_ms", "_utterances")

    def __init__(self, policy: AudioEndpointPolicy | None = None) -> None:
        self._policy = policy or AudioEndpointPolicy()
        self._state = AudioEndpointState.IDLE
        #: Contiguous speech accrued toward a start (reset by silence while pending).
        self._speech_ms = 0.0
        #: Contiguous silence accrued toward an end (reset by speech while speaking).
        self._silence_ms = 0.0
        #: Audio time since this utterance began — the `max_utterance_ms` guard's input.
        self._utterance_ms = 0.0
        self._utterances = 0

    # ── read-only exposure ───────────────────────────────────────────────────

    @property
    def policy(self) -> AudioEndpointPolicy:
        return self._policy

    @property
    def state(self) -> AudioEndpointState:
        return self._state

    @property
    def in_speech(self) -> bool:
        """Whether an utterance is open (a start was declared and no end has been)."""
        return self._state in (AudioEndpointState.SPEAKING, AudioEndpointState.TRAILING_SILENCE)

    @property
    def utterances(self) -> int:
        """Completed utterances — one per `SPEECH_END`."""
        return self._utterances

    @property
    def speech_ms(self) -> float:
        return self._speech_ms

    @property
    def silence_ms(self) -> float:
        return self._silence_ms

    # ── the deterministic model ──────────────────────────────────────────────

    def observe(self, *, speech: bool, duration_ms: float) -> AudioEndpointSignal:
        """Account for one frame of audio the provider classified. Never raises."""
        if duration_ms < 0:
            raise ValueError(f"duration_ms must be >= 0, got {duration_ms}")
        if self.in_speech:
            self._utterance_ms += duration_ms
        return self._on_speech(duration_ms) if speech else self._on_silence(duration_ms)

    def _on_speech(self, duration_ms: float) -> AudioEndpointSignal:
        self._silence_ms = 0.0  # continuity broken: a gap that did not reach the hold is forgotten
        if self._state is AudioEndpointState.TRAILING_SILENCE:
            # Speech resumed inside the hold — the same utterance continues. This is the case that
            # makes a brief gap harmless, and the reason one silent frame cannot end a turn.
            self._transition(AudioEndpointState.SPEAKING)
            return NO_SIGNAL
        if self._state is AudioEndpointState.SPEAKING:
            return self._guard_max_utterance()
        # IDLE or SPEECH_PENDING: accrue evidence toward a start.
        if self._state is AudioEndpointState.IDLE:
            self._transition(AudioEndpointState.SPEECH_PENDING)
        self._speech_ms += duration_ms
        if self._speech_ms < self._policy.min_speech_ms:
            return NO_SIGNAL
        self._transition(AudioEndpointState.SPEAKING)
        started_ms_ago = self._speech_ms
        self._utterance_ms = self._speech_ms
        self._speech_ms = 0.0
        return AudioEndpointSignal(AudioEndpointKind.SPEECH_START, backdate_ms=started_ms_ago)

    def _on_silence(self, duration_ms: float) -> AudioEndpointSignal:
        if self._state is AudioEndpointState.SPEECH_PENDING:
            # Not enough speech to call it an utterance, and it stopped — forget it entirely.
            self._speech_ms = 0.0
            self._transition(AudioEndpointState.IDLE)
            return NO_SIGNAL
        if self._state is AudioEndpointState.IDLE:
            return NO_SIGNAL  # silence before any speech is not an event
        if self._state is AudioEndpointState.SPEAKING:
            self._transition(AudioEndpointState.TRAILING_SILENCE)
            self._silence_ms = 0.0
        self._silence_ms += duration_ms
        if self._silence_ms >= self._policy.trailing_silence_ms:
            return self._end_utterance(backdate_ms=self._silence_ms)
        return self._guard_max_utterance()

    def _guard_max_utterance(self) -> AudioEndpointSignal:
        if 0 < self._policy.max_utterance_ms <= self._utterance_ms:
            # The guard closes the turn *now*, so the boundary is now: nothing is backdated,
            # because no silence established an earlier true end.
            return self._end_utterance(backdate_ms=0.0)
        return NO_SIGNAL

    def _end_utterance(self, *, backdate_ms: float) -> AudioEndpointSignal:
        self._transition(AudioEndpointState.IDLE)
        self._speech_ms = 0.0
        self._silence_ms = 0.0
        self._utterance_ms = 0.0
        self._utterances += 1
        return AudioEndpointSignal(AudioEndpointKind.SPEECH_END, backdate_ms=backdate_ms)

    # ── stream lifecycle ─────────────────────────────────────────────────────

    def flush(self) -> AudioEndpointSignal:
        """The audio stream ended (caller hung up) while an utterance was open.

        Closes it exactly once so the last utterance is not silently dropped — the same
        close-out rule `EndpointEngine.flush()` applies to a pending decision. A no-op when no
        utterance is open. The trailing silence already accrued is backdated, so a hangup does not
        inflate the measured end-of-speech.
        """
        if not self.in_speech:
            return NO_SIGNAL
        return self._end_utterance(backdate_ms=self._silence_ms)

    def note_unavailable(self) -> AudioEndpointSignal:
        """The voice-activity provider failed for this frame (AR-24, ADR-0047 ME8).

        **A failed provider is not silence.** Advancing the trailing-silence hold on a frame that
        was never classified would end turns out of a provider outage, and advancing the speech
        accrual would invent an utterance. So no counter moves and no boundary is produced: the
        detector holds its state until real verdicts resume, and the runtime records the
        degradation through the existing auxiliary path.
        """
        return NO_SIGNAL

    def reset(self) -> None:
        """Abandon any in-progress utterance (cancellation, shutdown, barge-in).

        Deliberately emits nothing: a boundary the audio did not produce must not appear in a
        metric. State returns to `IDLE` directly, which is why this is not routed through the
        transition table — it is a discard, not a move within a cycle.
        """
        self._state = AudioEndpointState.IDLE
        self._speech_ms = 0.0
        self._silence_ms = 0.0
        self._utterance_ms = 0.0

    # ── internals ────────────────────────────────────────────────────────────

    def _transition(self, target: AudioEndpointState) -> None:
        if target not in AUDIO_ENDPOINT_TRANSITIONS[self._state]:
            raise AssertionError(  # pragma: no cover — the model proposes only legal edges
                f"illegal audio endpoint transition {self._state.value} -> {target.value}"
            )
        self._state = target


__all__ = [
    "AUDIO_ENDPOINT_TRANSITIONS",
    "NO_SIGNAL",
    "AudioEndpointDetector",
    "AudioEndpointKind",
    "AudioEndpointPolicy",
    "AudioEndpointSignal",
    "AudioEndpointState",
    "frame_duration_ms",
]
