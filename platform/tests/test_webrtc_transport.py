"""Real WebRTC transport and AR-6 session-reality (M11.5).

Every assertion here is deterministic and hermetic: the low-level media path is a fake satisfying
`MediaEndpoint`, so lifecycle, ordering, backpressure and failure are all provable without a peer,
a network or a codec (TESTING_POLICY rules 1–2). **No sleeps** — shutdown and ordering are proven
by awaiting the things that actually complete.

The property this file exists to protect is **AR-6 truthfulness**: a transport fact is emitted
only when the fact is real. `MEDIA_READY` on the first actual frame, never on `start()`;
`DISCONNECTED` for a media path that ended, `FAILED` for one that broke; and the two are never
confused, because a hangup and a fault lead a session somewhere different.
"""

from __future__ import annotations

import ast
import asyncio
import inspect
import pathlib

import pytest

from core.audio import AudioEncoding, AudioFrame
from core.errors import ProviderStreamTruncated, ProviderUnavailable
from core.providers.transport import (
    ObservableTransport,
    TransportEvent,
    TransportEventKind,
    TransportProvider,
)
from providers.transport.media_endpoint import MediaEndpoint, MediaEndpointError
from providers.transport.webrtc import (
    INBOUND_CAPACITY_MS,
    NOMINAL_FRAME_MS,
    OUTBOUND_CAPACITY_MS,
    WebRTCTransport,
)

SESSION = "ses_rtc"


def _pcm(seq: int = 0, size: int = 320) -> AudioFrame:
    return AudioFrame(
        data=bytes(size),
        sample_rate=48_000,
        encoding=AudioEncoding.PCM16,
        channels=1,
        captured_at=float(seq),
        seq=seq,
    )


class FakeEndpoint:
    """A deterministic `MediaEndpoint`. The whole low-level media path, under test control."""

    def __init__(self, *, frames: list[AudioFrame] | None = None) -> None:
        self.frames = frames if frames is not None else []
        self.sent: list[AudioFrame] = []
        self.connected: list[str] = []
        self.closed: list[str] = []
        self.connect_error: Exception | None = None
        self.inbound_error: Exception | None = None
        self.send_error: Exception | None = None
        #: Held open until the test releases it, so "the media path is still live" is a state the
        #: test controls rather than a race it hopes for.
        self.hold = asyncio.Event()
        self.hold_after_frames = False
        self.started = asyncio.Event()

    async def connect(self, session_id: str) -> None:
        if self.connect_error is not None:
            raise self.connect_error
        self.connected.append(session_id)

    async def close(self, session_id: str) -> None:
        self.closed.append(session_id)

    async def inbound(self, session_id: str):
        self.started.set()
        for frame in self.frames:
            yield frame
        if self.inbound_error is not None:
            raise self.inbound_error
        if self.hold_after_frames:
            await self.hold.wait()

    async def send(self, session_id: str, frame: AudioFrame) -> None:
        if self.send_error is not None:
            raise self.send_error
        self.sent.append(frame)


def _recorder() -> tuple[list[TransportEvent], object]:
    events: list[TransportEvent] = []
    return events, events.append


async def _drain(transport: WebRTCTransport, session: str = SESSION) -> list[AudioFrame]:
    return [f async for f in transport.inbound(session_id=session)]


# ── A. construction and contract conformance ────────────────────────────────


def test_the_transport_implements_the_unchanged_capability_contract():
    transport = WebRTCTransport(FakeEndpoint())
    assert isinstance(transport, TransportProvider)
    assert transport.name == "webrtc"
    assert "audio" in transport.capabilities.media_kinds


def test_the_transport_can_report_reality():
    """AR-6 is opt-in and **structural** — `LocalTransport` does not implement it and emits
    nothing, which is why the local profile is unaffected by this milestone."""
    from providers.transport.local import LocalTransport

    assert isinstance(WebRTCTransport(FakeEndpoint()), ObservableTransport)
    assert not isinstance(LocalTransport(), ObservableTransport)


def test_the_fake_satisfies_the_media_endpoint_protocol():
    assert isinstance(FakeEndpoint(), MediaEndpoint)


# ── B / C. start lifecycle and the CONNECTED fact ───────────────────────────


