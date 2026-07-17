"""SmartTurnPolicy — the configurable adaptive rules (M3.5).

Nothing about conversational timing is hardcoded. Every knob lives here and is
injected into the `SmartTurnEngine`. Invalid combinations are rejected at
construction (no silent config): negative durations/weights raise
`InvalidSmartTurnPolicy`.

The defaults are the *identity* policy: no added silence, no grace, a zero
interruption threshold, unit confidence weight, no smoothing — so the engine
reproduces the pre-M3.5 behaviour (endpoint-confirmed → FINISH_TURN immediately,
confirmed interrupt → CONFIRM_INTERRUPT). Adaptation is entirely opt-in via these
values.
"""

from __future__ import annotations

from dataclasses import dataclass

from ..errors import InvalidSmartTurnPolicy


@dataclass(frozen=True, slots=True)
class SmartTurnPolicy:
    """Adaptive conversational-timing rules. Durations are observation frames.

    - `adaptive_silence_frames` / `adaptive_silence_scale`: base + scale of the
      post-endpoint hold before responding (0 → respond immediately, FINISH_TURN).
    - `adaptive_grace_frames`: leading listening frames after endpoint onset that
      resolve to WAIT (let a trailing clause land) before finishing.
    - `dynamic_interruption_threshold`: minimum weighted confidence to CONFIRM a
      barge-in; below it the interrupt is IGNORED.
    - `speech_confidence_weight`: multiplier applied to raw confidence.
    - `timing_smoothing_frames`: consecutive identical raw decisions required
      before a *changed* decision is committed (0 → no smoothing).
    - `semantic_weight`: how strongly a semantic hint influences the blend
      (0 → semantic hints ignored even if an evaluator is present).
    """

    adaptive_silence_frames: int = 0
    adaptive_silence_scale: float = 1.0
    adaptive_grace_frames: int = 0
    dynamic_interruption_threshold: float = 0.0
    speech_confidence_weight: float = 1.0
    timing_smoothing_frames: int = 0
    semantic_weight: float = 1.0

    def __post_init__(self) -> None:
        checks = {
            "adaptive_silence_frames": self.adaptive_silence_frames,
            "adaptive_grace_frames": self.adaptive_grace_frames,
            "timing_smoothing_frames": self.timing_smoothing_frames,
        }
        for name, value in checks.items():
            if value < 0:
                raise InvalidSmartTurnPolicy(f"{name} must be >= 0, got {value}")
        floats = {
            "adaptive_silence_scale": self.adaptive_silence_scale,
            "dynamic_interruption_threshold": self.dynamic_interruption_threshold,
            "speech_confidence_weight": self.speech_confidence_weight,
            "semantic_weight": self.semantic_weight,
        }
        for name, value in floats.items():
            if value < 0:
                raise InvalidSmartTurnPolicy(f"{name} must be >= 0, got {value}")

    def response_delay_frames(self, *, delay_scale: float = 1.0) -> int:
        """The adaptive hold before responding, for the current signal. A semantic
        evaluator may stretch/shrink it via `delay_scale`."""
        raw = self.adaptive_silence_frames * self.adaptive_silence_scale * delay_scale
        return max(0, round(raw))
