"""TurnCoordinator — the Runtime Pipeline's integration point for turns (M3.1).

A thin façade the pipeline calls with coarse, in-order lifecycle signals; it
drives the `TurnEngine` accordingly. Keeping the pipeline's coupling to this small
stable surface (rather than the engine's transition API) is what lets future
Turn Intelligence subsystems (VAD, endpoint detection, the interrupt engine) plug
in here without touching the pipeline.

The pipeline calls these as pure notifications — they only advance turn state and
record timing, never affecting speech, transcripts, or audio (conversation
behaviour is unchanged, M3.1 requirement).
"""

from __future__ import annotations

from .engine import TurnEngine
from .phase import TurnPhase


class TurnCoordinator:
    def __init__(self, engine: TurnEngine) -> None:
        self._engine = engine

    @property
    def engine(self) -> TurnEngine:
        return self._engine

    def on_user_speech(self) -> None:
        """First caller audio / partial transcript of an utterance. Lazily begins
        a new turn if none is active, then marks USER_SPEAKING (once)."""
        if self._engine.current is None:
            self._engine.begin_turn()  # IDLE -> LISTENING
        if self._engine.current is not None and self._engine.current.phase is TurnPhase.LISTENING:
            self._engine.advance(TurnPhase.USER_SPEAKING)

    def on_user_final(self) -> None:
        """The caller's utterance is complete (a Final transcript)."""
        self._engine.advance(TurnPhase.USER_FINISHED)

    def on_assistant_thinking(self) -> None:
        """The assistant has begun producing a response."""
        self._engine.advance(TurnPhase.ASSISTANT_THINKING)

    def on_assistant_speaking(self) -> None:
        """The assistant's response is being played back to the caller."""
        self._engine.advance(TurnPhase.ASSISTANT_SPEAKING)

    def on_turn_completed(self) -> None:
        """The assistant's response is finished — the turn is complete."""
        self._engine.advance(TurnPhase.TURN_COMPLETED)

    def cancel(self) -> None:
        """Cancel the active turn (e.g. session shutdown/cancel)."""
        self._engine.cancel_current()