@pytest.mark.asyncio
async def test_start_connects_the_endpoint_then_reports_connected():
    endpoint = FakeEndpoint()
    events, observer = _recorder()
    transport = WebRTCTransport(endpoint)
    transport.observe(SESSION, observer)

    await transport.start(session_id=SESSION)
    assert endpoint.connected == [SESSION]
    assert [e.kind for e in events] == [TransportEventKind.CONNECTED]
    assert transport.is_connected(SESSION)
    await transport.stop(session_id=SESSION)


@pytest.mark.asyncio
async def test_repeated_start_does_not_report_a_second_connection():
    endpoint = FakeEndpoint()
    events, observer = _recorder()
    transport = WebRTCTransport(endpoint)
    transport.observe(SESSION, observer)

    await transport.start(session_id=SESSION)
    await transport.start(session_id=SESSION)
    assert endpoint.connected == [SESSION]
    assert [e.kind for e in events].count(TransportEventKind.CONNECTED) == 1
    await transport.stop(session_id=SESSION)


# ── D. MEDIA_READY only for actual media ────────────────────────────────────


@pytest.mark.asyncio
async def test_media_ready_is_not_emitted_merely_because_start_was_called():
    """**The AR-6 assertion that matters most.** A connection can complete with nothing flowing;
    reporting readiness there would tell the runtime a call is working before a frame exists."""
    endpoint = FakeEndpoint(frames=[])
    endpoint.hold_after_frames = True
    events, observer = _recorder()
    transport = WebRTCTransport(endpoint)
    transport.observe(SESSION, observer)

    await transport.start(session_id=SESSION)
    await endpoint.started.wait()  # the reader is running and has produced nothing
    assert [e.kind for e in events] == [TransportEventKind.CONNECTED]
    assert not transport.is_media_ready(SESSION)

    endpoint.hold.set()
    await transport.stop(session_id=SESSION)


@pytest.mark.asyncio
async def test_media_ready_is_emitted_on_the_first_real_frame():
    endpoint = FakeEndpoint(frames=[_pcm(0), _pcm(1)])
    events, observer = _recorder()
    transport = WebRTCTransport(endpoint)
    transport.observe(SESSION, observer)

    await transport.start(session_id=SESSION)
    received = await _drain(transport)

    assert len(received) == 2
    kinds = [e.kind for e in events]
    assert kinds[:2] == [TransportEventKind.CONNECTED, TransportEventKind.MEDIA_READY]
    assert kinds.count(TransportEventKind.MEDIA_READY) == 1, "readiness reported more than once"
    await transport.stop(session_id=SESSION)


# ── E / G. disconnect and ordering ──────────────────────────────────────────


@pytest.mark.asyncio
async def test_the_full_reality_ordering_is_connected_media_ready_disconnected():
    endpoint = FakeEndpoint(frames=[_pcm(0)])
    events, observer = _recorder()
    transport = WebRTCTransport(endpoint)
    transport.observe(SESSION, observer)

    await transport.start(session_id=SESSION)
    await _drain(transport)

    assert [e.kind for e in events] == [
        TransportEventKind.CONNECTED,
        TransportEventKind.MEDIA_READY,
        TransportEventKind.DISCONNECTED,
    ]
    await transport.stop(session_id=SESSION)


@pytest.mark.asyncio
async def test_a_media_path_that_ends_without_a_frame_is_a_disconnect_not_a_failure():
    endpoint = FakeEndpoint(frames=[])
    events, observer = _recorder()
    transport = WebRTCTransport(endpoint)
    transport.observe(SESSION, observer)

    await transport.start(session_id=SESSION)
    assert await _drain(transport) == []
    kinds = [e.kind for e in events]
    assert TransportEventKind.DISCONNECTED in kinds
    assert TransportEventKind.FAILED not in kinds
    await transport.stop(session_id=SESSION)


# ── F. failure, and its separation from disconnect ──────────────────────────


