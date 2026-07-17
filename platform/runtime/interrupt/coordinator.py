"""InterruptCoordinator — the Runtime Pipeline's integration point for barge-in
(M3.4).

A thin façade the pipeline and the Voice Activity Framework call with neutral
signals. It drives the `InterruptEngine`, exposes a `stop_requested` signal the
assistant-playback loop polls, and — as the framework's one link to Turn
Intelligence — cancels the interrupted turn on the Turn Engine when playback is
pre-empted. It NEVER owns turn state; the Turn Engine remains the sole writer, and
the *new* user turn is begun by the normal speech path (`on_user_speech`), not
here.

Detection (`on_assistant_started`/`on_user_speech`/`on_user_silence`/
`on_assistant_finished`) runs up to a confirmed interrupt, at which point
`stop_requested` is set. The pipeline then performs the pre-emption and drives the
action phase (`begin_stop` → `start_user_turn` → `complete`). Keeping the pipeline
coupled to this small surface is what lets future semantic interruption engines
plug in without touching the pipeline.

When no interrupt support is wired the pipeline never constructs this coordinator
and runs exactly as M3.3 (graceful fallback).
"""

from __future__ import annotations

import asyncio

from .decision import CONFIRMED_INTERRUPT, InterruptDecision
from .engine import InterruptEngine


class InterruptCoordinator:
    def __init__(
        self,
        *,
        engine: InterruptEngine,
        session_id: str,
        turns=None,
    ) -> None:
        self._engine = engine
        self._session_id = session_id
        # The Turn Engine façade (optional). The interrupt framework cancels the
        # interrupted turn; it never manipulates turn state beyond that.
        self._turns = turns
        # Set when an interrupt is confirmed; the playback loop polls it and the run
        # loop acts on it. An asyncio primitive so playback can await pre-emption.
        self._stop_requested = asyncio.Event()

    @property
    def engine(self) -> InterruptEngine:
        return self._engine

    @property
    def stop_requested(self) -> asyncio.Event:
        return self._stop_requested

    @property
    def interrupting(self) -> bool:
        """True once an interrupt is confirmed and until the action phase completes."""
        return self._engine.decision in (
            InterruptDecision.INTERRUPT_CONFIRMED,
            InterruptDecision.ASSISTANT_STOPPING,
            InterruptDecision.USER_TURN_STARTING,
        )

    # ── detection signals ────────────────────────────────────────────────────
    def on_assistant_started(self) -> InterruptDecision | None:
        """Assistant playback began — begin monitoring for barge-in."""
        return self._engine.note_assistant_started()

    def on_assistant_finished(self) -> InterruptDecision | None:
        """Assistant playback finished on its own (no interruption)."""
        return self._engine.note_assistant_finished()

    def on_user_speech(self, confidence: float = 1.0) -> InterruptDecision | None:
        """User speech during assistant playback (a VAD SPEAKING). On a confirmed
        interrupt, raises the `stop_requested` signal for the playback loop."""
        decision = self._engine.note_user_speech(confidence)
        if decision is CONFIRMED_INTERRUPT:
            self._stop_requested.set()
        return decision

    def on_user_silence(self, confidence: float = 1.0) -> InterruptDecision | None:
        """Confirmed silence — withdraws a still-pending potential interrupt."""
        return self._engine.note_user_silence(confidence)

    # ── action phase (the pipeline performs the pre-emption) ────────────────
    def begin_stop(self) -> InterruptDecision:
        """The pipeline has requested assistant playback to stop."""
        return self._engine.begin_stop()

    def start_user_turn(self) -> InterruptDecision:
        """Playback stopped — cancel the interrupted assistant turn on the Turn
        Engine; the new user turn begins via the normal speech path."""
        decision = self._engine.start_user_turn()
        if self._turns is not None:
            self._turns.cancel()  # terminalize the pre-empted assistant turn
        return decision

    def complete(self) -> InterruptDecision:
        """The interrupt handoff is complete; ready for the next assistant utterance."""
        decision = self._engine.complete()
        self._stop_requested.clear()
        return decision

    def reset(self) -> None:
        """Ready the coordinator for the next assistant utterance / cycle."""
        self._engine.reset()
        self._stop_requested.clear()

    def cancel(self) -> None:
        """Runtime cancellation / shutdown: clear any in-flight interrupt state."""
        self._engine.reset()
        self._stop_requested.clear()
