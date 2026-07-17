"""SmartTurnInput — the neutral signal snapshot the engine arbitrates (M3.5).

A frozen, provider-neutral view assembled by the pipeline/coordinator from the
sub-frameworks' *own* outputs (they keep owning their state; Smart Turn only reads
them). No vendor objects, no provider events — only runtime enums + primitives.
"""

from __future__ import annotations

from dataclasses import dataclass

from ..endpoint import EndpointDecision
from ..interrupt import InterruptDecision
from ..turn import TurnPhase
from ..voice_activity import VoiceActivityState


@dataclass(frozen=True, slots=True)
class SmartTurnInput:
    """One arbitration input. Every field is optional so the pipeline can supply
    only what a given decision point knows."""

    endpoint: EndpointDecision | None = None
    interrupt: InterruptDecision | None = None
    voice_activity: VoiceActivityState | None = None
    turn_phase: TurnPhase | None = None
    confidence: float = 1.0
    assistant_speaking: bool = False
    elapsed_frames: int = 0

    def features(self) -> dict[str, float]:
        """A neutral named-float feature map for a semantic evaluator (never enums
        or vendor types — Constitution §A6)."""
        return {
            "confidence": self.confidence,
            "assistant_speaking": 1.0 if self.assistant_speaking else 0.0,
            "elapsed_frames": float(self.elapsed_frames),
            "endpoint_final": 1.0
            if self.endpoint
            in (EndpointDecision.ENDPOINT_CONFIRMED, EndpointDecision.TIMEOUT_ENDPOINT)
            else 0.0,
            "interrupt_confirmed": 1.0
            if self.interrupt is InterruptDecision.INTERRUPT_CONFIRMED
            else 0.0,
        }
