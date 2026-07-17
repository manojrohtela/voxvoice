"""Voice Activity Detection capability contract (Constitution §A6, ADR-0005; M3.2).

Provider-neutral speech-presence detection. A provider classifies inbound audio
frames as speech or silence; the framework's `VoiceActivityEngine`
(`runtime.voice_activity`) turns that raw signal into a deterministic activity
state model with hysteresis, so the model stays consistent across providers.
Future engines (Silero, WebRTC VAD, Deepgram, OpenAI Realtime, custom) implement
this one contract and plug in via the Provider Registry — no Runtime changes
(Constitution §A6, AR-21).

M3.2 is the framework only: no semantic/adaptive endpointing, no interruptions,
no double-talk, no noise suppression (those are later milestones). The provider
answers a single question per frame — "is this speech?" — with a confidence; the
engine owns the state model. A richer engine that natively emits speech
boundaries can report a hard `speech` flag (confidence 1.0) and set the engine's
thresholds to 1 — the contract does not constrain it.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field

from core.audio import AudioFrame


@dataclass(frozen=True, slots=True)
class VoiceActivityCapabilities:
    """Declared traits of a voice-activity provider (discovery + validation)."""

    streaming: bool = True  # analyzes frames incrementally as they arrive
    emits_confidence: bool = False  # `confidence` is a real score (vs a hard 0/1)
    frame_ms: int = 0  # native analysis frame size in ms; 0 = frame-size agnostic
    languages: tuple[str, ...] = field(default_factory=tuple)


@dataclass(frozen=True, slots=True)
class VoiceActivitySample:
    """One provider verdict for one audio frame. `confidence` is 0..1; providers
    without a real score report 1.0 (see `VoiceActivityCapabilities.emits_confidence`)."""

    speech: bool
    confidence: float = 1.0


class VoiceActivityProvider(ABC):
    """A voice-activity detector. Stateless across sessions from the framework's
    point of view — any per-session buffers/models are set up in `start` and torn
    down in `stop`, keyed by `session_id` so one instance can serve many sessions
    (as the local/dev and real streaming providers both do)."""

    name: str
    capabilities: VoiceActivityCapabilities

    @abstractmethod
    async def start(self, *, session_id: str) -> None:
        """Prepare per-session detection resources (models, ring buffers)."""

    @abstractmethod
    async def stop(self, *, session_id: str) -> None:
        """Release the session's detection resources."""

    @abstractmethod
    def detect(self, frame: AudioFrame, *, session_id: str) -> VoiceActivitySample:
        """Classify one audio frame as speech or silence for a session."""
