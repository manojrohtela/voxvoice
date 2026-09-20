"""Real VAD selection through the bootstrap seam, and real PCM reaching it (M11.7).

1. **Is real VAD reachable the intended way?** Selected explicitly through `bootstrap`, resolved by
   the runtime, never built by the Orchestrator (AR-20).
2. **Does it refuse to be selected dishonestly?** No importable model library → a clear startup
   error, and **no silent fallback** to `LocalVoiceActivity`.
3. **Does the local profile still behave exactly as it did?**

Hermetic: the model is a deterministic fake; no credential is involved because Silero needs none.
"""

from __future__ import annotations

import asyncio
import sys
import types

import pytest

import bootstrap
from core.audio import AudioEncoding, AudioFrame
from core.session_control import StartSessionRequest
from providers.voice_activity.local import LocalVoiceActivity
from providers.voice_activity.silero import SileroVoiceActivity
from providers.voice_activity.silero_model import load_silero_model
from runtime.registry import CapabilityKind
from runtime.service import RuntimeSessionService
from tests.fake_speech_model import EnergyModel, pcm_window
from tests.test_webrtc_transport import FakeEndpoint


def _pcm16k(data: bytes) -> AudioFrame:
    return AudioFrame(data=data, sample_rate=16000, encoding=AudioEncoding.PCM16, channels=1)


# ── W. local profile preservation ───────────────────────────────────────────


def test_w_voice_activity_is_still_off_by_default():
    assert not bootstrap.build_registry().has(CapabilityKind.VOICE_ACTIVITY)
    assert not bootstrap.build_local_registry().has(CapabilityKind.VOICE_ACTIVITY)


def test_w_include_voice_activity_still_selects_the_local_detector():
    registry = bootstrap.build_local_registry(include_voice_activity=True)
    assert isinstance(registry.resolve(CapabilityKind.VOICE_ACTIVITY), LocalVoiceActivity)


def test_w_the_local_profile_name_is_equivalent_to_the_legacy_flag():
    registry = bootstrap.build_registry(voice_activity=bootstrap.VOICE_ACTIVITY_LOCAL)
    assert [m.name for m in registry.list(CapabilityKind.VOICE_ACTIVITY)] == ["local"]


def test_w_the_default_session_controller_has_no_vad():
    controller = bootstrap.build_session_controller()
    assert not controller._registry.has(CapabilityKind.VOICE_ACTIVITY)  # noqa: SLF001


# ── V. explicit selection ───────────────────────────────────────────────────


def test_v_the_silero_profile_selects_real_vad_and_only_real_vad():
    registry = bootstrap.build_registry(
        voice_activity=bootstrap.VOICE_ACTIVITY_SILERO, speech_model_factory=EnergyModel
    )
    assert isinstance(registry.resolve(CapabilityKind.VOICE_ACTIVITY), SileroVoiceActivity)
    assert [m.name for m in registry.list(CapabilityKind.VOICE_ACTIVITY)] == ["silero"]


def test_v_without_a_test_model_the_real_loader_is_wired(monkeypatch):
    monkeypatch.setitem(sys.modules, "pysilero_vad", types.ModuleType("pysilero_vad"))
    registry = bootstrap.build_registry(voice_activity=bootstrap.VOICE_ACTIVITY_SILERO)
    vad = registry.resolve(CapabilityKind.VOICE_ACTIVITY)
    assert isinstance(vad, SileroVoiceActivity)
    assert vad._model_factory is load_silero_model  # noqa: SLF001


def test_v_the_session_controller_can_be_built_on_real_vad(monkeypatch):
    monkeypatch.setitem(sys.modules, "pysilero_vad", types.ModuleType("pysilero_vad"))
    controller = bootstrap.build_session_controller(voice_activity=bootstrap.VOICE_ACTIVITY_SILERO)
    assert isinstance(
        controller._registry.resolve(CapabilityKind.VOICE_ACTIVITY),  # noqa: SLF001
        SileroVoiceActivity,
    )