@pytest.mark.asyncio
async def test_a_failed_connect_reports_failed_and_never_connected():
    endpoint = FakeEndpoint()
    endpoint.connect_error = MediaEndpointError("ICE failed")
    events, observer = _recorder()
    transport = WebRTCTransport(endpoint)
    transport.observe(SESSION, observer)

    with pytest.raises(ProviderUnavailable):
        await transport.start(session_id=SESSION)

    assert [e.kind for e in events] == [TransportEventKind.FAILED]
    assert not transport.is_connected(SESSION)


@pytest.mark.asyncio
async def test_a_broken_media_path_is_failed_not_disconnected():
    """A fault and a hangup are different real-world facts and must not collapse into one.

    **Changed at M11.14, and strengthened rather than relaxed.** This test asserted only the
    *reality event*, and passed while the inbound **stream** still ended with the very sentinel a
    clean hangup produces — so the runtime, which reads the stream and not the event, recorded a
    completed call. Both halves of the fact are now asserted: `FAILED` on the seam, and a raised
    `ProviderStreamTruncated` on the stream the pipeline actually consumes (ADR-0045 SD4, AR-6).
    Every assertion that held before still holds."""
    endpoint = FakeEndpoint(frames=[_pcm(0)])
    endpoint.inbound_error = MediaEndpointError("peer reset")
    events, observer = _recorder()
    transport = WebRTCTransport(endpoint)
    transport.observe(SESSION, observer)

    await transport.start(session_id=SESSION)
    with pytest.raises(ProviderStreamTruncated):
        await _drain(transport)

    kinds = [e.kind for e in events]
    assert TransportEventKind.FAILED in kinds
    assert TransportEventKind.DISCONNECTED not in kinds
    await transport.stop(session_id=SESSION)


@pytest.mark.asyncio
async def test_an_unmapped_library_error_is_still_a_failure_not_exhaustion():
    """ADR-0045 SD4 / AR-6 — the pipeline reads stream end as caller hangup, so an unmapped
    exception must never arrive as an ordinary end of stream.

    **Changed at M11.14 to assert what this docstring already claimed.** The sentence above was
    true of the *event* and false of the *stream*: the runtime reads the stream, and the stream
    ended exactly as a hangup does. The raise below is the missing half."""
    endpoint = FakeEndpoint(frames=[])
    endpoint.inbound_error = RuntimeError("library exploded")
    events, observer = _recorder()
    transport = WebRTCTransport(endpoint)
    transport.observe(SESSION, observer)

    await transport.start(session_id=SESSION)
    with pytest.raises(ProviderStreamTruncated):
        await _drain(transport)

    assert TransportEventKind.FAILED in [e.kind for e in events]
    await transport.stop(session_id=SESSION)


@pytest.mark.asyncio
async def test_a_failed_send_reports_failure_and_raises_typed():
    endpoint = FakeEndpoint()
    endpoint.send_error = MediaEndpointError("track gone")
    events, observer = _recorder()
    transport = WebRTCTransport(endpoint)
    transport.observe(SESSION, observer)

    await transport.start(session_id=SESSION)
    with pytest.raises(ProviderUnavailable):
        await transport.send(session_id=SESSION, frame=_pcm())
    assert TransportEventKind.FAILED in [e.kind for e in events]
    await transport.stop(session_id=SESSION)


# ── H / I. inbound media becomes truthful AudioFrames ───────────────────────


@pytest.mark.asyncio
async def test_inbound_media_arrives_as_audio_frames_with_truthful_metadata():
    frames = [_pcm(seq=i) for i in range(3)]
    transport = WebRTCTransport(FakeEndpoint(frames=frames))
    await transport.start(session_id=SESSION)
    received = await _drain(transport)

    assert [f.seq for f in received] == [0, 1, 2], "frame order was not preserved"
    for frame in received:
        assert isinstance(frame, AudioFrame)
        assert frame.encoding is AudioEncoding.PCM16, "real media must not claim to be OPAQUE"
        assert frame.sample_rate == 48_000
        assert frame.channels == 1
        assert frame.captured_at is not None
    await transport.stop(session_id=SESSION)


@pytest.mark.asyncio
async def test_the_transport_does_not_alter_frames_it_carries():
    """The transport moves media; it does not convert it. Resampling is the runtime's, at exactly
    two points (ADR-0044 AD4)."""
    original = _pcm(seq=7)
    transport = WebRTCTransport(FakeEndpoint(frames=[original]))
    await transport.start(session_id=SESSION)
    received = await _drain(transport)
    assert received == [original]
    await transport.stop(session_id=SESSION)


