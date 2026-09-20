"""WebRTC selection through the bootstrap seam, and AR-6 reaching the event bus (M11.5).

Two questions this file answers that `test_webrtc_transport.py` cannot, because both are about
*wiring* rather than about the transport itself:

1. **Is the real transport reachable the intended way?** Through `bootstrap`, into the registry,
   resolved by the runtime — never constructed inside the Orchestrator (AR-20).
2. **Does transport reality actually become an event?** AR-6 is not satisfied by a transport that
   knows the truth privately; the fact has to reach the bus that drives the session.

And one question that matters more than either: **does the local profile still behave exactly as
it did?** A real transport existing must not change what an existing caller gets.
"""

from __future__ import annotations

import pytest

import bootstrap
from core.audio import AudioEncoding, AudioFrame
from core.events import EventType
from core.providers.transport import ObservableTransport
from providers.transport.local import LocalTransport
from providers.transport.webrtc import WebRTCTransport
from runtime.registry import CapabilityKind
from tests.test_webrtc_transport import FakeEndpoint, _pcm

SESSION = "ses_boot"


# ── the local profile is unchanged by WebRTC existing ───────────────────────


def test_the_default_profile_is_still_the_local_transport():
    """The floor. A capability nobody selected must not start acting."""
    registry = bootstrap.build_registry()
    assert isinstance(registry.resolve(CapabilityKind.TRANSPORT), LocalTransport)


def test_build_local_registry_is_untouched():
    registry = bootstrap.build_local_registry()
    assert registry.resolve(CapabilityKind.TRANSPORT).name == "local"


def test_the_default_session_controller_uses_the_local_transport():
    controller = bootstrap.build_session_controller()
    assert controller._registry.resolve(CapabilityKind.TRANSPORT).name == "local"  # noqa: SLF001


# ── WebRTC is selectable, and refuses to be selected dishonestly ────────────


def test_the_webrtc_profile_selects_the_real_transport():
    registry = bootstrap.build_registry(
        transport=bootstrap.TRANSPORT_WEBRTC, media_endpoint=FakeEndpoint()
    )
    transport = registry.resolve(CapabilityKind.TRANSPORT)
    assert isinstance(transport, WebRTCTransport)
    assert transport.name == "webrtc"


def test_selecting_webrtc_replaces_rather_than_shadows_the_local_transport():
    """A second registered transport would leave resolution ambiguous — and, worse, could keep
    silently serving the loopback while the operator believed WebRTC was live."""
    registry = bootstrap.build_registry(
        transport=bootstrap.TRANSPORT_WEBRTC, media_endpoint=FakeEndpoint()
    )
    assert [m.name for m in registry.list(CapabilityKind.TRANSPORT)] == ["webrtc"]


def test_webrtc_without_a_media_endpoint_is_refused_not_downgraded():
    """Falling back to loopback here would tell a caller who asked for real media that they had
    it. Refusing is the honest failure (GOVERNANCE §4)."""
    with pytest.raises(ValueError, match="media endpoint"):
        bootstrap.build_registry(transport=bootstrap.TRANSPORT_WEBRTC)


def test_an_unknown_transport_profile_is_refused():
    with pytest.raises(ValueError, match="unknown transport profile"):
        bootstrap.build_registry(transport="carrier-pigeon")


def test_the_session_controller_can_be_built_on_webrtc():
    controller = bootstrap.build_session_controller(
        transport=bootstrap.TRANSPORT_WEBRTC, media_endpoint=FakeEndpoint()
    )
    assert isinstance(
        controller._registry.resolve(CapabilityKind.TRANSPORT), WebRTCTransport  # noqa: SLF001
    )


