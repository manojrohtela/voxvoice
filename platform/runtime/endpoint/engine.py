"""EndpointEngine — the session-scoped owner of endpoint decisions (M3.3).

Turns provider-neutral signals — speech observations, completed transcript
segments, and (optional) Voice Activity silence — into the deterministic
`EndpointDecision` model, governed entirely by a configurable `EndpointPolicy`
(nothing hardcoded). It is the SOLE writer of endpoint-decision state. It owns NO
turns (the Turn Engine does), no conversation state (the Orchestrator does), no
voice-activity state (the VoiceActivityEngine does), and it never runs a provider
or touches speech/audio.

Inputs are neutral signals, not provider objects (Constitution §A6): `note_speech`
(an STT partial or a VAD `SPEAKING`), `note_segment` (a completed transcript
segment — an STT Final), `note_silence` (a VAD `SILENCE_CONFIRMED`). The engine
never imports an STT or VAD provider.

The three momentary decisions (`CANCEL`/`CONFIRMED`/`TIMEOUT`) are normalized back
to `CONTINUE_LISTENING` at the start of the next observation so the returned
decision reports exactly what happened on *this* call — mirroring the Voice
Activity engine. Every transition is validated; an illegal or duplicate move
raises (no silent recovery).
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable

from ..errors import DuplicateEndpointDecision, InvalidEndpointTransition
from .context import EndpointContext
from .decision import ENDPOINT_TRANSITIONS, EndpointDecision
from .events import EndpointEvent, EndpointObserver
from .policy import EndpointPolicy

log = logging.getLogger("voxvoice.runtime.endpoint")

_MOMENTARY: frozenset[EndpointDecision] = frozenset(
    {
        EndpointDecision.CANCEL_ENDPOINT,
        EndpointDecision.ENDPOINT_CONFIRMED,
        EndpointDecision.TIMEOUT_ENDPOINT,
    }
)
_TURN_END: frozenset[EndpointDecision] = frozenset(
    {EndpointDecision.ENDPOINT_CONFIRMED, EndpointDecision.TIMEOUT_ENDPOINT}
)


class EndpointEngine:
    def __init__(
        self,
        session_id: str,
        *,
        policy: EndpointPolicy | None = None,
        on_event: EndpointObserver | None = None,
        clock: Callable[[], float] | None = None,
    ) -> None:
        self._policy = policy or EndpointPolicy()
        self._on_event = on_event
        self._clock = clock or time.time
        self._context = EndpointContext(session_id=session_id)
        self._speech_run = 0
        self._silence_run = 0
        self._frame = 0
        # Record (and announce) the initial CONTINUE_LISTENING decision; not a move.
        self._record(previous=None, decision=self._context.decision, confidence=1.0)

    # ── read-only exposure ──────────────────────────────────────────────────
    @property
    def context(self) -> EndpointContext:
        return self._context

    @property
    def decision(self) -> EndpointDecision:
        return self._context.decision

    @property
    def policy(self) -> EndpointPolicy:
        return self._policy

    # ── neutral signal inputs (sole writer of decision state) ───────────────
    def note_speech(self, confidence: float = 1.0) -> EndpointDecision | None:
        """A speech observation (an STT partial, or a VAD SPEAKING). Keeps the turn
        open; withdraws a pending endpoint if speech resumed during a hold."""
        return self._step("speech", confidence)

    def note_segment(self, confidence: float = 1.0) -> EndpointDecision | None:
        """A completed transcript segment (an STT Final). The primary completion
        trigger: with the default policy this confirms the turn immediately; with a
        silence hold configured it opens a POTENTIAL_ENDPOINT to be confirmed by
        subsequent silence."""
        return self._step("segment", confidence)

    def note_speech_end(self, confidence: float = 1.0) -> EndpointDecision | None:
        """**True end-of-speech, detected from audio** (M11.8, ADR-0047 ME1).

        The audio-driven completion trigger: the `AudioEndpointDetector` observed that the caller
        stopped speaking for the configured hold, which §A8 calls *true end-of-speech*. It confirms
        the turn directly — a pending `POTENTIAL_ENDPOINT` included — because the silence evidence
        the hold represents is exactly what a silence hold was waiting for.

        Neutral by construction: the engine is told *that* speech ended, never by which provider or
        from what model output."""
        return self._step("speech_end", confidence)

    def note_silence(self, confidence: float = 1.0) -> EndpointDecision | None:
        """A silence observation (a VAD SILENCE_CONFIRMED). Counts down a pending
        endpoint's silence hold; does nothing while merely listening."""
        return self._step("silence", confidence)

    def flush(self, confidence: float = 1.0) -> EndpointDecision | None:
        """Close out a turn whose segment is still pending when the input stream
        ends (caller hangup). Confirms a POTENTIAL_ENDPOINT deterministically so no
        utterance is silently dropped; a no-op while listening."""
        if self._context.decision is EndpointDecision.POTENTIAL_ENDPOINT:
            self._transition(EndpointDecision.ENDPOINT_CONFIRMED, confidence)
            return EndpointDecision.ENDPOINT_CONFIRMED
        return None

    def reset_turn(self) -> None:
        """Return the engine to a clean listening state for a new turn. Legal from
        any decision (a pending hold is cancelled first). Idempotent while already
        listening — used on runtime cancellation/shutdown (no silent recovery: the
        move still goes through the validated transition table)."""
        d = self._context.decision
        if d is EndpointDecision.POTENTIAL_ENDPOINT:
            self._transition(EndpointDecision.CANCEL_ENDPOINT, 1.0)
            d = EndpointDecision.CANCEL_ENDPOINT
        if d is not EndpointDecision.CONTINUE_LISTENING:
            self._transition(EndpointDecision.CONTINUE_LISTENING, 1.0)
        self._reset_counters()

    # ── the deterministic decision model ────────────────────────────────────
    def _step(self, kind: str, confidence: float) -> EndpointDecision | None:
        # Normalize the previous call's momentary decision before processing so it
        # is not re-reported as a change (mirrors the Voice Activity engine).
        prev = self._context.decision
        if prev in _TURN_END:
            self._transition(EndpointDecision.CONTINUE_LISTENING, confidence)
            self._reset_counters()  # a finished turn → fresh counters for the next
        elif prev is EndpointDecision.CANCEL_ENDPOINT:
            self._transition(EndpointDecision.CONTINUE_LISTENING, confidence)
            self._silence_run = 0  # same turn resumes → keep the accrued speech
        entry = self._context.decision

        self._frame += 1
        if kind == "speech":
            self._on_speech(confidence)
        elif kind == "segment":
            self._on_segment(confidence)
        elif kind == "silence":
            self._on_silence(confidence)
        elif kind == "speech_end":
            self._on_speech_end(confidence)

        # A turn may not run forever: once the configured budget is exceeded and no
        # final decision was reached, force a timeout (deterministic close-out).
        if (
            self._policy.max_turn_frames > 0
            and self._frame >= self._policy.max_turn_frames
            and self._context.decision
            in (EndpointDecision.CONTINUE_LISTENING, EndpointDecision.POTENTIAL_ENDPOINT)
        ):
            self._transition(EndpointDecision.TIMEOUT_ENDPOINT, confidence)

        return self._context.decision if self._context.decision is not entry else None

    def _on_speech(self, confidence: float) -> None:
        state = self._context.decision
        if state is EndpointDecision.CONTINUE_LISTENING:
            self._speech_run += 1  # accrue speech; the turn stays open
        elif state is EndpointDecision.POTENTIAL_ENDPOINT:
            # Speech resumed before the endpoint was confirmed — withdraw it.
            self._transition(EndpointDecision.CANCEL_ENDPOINT, confidence)

    def _on_segment(self, confidence: float) -> None:
        # A completed transcript segment implies speech occurred.
        self._speech_run += 1
        if self._policy.audio_driven:
            return  # ME1: audio ends the turn; a transcript is evidence, not a decision
        if confidence < self._policy.confidence_threshold:
            return  # low-confidence segment → keep listening (do not endpoint)
        if self._context.decision is not EndpointDecision.CONTINUE_LISTENING:
            return  # only a listening turn can be ended by a fresh segment
        if self._speech_run < self._policy.min_speech_frames:
            return  # not enough speech yet → keep listening
        if self._policy.confirms_on_segment:
            self._transition(EndpointDecision.ENDPOINT_CONFIRMED, confidence)
        else:
            self._silence_run = 0
            self._transition(EndpointDecision.POTENTIAL_ENDPOINT, confidence)

    def _on_speech_end(self, confidence: float) -> None:
        # Audio said the utterance is over. Confirm from either resting state; the detector has
        # already applied the temporal policy, so there is nothing left to hold for.
        if self._context.decision is EndpointDecision.CONTINUE_LISTENING:
            self._speech_run += 1  # end-of-speech implies speech occurred
            self._transition(EndpointDecision.ENDPOINT_CONFIRMED, confidence)
        elif self._context.decision is EndpointDecision.POTENTIAL_ENDPOINT:
            self._transition(EndpointDecision.ENDPOINT_CONFIRMED, confidence)

    def _on_silence(self, confidence: float) -> None:
        if self._context.decision is not EndpointDecision.POTENTIAL_ENDPOINT:
            return  # silence only matters once a potential endpoint is pending
        self._silence_run += 1
        if (
            self._silence_run >= self._policy.silence_hold_frames
            and self._silence_run >= self._policy.min_silence_frames
        ):
            self._transition(EndpointDecision.ENDPOINT_CONFIRMED, confidence)

    def _reset_counters(self) -> None:
        self._speech_run = 0
        self._silence_run = 0
        self._frame = 0

    def _transition(self, target: EndpointDecision, confidence: float) -> None:
        """Advance to `target`. Raises on any illegal or duplicate move — never
        silent. The model above only proposes legal edges, so a raise here means a
        coding error surfaced deterministically (or a misuse of a public method)."""
        current = self._context.decision
        if target == current:
            raise DuplicateEndpointDecision(
                f"endpoint already {current.value}; duplicate decision rejected"
            )
        if target not in ENDPOINT_TRANSITIONS[current]:
            raise InvalidEndpointTransition(
                f"illegal endpoint transition {current.value} -> {target.value}"
            )
        self._context.decision = target
        if target in _TURN_END:
            self._context.turn_index += 1  # count finalized turns (confirmed/timeout)
        self._record(previous=current, decision=target, confidence=confidence)

    def _record(
        self,
        *,
        previous: EndpointDecision | None,
        decision: EndpointDecision,
        confidence: float,
    ) -> None:
        ts = self._clock()
        self._context.timing.record(decision, ts)
        if self._on_event is None:
            return
        event = EndpointEvent(
            session_id=self._context.session_id,
            turn_index=self._context.turn_index,
            previous=previous,
            decision=decision,
            confidence=confidence,
            ts=ts,
        )
        try:
            self._on_event(event)
        except Exception as exc:  # noqa: BLE001 — observability is best-effort
            log.warning("endpoint observer raised (ignored): %s", exc)