# ── J. outbound reaches the media boundary ──────────────────────────────────


@pytest.mark.asyncio
async def test_outbound_frames_reach_the_endpoint_in_order():
    endpoint = FakeEndpoint()
    transport = WebRTCTransport(endpoint)
    await transport.start(session_id=SESSION)

    for i in range(5):
        await transport.send(session_id=SESSION, frame=_pcm(seq=i))

    assert [f.seq for f in endpoint.sent] == [0, 1, 2, 3, 4]
    await transport.stop(session_id=SESSION)


@pytest.mark.asyncio
async def test_sending_before_start_is_refused_rather_than_silently_dropped():
    transport = WebRTCTransport(FakeEndpoint())
    with pytest.raises(ProviderUnavailable):
        await transport.send(session_id=SESSION, frame=_pcm())


@pytest.mark.asyncio
async def test_sending_after_stop_is_refused():
    transport = WebRTCTransport(FakeEndpoint())
    await transport.start(session_id=SESSION)
    await transport.stop(session_id=SESSION)
    with pytest.raises(ProviderUnavailable):
        await transport.send(session_id=SESSION, frame=_pcm())


# ── K / L / M. bounded buffering ────────────────────────────────────────────


def test_capacities_are_declared_in_milliseconds_not_frame_counts():
    """ADR-0044 AD6 — a frame count is meaningless across sample rates and encodings."""
    assert INBOUND_CAPACITY_MS > 0
    assert OUTBOUND_CAPACITY_MS > 0
    assert NOMINAL_FRAME_MS > 0


@pytest.mark.asyncio
async def test_the_inbound_queue_is_bounded_and_drops_oldest():
    """AR-40: no unbounded media queue. A producer that outruns a stalled consumer must not
    accumulate audio without limit — it drops the **stale** end, and counts it."""
    depth = INBOUND_CAPACITY_MS // NOMINAL_FRAME_MS
    overflow = depth + 25
    endpoint = FakeEndpoint(frames=[_pcm(seq=i) for i in range(overflow)])
    transport = WebRTCTransport(endpoint)

    await transport.start(session_id=SESSION)
    received = await _drain(transport)

    assert transport.dropped(SESSION) > 0, "an overflowing queue dropped nothing"
    assert len(received) <= depth + 1
    # DROP_OLDEST: the freshest frame survives, the earliest do not.
    assert received[-1].seq == overflow - 1
    assert received[0].seq > 0
    await transport.stop(session_id=SESSION)


