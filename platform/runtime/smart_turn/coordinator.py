"""SmartTurnCoordinator — the Runtime Pipeline's integration point for final
conversational-timing decisions (M3.5).

A thin façade the pipeline calls at its two decision points. It assembles a neutral
`SmartTurnInput` from the sub-frameworks' outputs (which keep owning their state)
and asks the `SmartTurnEngine` to arbitrate. It NEVER writes turn / endpoint /
interrupt / voice-activity state — it only reads their decisions and returns the
final Smart Turn decision for the pipeline to act on.

Keeping the pipeline coupled to this small surface (`on_endpoint` / `on_interrupt`
+ `pending_delay_frames`) is what lets richer (semantic) Smart Turn engines plug in
without touching the pipeline. When no Smart Turn is wired the pipeline consumes the
raw endpoint/interrupt decisions exactly as M3.4 (graceful fallback).
"""

from __future__ import annotations

from ..endpoint import EndpointDecision
from ..interrupt import InterruptDecision
from ..turn import TurnPhase
from ..voice_activity import VoiceActivityState
from .decision import SmartTurnDecision
from .engine import SmartTurnEngine
from .signals import SmartTurnInput


class SmartTurnCoordinator:
    def __init__(self, *, engine: SmartTurnEngine, session_id: str) -> None:
        self._engine = engine
        self._session_id = session_id

    @property
    def engine(self) -> SmartTurnEngine:
        return self._engine

    @property
    def pending_delay_frames(self) -> int:
        return self._engine.pending_delay_frames

    def on_endpoint(
        self,
        endpoint: EndpointDecision,
        *,
        confidence: float = 1.0,
        turn_phase: TurnPhase | None = None,
        voice_activity: VoiceActivityState | None = None,
        assistant_speaking: bool = False,
        elapsed_frames: int = 0,
    ) -> SmartTurnDecision:
        """Arbitrate an endpoint decision into a final timing decision."""
        return self._engine.decide(
            SmartTurnInput(
                endpoint=endpoint,
                turn_phase=turn_phase,
                voice_activity=voice_activity,
                confidence=confidence,
                assistant_speaking=assistant_speaking,
                elapsed_frames=elapsed_frames,
            )
        )

    def on_interrupt(
        self,
        interrupt: InterruptDecision,
        *,
        confidence: float = 1.0,
        assistant_speaking: bool = True,
    ) -> SmartTurnDecision:
        """Arbitrate an interrupt decision into a final timing decision."""
        return self._engine.decide(
            SmartTurnInput(
                interrupt=interrupt,
                confidence=confidence,
                assistant_speaking=assistant_speaking,
            )
        )

    def reset(self) -> None:
        self._engine.reset()

    def cancel(self) -> None:
        self._engine.reset()
