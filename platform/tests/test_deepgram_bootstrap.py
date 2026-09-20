"""Deepgram STT selection through the bootstrap seam, and real transcripts through the pipeline
(M11.6).

Three questions this file answers that the provider tests cannot, because all three are about
*wiring*:

1. **Is real STT reachable the intended way?** Selected explicitly through `bootstrap`, constructed
   there with one resolved credential (ADR-0049 CR3/CR4), resolved by the runtime — never built by
   the Orchestrator (AR-20).
2. **Does it refuse to be selected dishonestly?** No credential means no Deepgram — and **no silent
   fallback** to the local transcriber either.
3. **Does the local profile still behave exactly as it did?**

Hermetic: a `CredentialProvider` built from explicit overrides, never the process environment, and
a fake transcription socket. The full suite depends on no real credential and no network.
"""

from __future__ import annotations

from dataclasses import replace

import pytest

import bootstrap
from api.adapters.secrets import ChainedCredentialProvider, EnvCredentialProvider
from core.session_control import StartSessionRequest
from providers.stt.deepgram import DeepgramSTT
from providers.stt.deepgram_client import DeepgramTranscriptionClient
from providers.stt.local import LocalSTT
from runtime.registry import CapabilityKind
from runtime.service import RuntimeSessionService
from tests.fake_transcription import FakeTranscriptionClient
from tests.test_webrtc_transport import FakeEndpoint, _pcm

SECRET = "dg-bootstrap-key-fedcba9876543210"


def _credentials(**values: str) -> EnvCredentialProvider:
    """An ADR-0049 provider that cannot see the real environment: an unused prefix + overrides."""
    return EnvCredentialProvider(prefix="VOXVOICE_TEST_UNSET_PREFIX_", overrides=values)


# ── the local profile is unchanged by Deepgram existing ─────────────────────


def test_the_default_stt_profile_is_still_local():
    assert isinstance(bootstrap.build_registry().resolve(CapabilityKind.STT), LocalSTT)


def test_the_default_session_controller_uses_local_stt():
    controller = bootstrap.build_session_controller()
    assert controller._registry.resolve(CapabilityKind.STT).name == "local"  # noqa: SLF001


def test_build_local_registry_is_untouched_by_the_stt_profile():
    assert bootstrap.build_local_registry().resolve(CapabilityKind.STT).name == "local"


# ── explicit selection ──────────────────────────────────────────────────────


def test_the_deepgram_profile_resolves_the_credential_and_builds_the_real_client():
    registry = bootstrap.build_registry(
        stt=bootstrap.STT_DEEPGRAM,
        credentials=_credentials(**{bootstrap.DEEPGRAM_API_KEY: SECRET}),
    )
    stt = registry.resolve(CapabilityKind.STT)
    assert isinstance(stt, DeepgramSTT)
    assert isinstance(stt._client, DeepgramTranscriptionClient)  # noqa: SLF001
    assert SECRET not in repr(stt._client)  # noqa: SLF001


def test_selecting_deepgram_replaces_rather_than_shadows_local_stt():
    registry = bootstrap.build_registry(
        stt=bootstrap.STT_DEEPGRAM, stt_client=FakeTranscriptionClient()
    )
    assert [m.name for m in registry.list(CapabilityKind.STT)] == ["deepgram"]


def test_the_credential_name_is_the_documented_one():
    assert bootstrap.DEEPGRAM_API_KEY == "deepgram_api_key"
    example = (bootstrap.__file__.rsplit("/", 1)[0] + "/.env.example")
    with open(example, encoding="utf-8") as handle:
        lines = handle.read().splitlines()
    assert "VOXVOICE_SECRET_DEEPGRAM_API_KEY=" in lines, "credential not documented by name"


def test_the_key_rotation_seam_is_honoured():
    """ADR-0049 keeps `ChainedCredentialProvider` as the rotation seam; it must work here too."""
    chained = ChainedCredentialProvider(
        _credentials(), _credentials(**{bootstrap.DEEPGRAM_API_KEY: SECRET})
    )
    registry = bootstrap.build_registry(stt=bootstrap.STT_DEEPGRAM, credentials=chained)
    assert isinstance(registry.resolve(CapabilityKind.STT), DeepgramSTT)


def test_only_the_deepgram_credential_is_resolved():
    """CR4 — least privilege: the composition root asks for exactly one name."""
    asked: list[str] = []

    class Recording(EnvCredentialProvider):
        def get(self, name: str) -> str | None:
            asked.append(name)
            return SECRET

    bootstrap.build_registry(stt=bootstrap.STT_DEEPGRAM, credentials=Recording())
    assert asked == [bootstrap.DEEPGRAM_API_KEY]


# ── refusal, never downgrade ────────────────────────────────────────────────


def test_deepgram_without_a_credential_provider_is_refused_not_downgraded():
    with pytest.raises(ValueError, match="refusing to fall back"):
        bootstrap.build_registry(stt=bootstrap.STT_DEEPGRAM)