# ── AR-6 reaches the event bus ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_transport_reality_reaches_the_event_bus_in_order():
    """**The end-to-end AR-6 assertion.** The transport observes; the runtime relays; the bus
    carries `transport.*` types that exist for exactly this and nothing else."""
    from core.session_control import StartSessionRequest
    from runtime.service import RuntimeSessionService

    endpoint = FakeEndpoint(frames=[_pcm(0)])
    registry = bootstrap.build_registry(
        transport=bootstrap.TRANSPORT_WEBRTC, media_endpoint=endpoint
    )
    service = RuntimeSessionService(registry)
    await service.start(
        StartSessionRequest(
            session_id=SESSION, org_id="o", agent_id="a", channel="web", system_prompt="p"
        )
    )
    worker = service._workers[SESSION]  # noqa: SLF001
    bus = worker.orchestrator.event_bus
    seen: list[EventType] = []
    bus.subscribe(lambda event: seen.append(event.type))

    await worker.wait_closed()
    await service.end(SESSION)

    transport_events = [t for t in seen if t.value.startswith("transport.")]
    assert EventType.TRANSPORT_MEDIA_READY in transport_events
    assert EventType.TRANSPORT_DISCONNECTED in transport_events
    assert transport_events.index(EventType.TRANSPORT_MEDIA_READY) < transport_events.index(
        EventType.TRANSPORT_DISCONNECTED
    )


@pytest.mark.asyncio
async def test_the_local_profile_emits_no_transport_reality_events():
    """`LocalTransport` knows no real facts, so it invents none — the loopback never claims a
    connection it does not have."""
    from core.session_control import StartSessionRequest
    from runtime.service import RuntimeSessionService

    registry = bootstrap.build_local_registry()
    service = RuntimeSessionService(registry)
    await service.start(
        StartSessionRequest(
            session_id="ses_local_ar6", org_id="o", agent_id="a", channel="web", system_prompt="p"
        )
    )
    worker = service._workers["ses_local_ar6"]  # noqa: SLF001
    seen: list[EventType] = []
    worker.orchestrator.event_bus.subscribe(lambda event: seen.append(event.type))

    transport = registry.resolve(CapabilityKind.TRANSPORT)
    await transport.feed("ses_local_ar6", AudioFrame(data=b"hi"))
    await transport.hangup("ses_local_ar6")
    await worker.wait_closed()
    await service.end("ses_local_ar6")

    assert not [t for t in seen if t.value.startswith("transport.")]


def test_local_transport_does_not_advertise_observability():
    assert not isinstance(LocalTransport(), ObservableTransport)


# ── real media flows through the unchanged pipeline ─────────────────────────


@pytest.mark.asyncio
async def test_real_pcm_media_reaches_stt_through_the_normal_ingress():
    """The real path end to end: WebRTC → Transport.inbound → pipeline ingress → STT.

    No parallel audio path, and no transcript-as-audio: the frames are genuine PCM16 and they
    arrive at the STT stage the same way loopback frames always have."""
    from core.session_control import StartSessionRequest
    from runtime.service import RuntimeSessionService

    seen: list[AudioFrame] = []

    from core.providers.stt import STTCapabilities, STTProvider

    class RecordingSTT(STTProvider):
        name = "recording-stt"
        capabilities = STTCapabilities(streaming=True, partials=False)

        async def transcribe(self, frames, *, language, sample_rate):
            async for frame in frames:
                seen.append(frame)
            return
            yield  # pragma: no cover — empty async generator

    endpoint = FakeEndpoint(frames=[_pcm(0), _pcm(1)])
    registry = bootstrap.build_registry(
        transport=bootstrap.TRANSPORT_WEBRTC, media_endpoint=endpoint
    )
    registry.unregister(CapabilityKind.STT, "local")
    registry.register(CapabilityKind.STT, RecordingSTT(), default=True)

    service = RuntimeSessionService(registry)
    await service.start(
        StartSessionRequest(
            session_id="ses_media", org_id="o", agent_id="a", channel="web",
            system_prompt="p", sample_rate=48_000,
        )
    )
    worker = service._workers["ses_media"]  # noqa: SLF001
    await worker.wait_closed()
    await service.end("ses_media")

    assert len(seen) == 2, f"real media did not reach STT: {seen}"
    for frame in seen:
        assert frame.encoding is AudioEncoding.PCM16
        assert frame.sample_rate == 48_000
