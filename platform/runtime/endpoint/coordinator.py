"""EndpointCoordinator — the Runtime Pipeline's integration point for endpointing
(M3.3).

A thin façade the pipeline (and the Voice Activity Framework) call with neutral
turn signals. It drives the `EndpointEngine` and — as the framework's one link to
Turn Intelligence — notifies the Turn Engine when a turn should *finish*
(`on_user_final`). It NEVER owns turn state; the Turn Engine remains the sole
writer of that.

This replaces the pipeline's previous STT-final heuristic: completion is no longer
"respond on every Final" but "respond when the Endpoint Intelligence Framework
confirms the turn is complete" — a decision governed by the injected
`EndpointPolicy`. Keeping the pipeline coupled to this small surface (`on_user_speech`
/ `on_segment` / `on_user_silence` / `flush` / `reset` / `cancel`) is what lets
richer endpoint models (semantic EOT, adaptive delay) plug in without touching the
pipeline.
"""

from __future__ import annotations

from .decision import FINAL_ENDPOINT_DECISIONS, EndpointDecision
from .engine import EndpointEngine


class EndpointCoordinator:
    def __init__(
        self,
        *,
        engine: EndpointEngine,
        session_id: str,
        turns=None,
    ) -> None:
        self._engine = engine
        self._session_id = session_id
        # The Turn Engine façade (optional). The endpoint framework notifies it that
        # a turn should finish; it never manipulates turn state directly.
        self._turns = turns

    @property
    def engine(self) -> EndpointEngine:
        return self._engine

    def on_user_speech(self, confidence: float = 1.0) -> EndpointDecision | None:
        """Speech is in progress (an STT partial or a VAD SPEAKING). Keeps the turn
        open / withdraws a pending endpoint. Never finishes a turn on its own."""
        return self._engine.note_speech(confidence)

    def on_user_silence(self, confidence: float = 1.0) -> EndpointDecision | None:
        """Confirmed silence (a VAD SILENCE_CONFIRMED). May confirm a pending
        endpoint once the policy's silence hold is satisfied."""
        return self._notify(self._engine.note_silence(confidence))

    def on_speech_end(self, confidence: float = 1.0) -> EndpointDecision | None:
        """True end-of-speech was detected from audio (M11.8). Confirms the turn and notifies the
        Turn Engine through the one existing pathway — the coordinator remains the only link, and
        the Turn Engine remains the sole writer of turn state."""
        return self._notify(self._engine.note_speech_end(confidence))

    def on_segment(self, confidence: float = 1.0) -> EndpointDecision | None:
        """A completed transcript segment (an STT Final) — the primary completion
        trigger. Returns the resulting decision; a final decision has already
        notified the Turn Engine to finish the turn."""
        return self._notify(self._engine.note_segment(confidence))

    def flush(self, confidence: float = 1.0) -> EndpointDecision | None:
        """The input stream ended with a segment still pending — force a clean
        close-out so the last utterance is not dropped."""
        return self._notify(self._engine.flush(confidence))

    def reset(self) -> None:
        """Ready the engine for the next turn (called once a turn is completed)."""
        self._engine.reset_turn()

    def cancel(self) -> None:
        """Runtime cancellation / shutdown: terminalize the decision state cleanly
        through the validated transition table (no silent recovery)."""
        self._engine.reset_turn()

    def _notify(self, decision: EndpointDecision | None) -> EndpointDecision | None:
        if decision in FINAL_ENDPOINT_DECISIONS and self._turns is not None:
            self._turns.on_user_final()  # endpoint → Turn Engine: finish the turn
        return decision