@pytest.mark.parametrize("value", [None, ""])
def test_deepgram_without_the_credential_is_refused_and_names_it(value):
    overrides = {} if value is None else {bootstrap.DEEPGRAM_API_KEY: value}
    with pytest.raises(ValueError, match="deepgram_api_key") as raised:
        bootstrap.build_registry(stt=bootstrap.STT_DEEPGRAM, credentials=_credentials(**overrides))
    assert "refusing to fall back" in str(raised.value)


def test_a_refused_profile_never_leaks_a_credential_value():
    class WrongName(EnvCredentialProvider):
        def get(self, name: str) -> str | None:
            return None

    with pytest.raises(ValueError) as raised:
        bootstrap.build_registry(stt=bootstrap.STT_DEEPGRAM, credentials=WrongName())
    assert SECRET not in str(raised.value)


def test_an_unknown_stt_profile_is_refused():
    with pytest.raises(ValueError, match="unknown stt profile"):
        bootstrap.build_registry(stt="carrier-pigeon")


def test_a_bad_stt_profile_is_refused_before_the_transport_is_built():
    endpoint = FakeEndpoint()
    with pytest.raises(ValueError, match="unknown stt profile"):
        bootstrap.build_registry(
            transport=bootstrap.TRANSPORT_WEBRTC, media_endpoint=endpoint, stt="nope"
        )


def test_the_session_controller_can_be_built_on_deepgram():
    controller = bootstrap.build_session_controller(
        stt=bootstrap.STT_DEEPGRAM,
        credentials=_credentials(**{bootstrap.DEEPGRAM_API_KEY: SECRET}),
    )
    assert isinstance(
        controller._registry.resolve(CapabilityKind.STT), DeepgramSTT  # noqa: SLF001
    )


# ── real PCM → real streaming STT → transcripts, through the unchanged pipeline ──


@pytest.mark.asyncio
async def test_real_media_is_transcribed_end_to_end_through_the_runtime():
    """WebRTC frames → Transport.inbound → pipeline ingress (normalizer) → ProviderGuard →
    DeepgramSTT → vendor socket → Partial/Final → transcript sink. No parallel path."""
    transcripts: list[tuple[str, str, bool]] = []

    def sink(session_id: str, role: str, text: str, is_final: bool) -> None:
        transcripts.append((role, text, is_final))

    # Genuine 48 kHz PCM16 frames from the WebRTC fixture, carrying the fake recognizer's words.
    frames = [
        replace(_pcm(seq), data=word.ljust(320, b"\x00"))
        for seq, word in enumerate((b"hello", b"again"))
    ]
    client = FakeTranscriptionClient()
    registry = bootstrap.build_registry(
        transport=bootstrap.TRANSPORT_WEBRTC,
        media_endpoint=FakeEndpoint(frames=frames),
        stt=bootstrap.STT_DEEPGRAM,
        stt_client=client,
    )
    service = RuntimeSessionService(registry, on_transcript=sink)
    await service.start(
        StartSessionRequest(
            session_id="ses_dg", org_id="o", agent_id="a", channel="web",
            system_prompt="p", sample_rate=48_000, language="en",
        )
    )
    worker = service._workers["ses_dg"]  # noqa: SLF001
    await worker.wait_closed()
    await service.end("ses_dg")

    (session,) = client.sessions
    assert session.spec.sample_rate == 48_000 and session.spec.channels == 1
    assert len(session.sent) == 2 and session.finished and session.closed
    user = [(text, final) for role, text, final in transcripts if role == "user"]
    assert user == [("hello", False), ("hello", True), ("again", False), ("again", True)]


@pytest.mark.asyncio
async def test_the_platform_default_language_is_refused_not_silently_transcribed():
    """**R-37, pinned.** Sessions default to `en-IN`; the Deepgram profile declares only `en`
    because nothing else has been verified. The honest outcome is a clear, typed refusal — no
    stream is opened, no transcript is invented, and the local transcriber does not quietly step
    in."""
    transcripts: list[tuple[str, str, bool]] = []
    client = FakeTranscriptionClient()
    registry = bootstrap.build_registry(
        transport=bootstrap.TRANSPORT_WEBRTC,
        media_endpoint=FakeEndpoint(frames=[_pcm(0)]),
        stt=bootstrap.STT_DEEPGRAM,
        stt_client=client,
    )
    def sink(session_id: str, role: str, text: str, is_final: bool) -> None:
        transcripts.append((role, text, is_final))

    service = RuntimeSessionService(registry, on_transcript=sink)
    request = StartSessionRequest(
        session_id="ses_dg_default", org_id="o", agent_id="a", channel="web",
        system_prompt="p", sample_rate=48_000,
    )
    assert request.language == "en-IN"
    await service.start(request)
    worker = service._workers["ses_dg_default"]  # noqa: SLF001
    await worker.wait_closed()
    await service.end("ses_dg_default")

    assert client.specs == [], "a stream was opened for an undeclared language"
    assert [t for t in transcripts if t[0] == "user"] == []
    assert isinstance(registry.resolve(CapabilityKind.STT), DeepgramSTT)
