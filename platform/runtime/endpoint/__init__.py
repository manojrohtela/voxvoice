"""runtime.endpoint — the Endpoint Intelligence Framework (Constitution §A4.2, M3.3).

Determines when a caller's turn is *truly complete*. `EndpointEngine` owns the
deterministic `EndpointDecision` state for a session (sole writer), governed by a
configurable `EndpointPolicy`; `EndpointCoordinator` is the Runtime Pipeline's
integration point and the framework's one link to the Turn Engine;
`EndpointContext`/`EndpointTiming` carry per-session data + timing; `EndpointEvent`
is the internal notification extension point (not the Event Bus, M4).

It consumes neutral signals — speech observations, completed transcript segments,
and (optional) Voice Activity silence — never provider objects. It replaces the
pipeline's STT-final completion heuristic: the *decision* to finish a turn now
flows through this framework. M3.3 is deterministic, policy-driven endpointing —
no semantic/LLM endpointing, interruptions, or double-talk (M3.4+).

**M11.8 (ADR-0047 ME1)** adds `AudioEndpointDetector`: the audio-driven half, which turns per-frame
voice-activity verdicts into `speech_start` / `speech_end` boundaries using duration-based
hysteresis. **VAD is speech detection; endpointing decides when a speech turn ends.** The detector
stays provider-neutral — booleans and milliseconds in, boundaries out — and the decision *record*
remains the `EndpointEngine`'s, reached through the new neutral `note_speech_end` signal.
"""

from .audio import (
    AUDIO_ENDPOINT_TRANSITIONS,
    AudioEndpointDetector,
    AudioEndpointKind,
    AudioEndpointPolicy,
    AudioEndpointSignal,
    AudioEndpointState,
    frame_duration_ms,
)
from .context import EndpointContext, EndpointTiming
from .coordinator import EndpointCoordinator
from .decision import (
    ENDPOINT_TRANSITIONS,
    FINAL_ENDPOINT_DECISIONS,
    EndpointDecision,
)
from .engine import EndpointEngine
from .events import EndpointEvent, EndpointObserver
from .policy import EndpointPolicy

__all__ = [
    "AUDIO_ENDPOINT_TRANSITIONS",
    "AudioEndpointDetector",
    "AudioEndpointKind",
    "AudioEndpointPolicy",
    "AudioEndpointSignal",
    "AudioEndpointState",
    "frame_duration_ms",
    "EndpointEngine",
    "EndpointPolicy",
    "EndpointCoordinator",
    "EndpointContext",
    "EndpointTiming",
    "EndpointEvent",
    "EndpointObserver",
    "EndpointDecision",
    "ENDPOINT_TRANSITIONS",
    "FINAL_ENDPOINT_DECISIONS",
]