def test_v_real_vad_composes_with_real_transport_and_stt_selection():
    from tests.fake_transcription import FakeTranscriptionClient

    registry = bootstrap.build_registry(
        transport=bootstrap.TRANSPORT_WEBRTC,
        media_endpoint=FakeEndpoint(),
        stt=bootstrap.STT_DEEPGRAM,
        stt_client=FakeTranscriptionClient(),
        voice_activity=bootstrap.VOICE_ACTIVITY_SILERO,
        speech_model_factory=EnergyModel,
    )
    assert registry.resolve(CapabilityKind.TRANSPORT).name == "webrtc"
    assert registry.resolve(CapabilityKind.STT).name == "deepgram"
    assert registry.resolve(CapabilityKind.VOICE_ACTIVITY).name == "silero"


# ── U. refusal, never downgrade ─────────────────────────────────────────────


def test_u_silero_without_the_library_is_refused_not_downgraded(monkeypatch):
    monkeypatch.setitem(sys.modules, "pysilero_vad", None)
    with pytest.raises(ValueError, match="refusing to fall back to the local detector"):
        bootstrap.build_registry(voice_activity=bootstrap.VOICE_ACTIVITY_SILERO)


def test_u_the_legacy_flag_cannot_smuggle_local_vad_in_beside_silero():
    with pytest.raises(ValueError, match="conflicts"):
        bootstrap.build_registry(
            include_voice_activity=True,
            voice_activity=bootstrap.VOICE_ACTIVITY_SILERO,
            speech_model_factory=EnergyModel,
        )


def test_u_an_unknown_vad_profile_is_refused():
    with pytest.raises(ValueError, match="unknown voice activity profile"):
        bootstrap.build_registry(voice_activity="ouija")


def test_u_a_bad_vad_profile_is_refused_before_anything_is_built():
    with pytest.raises(ValueError, match="unknown voice activity profile"):
        bootstrap.build_registry(
            transport=bootstrap.TRANSPORT_WEBRTC, media_endpoint=FakeEndpoint(), voice_activity="x"
        )


# ── real PCM → real VAD, through the unchanged runtime path ─────────────────


@pytest.mark.asyncio
async def test_real_pcm_reaches_real_vad_through_normal_ingress_and_drives_the_existing_engine():
    """Transport.inbound → ingress normalization → VAD tap → provider → background inference →
    `VoiceActivityEngine`. The engine's existing hysteresis consumes the verdicts unchanged; this
    test asserts only that real audio arrived and speech was *detected* — never a turn decision."""
    models: list[EnergyModel] = []

    def factory():
        models.append(EnergyModel())
        return models[-1]

    registry = bootstrap.build_registry(
        voice_activity=bootstrap.VOICE_ACTIVITY_SILERO, speech_model_factory=factory
    )
    service = RuntimeSessionService(registry)
    await service.start(
        StartSessionRequest(
            session_id="ses_rvad", org_id="o", agent_id="a", channel="web",
            system_prompt="p", sample_rate=16000,
        )
    )
    worker = service._workers["ses_rvad"]  # noqa: SLF001
    engine = worker.orchestrator.voice_activity_engine
    transport = registry.resolve(CapabilityKind.TRANSPORT)
    vad = registry.resolve(CapabilityKind.VOICE_ACTIVITY)
    try:
        # Real speech-like PCM, fed one window per frame. After each frame the loop is given turns
        # until that window's inference completes, so the next frame's detect() sees its verdict.
        for index in range(6):
            await transport.feed("ses_rvad", _pcm16k(pcm_window(20000)))
            for _ in range(200_000):
                if vad.windows_analysed("ses_rvad") > index:
                    break
                await asyncio.sleep(0)
        assert models and len(models[0].windows) >= 5, "real PCM never reached the model"
        assert engine is not None
        # The existing engine consumed real verdicts: speech was *detected*. Nothing about turns.
        assert engine.context.timing.speech_started_at is not None, engine.state
    finally:
        await transport.hangup("ses_rvad")
        await worker.wait_closed()
        await service.end("ses_rvad")
    assert vad.windows_analysed("ses_rvad") == 0, "the session's VAD state outlived the call"
