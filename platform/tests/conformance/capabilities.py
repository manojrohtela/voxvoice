"""The provider inventory the conformance suite runs against (M11.3, ADR-0051 CE5 control 4).

One registry of *providers under test*, per capability. A provider enters the suite here and is
then judged by every assertion for its capability — there is no per-provider opt-out, because a
suite a provider can decline is not a contract.

The set is the local profile today. A real vendor lands at M11.5+ by adding one entry, which is
what §A6.3's *"one module + one registry entry + passing the shared contract test suite"* means in
practice.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from core.audio import AudioFrame
from providers.llm.echo import EchoLLM
from providers.stt.deepgram import DeepgramSTT
from providers.stt.local import LocalSTT
from providers.transport.local import LocalTransport
from providers.tts.local import LocalTTS
from providers.voice_activity.local import LocalVoiceActivity
from providers.voice_activity.silero import SileroVoiceActivity
from runtime.registry import CapabilityKind
from tests.fake_speech_model import EnergyModel
from tests.fake_transcription import FakeTranscriptionClient, pcm_frame


def _undeclared_frame(payload: bytes) -> AudioFrame:
    """The frame the local profile has always been probed with: an undeclared (`OPAQUE`) payload."""
    return AudioFrame(data=payload)


def _deepgram_over_fake_socket() -> DeepgramSTT:
    """The real provider over a deterministic vendor socket — no network, no credential."""
    return DeepgramSTT(FakeTranscriptionClient())


def _silero_over_fake_model() -> SileroVoiceActivity:
    """The real VAD provider — real buffer, windowing, worker thread and inference task — over a
    deterministic model instead of the native weights."""
    return SileroVoiceActivity(EnergyModel)


@dataclass(frozen=True, slots=True)
class ProviderUnderTest:
    """One provider the conformance suite must pass.

    `factory` rather than an instance: several assertions need a provider with no prior session
    state, and sharing one instance across them would let an earlier test's `start()` mask a later
    test's lifecycle failure."""

    kind: CapabilityKind
    name: str
    factory: Callable[[], Any]
    #: Whether this provider is expected to run real analysis windows. The local profile is
    #: deterministic and frame-size agnostic (`frame_ms = 0`), which is a *truthful* declaration,
    #: not an exemption — the windowing assertions read the declared value rather than assume one.
    deterministic: bool = True
    #: How a probe payload becomes a frame **this provider accepts** (M11.6). The local profile
    #: reads undeclared payloads; a real STT validates its declared format and refuses anything
    #: else, so it is probed with genuine PCM16. The assertions themselves are identical — only the
    #: input shape follows the provider's own declared contract.
    frame: Callable[[bytes], AudioFrame] = _undeclared_frame


CONFORMANCE_PROVIDERS: tuple[ProviderUnderTest, ...] = (
    ProviderUnderTest(CapabilityKind.STT, "local", LocalSTT),
    ProviderUnderTest(
        CapabilityKind.STT, "deepgram", _deepgram_over_fake_socket, frame=pcm_frame
    ),
    ProviderUnderTest(CapabilityKind.TTS, "local", LocalTTS),
    ProviderUnderTest(CapabilityKind.LLM, "echo", EchoLLM),
    ProviderUnderTest(CapabilityKind.TRANSPORT, "local", LocalTransport),
    ProviderUnderTest(CapabilityKind.VOICE_ACTIVITY, "local", LocalVoiceActivity),
    ProviderUnderTest(
        CapabilityKind.VOICE_ACTIVITY, "silero", _silero_over_fake_model, frame=pcm_frame
    ),
)


def _of(kind: CapabilityKind) -> tuple[ProviderUnderTest, ...]:
    return tuple(p for p in CONFORMANCE_PROVIDERS if p.kind is kind)


def stt_providers() -> tuple[ProviderUnderTest, ...]:
    return _of(CapabilityKind.STT)


def tts_providers() -> tuple[ProviderUnderTest, ...]:
    return _of(CapabilityKind.TTS)


def llm_providers() -> tuple[ProviderUnderTest, ...]:
    return _of(CapabilityKind.LLM)


def transport_providers() -> tuple[ProviderUnderTest, ...]:
    return _of(CapabilityKind.TRANSPORT)


def voice_activity_providers() -> tuple[ProviderUnderTest, ...]:
    return _of(CapabilityKind.VOICE_ACTIVITY)


__all__ = [
    "CONFORMANCE_PROVIDERS",
    "ProviderUnderTest",
    "llm_providers",
    "stt_providers",
    "transport_providers",
    "tts_providers",
    "voice_activity_providers",
]
