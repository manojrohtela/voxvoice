"""runtime.voice_activity — the Voice Activity Framework (VAF; Constitution §A4.2, M3.2).

Provider-neutral speech-presence detection. `VoiceActivityEngine` owns the
deterministic activity state for a session (sole writer); `VoiceActivityCoordinator`
is the Runtime Pipeline's integration point and the framework's one link to the
Turn Engine; `VoiceActivityContext`/`VoiceActivityTiming` carry per-session data +
timing; `VoiceActivityEvent` is the internal notification extension point (not the
Event Bus, M4). The provider capability contract lives in
`core.providers.voice_activity.VoiceActivityProvider` and is resolved via the
Provider Registry (`CapabilityKind.VOICE_ACTIVITY`).

M3.2 is the framework only — no semantic/adaptive endpointing, interruptions,
double-talk, or noise suppression (those plug in at M3.3+). The VAF supplies
speech-activity signals; it never owns turns.
"""

from .context import VoiceActivityContext, VoiceActivityTiming
from .coordinator import VoiceActivityCoordinator
from .engine import VoiceActivityConfig, VoiceActivityEngine
from .events import VoiceActivityEvent, VoiceActivityObserver
from .state import VA_TRANSITIONS, VoiceActivityState

__all__ = [
    "VoiceActivityEngine",
    "VoiceActivityConfig",
    "VoiceActivityCoordinator",
    "VoiceActivityContext",
    "VoiceActivityTiming",
    "VoiceActivityEvent",
    "VoiceActivityObserver",
    "VoiceActivityState",
    "VA_TRANSITIONS",
]