@pytest.mark.asyncio
async def test_outbound_never_silently_drops():
    """The other half of AD7. A gap mid-sentence is audible, so outbound applies back-pressure —
    every frame handed in is a frame that reached the media boundary."""
    endpoint = FakeEndpoint()
    transport = WebRTCTransport(endpoint)
    await transport.start(session_id=SESSION)

    count = (OUTBOUND_CAPACITY_MS // NOMINAL_FRAME_MS) * 3
    for i in range(count):
        await transport.send(session_id=SESSION, frame=_pcm(seq=i))

    assert len(endpoint.sent) == count, "outbound audio was dropped"
    assert [f.seq for f in endpoint.sent] == list(range(count))
    await transport.stop(session_id=SESSION)


def test_no_unbounded_queue_exists_in_the_webrtc_media_path():
    source = pathlib.Path(inspect.getfile(WebRTCTransport)).read_text(encoding="utf-8")
    assert "asyncio.Queue()" not in source
    assert "maxsize=" in source


# ── N / O / P. cancellation and deterministic teardown ──────────────────────


@pytest.mark.asyncio
async def test_stop_is_deterministic_and_releases_the_endpoint():
    endpoint = FakeEndpoint(frames=[])
    endpoint.hold_after_frames = True
    transport = WebRTCTransport(endpoint)

    await transport.start(session_id=SESSION)
    await endpoint.started.wait()
    await transport.stop(session_id=SESSION)

    assert endpoint.closed == [SESSION]
    assert not transport.is_connected(SESSION)


@pytest.mark.asyncio
async def test_no_transport_task_survives_shutdown():
    """§A11.1.7 — a reader that outlived its transport would hold a peer connection open."""
    endpoint = FakeEndpoint(frames=[])
    endpoint.hold_after_frames = True
    transport = WebRTCTransport(endpoint)

    await transport.start(session_id=SESSION)
    await endpoint.started.wait()
    pump = transport._sessions[SESSION].pump  # noqa: SLF001 — asserting task ownership
    assert pump is not None and not pump.done()

    await transport.stop(session_id=SESSION)
    assert pump.done(), "the inbound reader survived shutdown"
    assert transport._sessions[SESSION].pump is None  # noqa: SLF001


@pytest.mark.asyncio
async def test_repeated_stop_is_safe():
    endpoint = FakeEndpoint(frames=[])
    endpoint.hold_after_frames = True
    transport = WebRTCTransport(endpoint)

    await transport.start(session_id=SESSION)
    await endpoint.started.wait()
    await transport.stop(session_id=SESSION)
    await transport.stop(session_id=SESSION)
    await transport.stop(session_id=SESSION)
    assert endpoint.closed == [SESSION], "teardown ran more than once"


@pytest.mark.asyncio
async def test_stop_unblocks_a_waiting_inbound_consumer():
    """A consumer parked on an empty queue must be released, not left awaiting a dead session."""
    endpoint = FakeEndpoint(frames=[])
    endpoint.hold_after_frames = True
    transport = WebRTCTransport(endpoint)
    await transport.start(session_id=SESSION)
    await endpoint.started.wait()

    consumer = asyncio.create_task(_drain(transport))
    await transport.stop(session_id=SESSION)
    assert await asyncio.wait_for(consumer, timeout=5) == []


@pytest.mark.asyncio
async def test_cancelling_start_is_cancellation_not_a_transport_failure():
    """ADR-0046 rule 2 — the caller withdrawing is not the transport misbehaving."""

    class _Slow(FakeEndpoint):
        async def connect(self, session_id: str) -> None:
            await asyncio.Event().wait()

    endpoint = _Slow()
    events, observer = _recorder()
    transport = WebRTCTransport(endpoint)
    transport.observe(SESSION, observer)

    task = asyncio.create_task(transport.start(session_id=SESSION))
    await asyncio.sleep(0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert events == [], "cancellation was reported as a transport fact"


# ── Q / R. AR-23 and AR-24 integration ──────────────────────────────────────


@pytest.mark.asyncio
async def test_transport_failures_map_into_the_typed_taxonomy():
    """AR-23: the runtime receives typed provider errors, never a library exception."""
    endpoint = FakeEndpoint()
    endpoint.connect_error = MediaEndpointError("no route")
    transport = WebRTCTransport(endpoint)

    with pytest.raises(ProviderUnavailable) as caught:
        await transport.start(session_id=SESSION)
    assert isinstance(caught.value.__cause__, MediaEndpointError)


@pytest.mark.asyncio
async def test_the_guard_wraps_the_real_transport_exactly_as_it_wraps_the_local_one():
    """The fault-tolerance layer is capability-shaped, so a real transport needs no special case."""
    from runtime.faults import DegradationRecorder, ProviderGuard

    recorder = DegradationRecorder(session_id=SESSION)
    guard = ProviderGuard(capability="transport", degradations=recorder)
    endpoint = FakeEndpoint()
    endpoint.connect_error = MediaEndpointError("down")
    transport = WebRTCTransport(endpoint)

    with pytest.raises(ProviderUnavailable):
        await guard.call(
            transport.name, lambda: transport.start(session_id=SESSION), what="start"
        )
    assert recorder.history, "a transport failure produced no degradation record (AR-24)"


@pytest.mark.asyncio
async def test_an_observer_that_raises_never_breaks_the_transport():
    """DL-0013 — observability is auxiliary."""

    def _explode(_event: TransportEvent) -> None:
        raise RuntimeError("observer down")

    transport = WebRTCTransport(FakeEndpoint(frames=[_pcm()]))
    transport.observe(SESSION, _explode)
    await transport.start(session_id=SESSION)
    assert len(await _drain(transport)) == 1
    await transport.stop(session_id=SESSION)


# ── S–X. architecture ───────────────────────────────────────────────────────

_TRANSPORT_DIR = pathlib.Path(inspect.getfile(WebRTCTransport)).parent
_PLATFORM_ROOT = _TRANSPORT_DIR.parent.parent


def _imports(path: pathlib.Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    roots: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            roots.add(node.module.split(".")[0])
        elif isinstance(node, ast.Import):
            roots.update(a.name.split(".")[0] for a in node.names)
    return roots


def _production_files() -> list[pathlib.Path]:
    packages = ("core", "api", "runtime", "providers", "events", "observability")
    return [
        p
        for package in packages
        for p in (_PLATFORM_ROOT / package).rglob("*.py")
        if "__pycache__" not in p.parts
    ]


def test_no_pipecat_import_exists_anywhere_in_the_production_path():
    """P-ADR-0002 — the substrate stays retired. AR-13 remains enforceable."""
    for path in _production_files():
        assert "pipecat" not in _imports(path), path.relative_to(_PLATFORM_ROOT)


#: The modules permitted to import aiortc. **AR-12's rule is that the SDK stays inside its
#: plugin**, and both of these are `providers/transport/` — the plugin itself.
#:
#: `signalling.py` joined at **M11.16.3**, and the interface forced it: M11.5 defined the seam as
#: `signaller.negotiate(session_id, pc) -> track`, so the signaller *drives the peer connection*
#: and must construct exactly one library type (`RTCSessionDescription`) to apply the remote
#: answer. Keeping it out would have meant changing M11.5's interface, which that milestone's
#: contract preserves. Nothing outside this package may import the library, and the assertion
#: below is what keeps that true.
_AIORTC_RENTERS = frozenset({"aiortc_endpoint.py", "signalling.py"})


def test_aiortc_is_imported_only_by_the_dedicated_endpoint_module():
    """**AR-12, asserted.** The transport plugin rents the library; nothing else may *import* it.

    Read from the AST, not the text: several modules legitimately **name** aiortc in prose to
    state where the boundary is, and a guard that cannot tell a docstring from an import fails on
    its own documentation — a trap this repository has sprung more than once."""
    offenders = []
    for path in _production_files():
        if "aiortc" not in _imports(path):
            continue
        if path.name not in _AIORTC_RENTERS:
            offenders.append(str(path.relative_to(_PLATFORM_ROOT)))
    assert offenders == [], f"aiortc leaked outside its plugin module: {offenders}"


def test_every_aiortc_renter_lives_inside_the_transport_plugin():
    """The property the widening must not lose: AR-12 is about the *plugin* boundary, so an
    admitted module that drifted out of `providers/transport/` would be a real violation."""
    for path in _production_files():
        if "aiortc" in _imports(path):
            assert path.parent == _TRANSPORT_DIR, (
                f"{path.name} imports aiortc from outside the transport plugin"
            )


def test_the_endpoint_module_does_import_aiortc():
    """The other half — a boundary guard is only evidence if the thing it bounds exists."""
    endpoint = _TRANSPORT_DIR / "aiortc_endpoint.py"
    source = endpoint.read_text(encoding="utf-8")
    assert "from aiortc import" in source, "the endpoint no longer rents the library"


def test_no_aiortc_type_crosses_the_core_contracts():
    for path in (_PLATFORM_ROOT / "core").rglob("*.py"):
        if "__pycache__" in path.parts:
            continue
        assert "aiortc" not in _imports(path), path.name
        assert "av" not in _imports(path), path.name


def test_the_webrtc_transport_itself_is_library_free():
    """The transport is first-party: lifecycle, AR-6 facts and buffering owe nothing to aiortc."""
    path = pathlib.Path(inspect.getfile(WebRTCTransport))
    assert "aiortc" not in _imports(path)
    assert "av" not in _imports(path)


def test_the_orchestrator_constructs_no_transport():
    """AR-20 — provider provisioning belongs to the registry, not the orchestration layer."""
    from runtime.orchestrator import orchestrator as orch

    source = pathlib.Path(inspect.getfile(orch)).read_text(encoding="utf-8")
    assert "WebRTCTransport(" not in source
    assert "LocalTransport(" not in source


def test_the_transport_constructs_no_other_capability():
    source = pathlib.Path(inspect.getfile(WebRTCTransport)).read_text(encoding="utf-8")
    for forbidden in ("ProviderRegistry", "SessionController", "ConversationOrchestrator",
                      "LocalSTT", "LocalTTS", "EchoLLM"):
        assert forbidden not in source, forbidden


def test_there_is_still_exactly_one_resampler_owner():
    owners = set()
    for path in _production_files():
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef)
                and "resampl" in node.name.lower()
            ):
                owners.add(str(path.relative_to(_PLATFORM_ROOT)))
    assert owners and all(o.startswith("runtime/pipeline/audio/") for o in owners), owners


def test_there_is_still_exactly_one_audio_normalizer():
    declarations = [
        str(path.relative_to(_PLATFORM_ROOT))
        for path in _production_files()
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8")))
        if isinstance(node, ast.ClassDef) and node.name == "AudioNormalizer"
    ]
    assert declarations == ["runtime/pipeline/audio/resampler.py"], declarations


def test_the_epic10_durable_spine_is_wired_without_the_runtime_acquiring_storage():
    """ADR-0050 SW1, **retargeted at M11.12** — the milestone this guard named as its own expiry.

    Its original text said production wiring *"is M11.12's, and M11.5 did not anticipate it"*, so
    this is the guard doing exactly what it was written to do: hold the line until the authorized
    milestone, then be updated by it. The half that M11.5 actually owns still holds — the
    transport did not wire anything, the runtime names no storage class, and one bus is built."""
    orchestrator = (_PLATFORM_ROOT / "runtime" / "orchestrator" / "orchestrator.py").read_text(
        encoding="utf-8"
    )
    assert orchestrator.count("InProcessEventBus(") == 1
    assert "SqlDurableEventSpine" not in orchestrator


def test_no_second_provider_registry_was_introduced():
    declarations = [
        str(path.relative_to(_PLATFORM_ROOT))
        for path in _production_files()
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8")))
        if isinstance(node, ast.ClassDef) and node.name == "ProviderRegistry"
    ]
    assert declarations == ["runtime/registry.py"], declarations


def test_a_decoded_plane_is_sliced_to_the_samples_it_actually_holds():
    """M11.16.9: `av` allocates a plane larger than the decoded audio.

    A 960-sample stereo frame arrives in a **23040-byte** buffer of which only **3840** bytes are
    audio; `bytes(plane)` returns the whole allocation, so five-sixths of every frame was stale
    memory. The signal level stayed plausible, which is exactly why it survived: the stream was
    unintelligible to Deepgram while looking healthy in every RMS check.

    Asserted against a stand-in that reproduces the over-allocation, so the guard holds without
    needing a live peer."""
    from core.audio import AudioEncoding
    from providers.transport.aiortc_endpoint import AiortcMediaEndpoint

    class _Layout:
        channels = (object(), object())  # stereo

    class _Plane:
        def __init__(self, payload: bytes) -> None:
            self._payload = payload

        def __bytes__(self) -> bytes:
            return self._payload

    class _Format:
        name = "s16"

    class _Frame:
        format = _Format()
        layout = _Layout()
        samples = 960
        sample_rate = 48000

        def __init__(self) -> None:
            valid = b"\x11\x22" * 960 * 2          # 3840 bytes of real audio
            garbage = b"\x7f\x7f" * 960 * 2 * 5    # 19200 bytes of stale buffer
            self.planes = (_Plane(valid + garbage),)

    endpoint = AiortcMediaEndpoint.__new__(AiortcMediaEndpoint)
    endpoint._seq = {}  # noqa: SLF001 — the only state `_to_audio_frame` reads

    out = endpoint._to_audio_frame("s", _Frame())  # noqa: SLF001

    assert out.encoding is AudioEncoding.PCM16
    assert out.channels == 2 and out.sample_rate == 48000
    assert len(out.data) == 960 * 2 * 2, "the plane must be sliced to samples * channels * 2"
    assert b"\x7f\x7f" not in out.data, "stale buffer beyond the valid samples leaked through"
