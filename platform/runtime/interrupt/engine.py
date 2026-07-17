"""InterruptEngine — the session-scoped owner of interruption decisions (M3.4).

Turns provider-neutral signals — assistant playback state and user speech/silence
observations — into the deterministic `InterruptDecision` model, governed entirely
by a configurable `InterruptPolicy` (nothing hardcoded). It is the SOLE writer of
interrupt-decision state. It owns NO turns (the Turn Engine does), no conversation
state (the Orchestrator does), no voice-activity state (the VoiceActivityEngine
does), and it never runs a provider or stops playback itself (the pipeline does,
driven by the coordinator).

Inputs are neutral signals, not provider objects (Constitution §A6):
`note_assistant_started` / `note_assistant_finished` (assistant playback state),
`note_user_speech` (a VAD `SPEAKING` / speech observation), `note_user_silence` (a
VAD `SILENCE_CONFIRMED`). Detection runs up to `INTERRUPT_CONFIRMED`; the *action*
phase (`begin_stop` → `start_user_turn` → `complete`) is advanced by the pipeline
via the coordinator as it performs the pre-emption.

The two momentary outcomes (`INTERRUPT_COMPLETED` / `INTERRUPT_CANCELLED`) are
normalized back to a resting decision at the start of the next signal, so a
returned decision reports exactly what happened on *this* call — mirroring the
Voice Activity and Endpoint engines. Every transition is validated; an illegal or
duplicate move raises (no silent recovery).
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable

from ..errors import DuplicateInterruptDecision, InvalidInterruptTransition
from .context import InterruptContext
from .decision import _MOMENTARY, INTERRUPT_TRANSITIONS, InterruptDecision
from .events import InterruptEvent, InterruptObserver
from .policy import InterruptPolicy

log = logging.getLogger("voxvoice.runtime.interrupt")

D = InterruptDecision


class InterruptEngine:
    def __init__(
        self,
        session_id: str,
        *,
        policy: InterruptPolicy | None = None,
        on_event: InterruptObserver | None = None,
        clock: Callable[[], float] | None = None,
    ) -> None:
        self._policy = policy or InterruptPolicy()
        self._on_event = on_event
        self._clock = clock or time.time
        self._context = InterruptContext(session_id=session_id)
        self._assistant_active = False
        self._speech_run = 0
        self._hold_run = 0
        # Record (and announce) the initial IDLE decision; not a move.
        self._record(previous=None, decision=self._context.decision, confidence=1.0)

    # ── read-only exposure ──────────────────────────────────────────────────
    @property
    def context(self) -> InterruptContext:
        return self._context

    @property
    def decision(self) -> InterruptDecision:
        return self._context.decision

    @property
    def policy(self) -> InterruptPolicy:
        return self._policy

    @property
    def assistant_active(self) -> bool:
        return self._assistant_active

    # ── signal inputs (detection; sole writer of decision state) ────────────
    def note_assistant_started(self, confidence: float = 1.0) -> InterruptDecision | None:
        """Assistant playback has begun — start monitoring for barge-in."""
        self._normalize(confidence)
        entry = self._context.decision
        if self._context.decision is D.IDLE:
            self._assistant_active = True
            self._speech_run = 0
            self._hold_run = 0
            self._transition(D.ASSISTANT_SPEAKING, confidence)
        return self._changed(entry)

    def note_assistant_finished(self, confidence: float = 1.0) -> InterruptDecision | None:
        """Assistant playback finished on its own — no interruption occurred."""
        self._normalize(confidence)
        entry = self._context.decision
        state = self._context.decision
        if state is D.ASSISTANT_SPEAKING:
            self._assistant_active = False
            self._transition(D.IDLE, confidence)
        elif state is D.POTENTIAL_INTERRUPT:
            # The assistant finished just as the user began — drop the pending
            # interrupt (it is moot); normalization then returns to IDLE.
            self._assistant_active = False
            self._transition(D.INTERRUPT_CANCELLED, confidence)
        return self._changed(entry)

    def note_user_speech(self, confidence: float = 1.0) -> InterruptDecision | None:
        """A user-speech observation during assistant playback (a VAD SPEAKING).
        Accrues toward a potential/confirmed interrupt per the policy."""
        self._normalize(confidence)
        entry = self._context.decision
        state = self._context.decision
        if state is D.ASSISTANT_SPEAKING:
            self._speech_run += 1
            within_grace = self._speech_run <= self._policy.assistant_grace_frames
            effective = self._speech_run - self._policy.assistant_grace_frames
            if (
                not within_grace
                and confidence >= self._policy.confidence_threshold
                and effective >= self._policy.min_interrupt_speech_frames
            ):
                self._hold_run = 0
                self._transition(D.POTENTIAL_INTERRUPT, confidence)
                if self._policy.interrupt_hold_frames <= 0:
                    self._transition(D.INTERRUPT_CONFIRMED, confidence)
        elif state is D.POTENTIAL_INTERRUPT:
            self._hold_run += 1
            if self._hold_run >= self._policy.interrupt_hold_frames:
                self._transition(D.INTERRUPT_CONFIRMED, confidence)
        # Any other state (confirmed/stopping/starting/idle) → duplicate speech is
        # suppressed (no transition); returns None.
        return self._changed(entry)

    def note_user_silence(self, confidence: float = 1.0) -> InterruptDecision | None:
        """A confirmed-silence observation. Withdraws a still-pending potential
        interrupt (the user stopped before the hold elapsed) — a false alarm."""
        self._normalize(confidence)
        entry = self._context.decision
        if self._context.decision is D.POTENTIAL_INTERRUPT:
            self._transition(D.INTERRUPT_CANCELLED, confidence)
        return self._changed(entry)

    # ── action phase (advanced by the pipeline via the coordinator) ─────────
    def begin_stop(self, confidence: float = 1.0) -> InterruptDecision:
        """The pipeline is stopping assistant playback (INTERRUPT_CONFIRMED →
        ASSISTANT_STOPPING). Raises if not confirmed (no silent recovery)."""
        self._transition(D.ASSISTANT_STOPPING, confidence)
        return self._context.decision

    def start_user_turn(self, confidence: float = 1.0) -> InterruptDecision:
        """Playback stopped; a new user turn is beginning (ASSISTANT_STOPPING →
        USER_TURN_STARTING)."""
        self._assistant_active = False
        self._transition(D.USER_TURN_STARTING, confidence)
        return self._context.decision

    def complete(self, confidence: float = 1.0) -> InterruptDecision:
        """The interrupt handoff is complete (USER_TURN_STARTING →
        INTERRUPT_COMPLETED). Counts one confirmed barge-in."""
        self._transition(D.INTERRUPT_COMPLETED, confidence)
        self._context.interrupt_count += 1
        return self._context.decision

    def reset(self) -> None:
        """Hard reset to IDLE for runtime cancellation / shutdown. Deterministic
        (always lands IDLE); used only on teardown, recorded (never silent)."""
        if self._context.decision is not D.IDLE:
            previous = self._context.decision
            self._context.decision = D.IDLE
            self._record(previous=previous, decision=D.IDLE, confidence=1.0)
        self._assistant_active = False
        self._speech_run = 0
        self._hold_run = 0

    # ── internals ────────────────────────────────────────────────────────────
    def _normalize(self, confidence: float) -> None:
        """Return the two momentary outcomes to a resting decision before the next
        signal (mirrors the Endpoint/Voice Activity engines)."""
        d = self._context.decision
        if d not in _MOMENTARY:
            return
        if d is D.INTERRUPT_COMPLETED:
            self._transition(D.IDLE, confidence)
        else:  # INTERRUPT_CANCELLED → resume monitoring if still speaking, else idle
            self._transition(
                D.ASSISTANT_SPEAKING if self._assistant_active else D.IDLE, confidence
            )
        self._speech_run = 0
        self._hold_run = 0

    def _changed(self, entry: InterruptDecision) -> InterruptDecision | None:
        return self._context.decision if self._context.decision is not entry else None

    def _transition(self, target: InterruptDecision, confidence: float) -> None:
        """Advance to `target`. Raises on any illegal or duplicate move — never
        silent. The model above only proposes legal edges, so a raise here means a
        coding error surfaced deterministically (or a misuse of a public method)."""
        current = self._context.decision
        if target == current:
            raise DuplicateInterruptDecision(
                f"interrupt already {current.value}; duplicate decision rejected"
            )
        if target not in INTERRUPT_TRANSITIONS[current]:
            raise InvalidInterruptTransition(
                f"illegal interrupt transition {current.value} -> {target.value}"
            )
        self._context.decision = target
        self._record(previous=current, decision=target, confidence=confidence)

    def _record(
        self,
        *,
        previous: InterruptDecision | None,
        decision: InterruptDecision,
        confidence: float,
    ) -> None:
        ts = self._clock()
        self._context.timing.record(decision, ts)
        if self._on_event is None:
            return
        event = InterruptEvent(
            session_id=self._context.session_id,
            previous=previous,
            decision=decision,
            confidence=confidence,
            ts=ts,
        )
        try:
            self._on_event(event)
        except Exception as exc:  # noqa: BLE001 — observability is best-effort
            log.warning("interrupt observer raised (ignored): %s", exc)
