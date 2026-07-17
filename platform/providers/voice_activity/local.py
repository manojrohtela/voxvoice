"""LocalVoiceActivity — deterministic in-process VAD for local dev + tests (M3.2).

Implements the `VoiceActivityProvider` contract with NO external SDKs and NO ML
models — it exists only to validate the framework architecture end-to-end. For
the local-dev profile an audio frame carries its transcript as UTF-8 bytes
(matching `LocalSTT`), so "is this speech?" is simply "does the frame carry any
non-whitespace payload?" — fully deterministic and network-free. A real streaming
VAD engine is a drop-in replacement behind the same contract.
"""

from __future__ import annotations

from core.audio import AudioFrame
from core.providers.voice_activity import (
    VoiceActivityCapabilities,
    VoiceActivityProvider,
    VoiceActivitySample,
)


class LocalVoiceActivity(VoiceActivityProvider):
    name = "local"
    capabilities = VoiceActivityCapabilities(streaming=True, emits_confidence=False)

    async def start(self, *, session_id: str) -> None:
        """No per-session resources — deterministic, stateless detection."""

    async def stop(self, *, session_id: str) -> None:
        """No per-session resources to release."""

    def detect(self, frame: AudioFrame, *, session_id: str) -> VoiceActivitySample:
        text = frame.data.decode("utf-8", errors="replace").strip()
        return VoiceActivitySample(speech=bool(text), confidence=1.0)
