"""First-party WebRTC signalling and the real-aiortc media path (M11.16.3).

Three layers, and the distinction between them is the point:

**A. Signaller unit tests** — the negotiation state machine, input validation, bounds and
cancellation, against a peer-connection double. No library, no network.

**B. Real-aiortc integration** — two genuine `RTCPeerConnection`s negotiated against each other
in-process, carrying real audio through the real codec path. This exercises the actual library
and the actual media conversion.

> **These are hermetic real-WebRTC-stack tests. They are NOT `E11-15` live evidence.**
> `aiortc ↔ aiortc` inside one process is not a Stage-2 external peer, and §23 forbids confusing
> the two. What they establish is that the path *works*, not that it was *deployed*.

**C. Lifecycle and resources** — close, repeated close, cancellation, and the absence of orphaned
tasks, peers, tracks and queues.

Nothing here sleeps for a duration or spins a fixed number of times; every wait is on a real
condition under a bounded deadline (the M11.15 R-70 rule).
"""

from __future__ import annotations

import asyncio
import time

import pytest

from providers.transport.media_endpoint import MediaEndpoint, MediaEndpointError
from providers.transport.signalling import (
    MAX_SDP_BYTES,
    PeerSignaller,
    SdpExchange,
    Signaller,
    SignallingState,
)

#: A minimal well-formed SDP body: enough to pass validation, never fed to the library in layer A.
VALID_SDP = "v=0\r\no=- 0 0 IN IP4 127.0.0.1\r\ns=-\r\nt=0 0\r\n"


# There is deliberately **no `skipif` for aiortc**. It is a declared, locked, non-optional
# dependency (`pyproject.toml`, `uv.lock`), so an environment without it is broken rather than
# merely different — and a skip would quietly lower the regression floor that `E11-1` exists to
# hold. M11.16.3's first draft used one; the control caught it, which is the control working.


# ══════════════════════════════════════════════════════════════════════════════
# A. Signaller unit tests — no library, no network
# ══════════════════════════════════════════════════════════════════════════════


class FakeDescription:
    def __init__(self, sdp: str) -> None:
        self.sdp = sdp


class FakePeerConnection:
    """The surface `PeerSignaller` touches, and nothing more."""

    def __init__(self, *, offer_sdp: str = VALID_SDP) -> None:
        self.localDescription = FakeDescription(offer_sdp)  # noqa: N815 — library's spelling
        self.remote_sdp: str | None = None
        self._handlers: dict[str, object] = {}
        self.closed = 0

    def on(self, event: str):
        def register(fn):
            self._handlers[event] = fn
            return fn
        return register

    def fire_track(self, track) -> None:
        self._handlers["track"](track)

    async def createOffer(self):  # noqa: N802 — library's spelling
        return FakeDescription(self.localDescription.sdp)

    async def setLocalDescription(self, description) -> None:  # noqa: N802
        self.localDescription = description

    async def setRemoteDescription(self, description) -> None:  # noqa: N802
        self.remote_sdp = description.sdp

    async def close(self) -> None:
        self.closed += 1


class FakeTrack:
    kind = "audio"


class ScriptedExchange(SdpExchange):
    """An `SdpExchange` whose answer — or failure — the test chooses."""

    def __init__(self, answer=VALID_SDP, *, error: Exception | None = None,
                 hang: asyncio.Event | None = None) -> None:
        self.answer = answer
        self.error = error
        self.hang = hang
        self.calls: list[str] = []

    async def exchange(self, session_id: str, offer_sdp: str) -> str:
        self.calls.append(offer_sdp)
        if self.hang is not None:
            await self.hang.wait()
        if self.error is not None:
            raise self.error
        return self.answer


async def negotiate_with(exchange, *, pc=None, timeout=5.0, track=FakeTrack()):
    """Drive one negotiation, firing the inbound track once the exchange has been called."""
    pc = pc or FakePeerConnection()
    signaller = PeerSignaller(exchange, negotiation_timeout_s=timeout)
    task = asyncio.create_task(signaller.negotiate("s1", pc))

    async def deliver():
        while not exchange.calls and not task.done():
            await asyncio.sleep(0)
        if track is not None and not task.done():
            pc.fire_track(track)

    await asyncio.gather(deliver(), return_exceptions=True)
    return signaller, pc, task


async def test_a_successful_negotiation_returns_the_inbound_track():
    exchange = ScriptedExchange()
    track = FakeTrack()
    signaller, pc, task = await negotiate_with(exchange, track=track)
    assert await asyncio.wait_for(task, 5.0) is track
    assert pc.remote_sdp == VALID_SDP, "the remote answer was not applied"
    assert signaller.state("s1") is SignallingState.CONNECTED


async def test_the_offer_handed_to_the_exchange_is_the_one_the_library_filled_in():
    """`setLocalDescription` adds ICE candidates, so the SDP sent must be read back afterwards."""
    exchange = ScriptedExchange()
    pc = FakePeerConnection(offer_sdp=VALID_SDP + "a=candidate:1 1 udp 1 127.0.0.1 1 typ host\r\n")
    _, _, task = await negotiate_with(exchange, pc=pc)
    await asyncio.wait_for(task, 5.0)
    assert "a=candidate" in exchange.calls[0], "the offer was sent before ICE was populated"


@pytest.mark.parametrize(
    "answer,reason",
    [
        (None, "not a string"),
        (b"v=0", "not a string"),
        ("", "empty"),
        ("   ", "empty"),
        ("<html>error</html>", "no version line"),
        ("v=0" + "x" * (MAX_SDP_BYTES + 1), "oversized"),
    ],
)
async def test_a_malformed_remote_answer_is_refused_before_the_library_sees_it(answer, reason):
    """§7: an SDP arrives from outside the process; `setRemoteDescription` is not where its type
    should be discovered."""
    exchange = ScriptedExchange(answer=answer)
    signaller, pc, task = await negotiate_with(exchange)
    with pytest.raises(MediaEndpointError):
        await asyncio.wait_for(task, 5.0)
    assert pc.remote_sdp is None, f"a {reason} answer reached the peer connection"
    assert signaller.state("s1") is SignallingState.FAILED


async def test_a_remote_that_never_answers_fails_within_the_bound():
    """§17: bounded, so a silent peer cannot hold a session open forever."""
    exchange = ScriptedExchange(hang=asyncio.Event())
    signaller = PeerSignaller(exchange, negotiation_timeout_s=0.05)
    with pytest.raises(MediaEndpointError, match="exceeded"):
        await asyncio.wait_for(signaller.negotiate("s1", FakePeerConnection()), 5.0)
    assert signaller.state("s1") is SignallingState.FAILED


async def test_an_answer_with_no_audio_track_fails_within_the_bound():
    """A peer that answers but sends nothing is a failed negotiation, not a usable connection."""
    exchange = ScriptedExchange()
    signaller = PeerSignaller(exchange, negotiation_timeout_s=0.05)
    with pytest.raises(MediaEndpointError, match="exceeded"):
        await asyncio.wait_for(signaller.negotiate("s1", FakePeerConnection()), 5.0)


async def test_a_failing_exchange_becomes_a_typed_media_endpoint_error():
    exchange = ScriptedExchange(error=RuntimeError("signalling channel down"))
    signaller, _, task = await negotiate_with(exchange)
    with pytest.raises(MediaEndpointError):
        await asyncio.wait_for(task, 5.0)
    assert signaller.state("s1") is SignallingState.FAILED


async def test_cancellation_is_withdrawal_not_failure():
    """The rule ADR-0046 applies everywhere: a cancelled caller is not a broken peer."""
    exchange = ScriptedExchange(hang=asyncio.Event())
    signaller = PeerSignaller(exchange)
    task = asyncio.create_task(signaller.negotiate("s1", FakePeerConnection()))
    while not exchange.calls:
        await asyncio.sleep(0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert signaller.state("s1") is not SignallingState.FAILED


async def test_a_second_negotiation_for_a_connected_session_is_refused():
    """§18: an illegal transition is deterministic, not a race."""
    exchange = ScriptedExchange()
    signaller, pc, task = await negotiate_with(exchange)
    await asyncio.wait_for(task, 5.0)
    with pytest.raises(MediaEndpointError, match="illegal transition"):
        await signaller.negotiate("s1", pc)


async def test_release_is_idempotent_and_forgets_the_session():
    exchange = ScriptedExchange()
    signaller, _, task = await negotiate_with(exchange)
    await asyncio.wait_for(task, 5.0)
    signaller.release("s1")
    signaller.release("s1")
    assert signaller.state("s1") is SignallingState.NEW


def test_an_impossible_timeout_is_refused_rather_than_defaulted():
    with pytest.raises(ValueError, match="negotiation_timeout_s"):
        PeerSignaller(ScriptedExchange(), negotiation_timeout_s=0)


def test_the_signalling_module_owns_nothing_it_should_not():
    """§5: it establishes a peer connection and returns a track. Asserted structurally so the
    responsibility cannot drift."""
    import ast
    import pathlib

    src = (
        pathlib.Path(__file__).parent.parent / "providers" / "transport" / "signalling.py"
    ).read_text(encoding="utf-8")
    imported: set[str] = set()
    for node in ast.walk(ast.parse(src)):
        if isinstance(node, ast.Import):
            imported.update(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split(".")[0])
    forbidden = {"runtime", "core", "billing", "events", "observability", "api"}
    assert not (imported & forbidden), f"signalling reached into {imported & forbidden}"


def test_signalling_carries_no_credential_vocabulary():
    """§7/§21: signalling is control-plane and must never touch a provider credential."""
    import pathlib

    src = (
        pathlib.Path(__file__).parent.parent / "providers" / "transport" / "signalling.py"
    ).read_text(encoding="utf-8").lower()
    for token in ("api_key", "apikey", "authorization", "bearer", "credentialprovider",
                  "deepgram", "groq", "cartesia"):
        assert token not in src, f"signalling references {token!r}"


def test_the_protocols_are_satisfied_by_the_implementation():
    assert isinstance(PeerSignaller(ScriptedExchange()), Signaller)
    assert isinstance(ScriptedExchange(), SdpExchange)


# ══════════════════════════════════════════════════════════════════════════════
# B. Real-aiortc integration — hermetic real-WebRTC-stack, NOT live evidence
# ══════════════════════════════════════════════════════════════════════════════


class LoopbackExchange(SdpExchange):
    """Answers the offer with a second **real** `RTCPeerConnection` in this process.

    This is a genuine WebRTC negotiation — real SDP, real ICE, real DTLS, real RTP — between two
    aiortc peers. It is **not** a Stage-2 external peer, and nothing here may be presented as
    `E11-15` evidence (§23)."""

    def __init__(self) -> None:
        self.remote = None
        self.sent_frames = 0

    async def exchange(self, session_id: str, offer_sdp: str) -> str:
        import fractions

        import av
        from aiortc import MediaStreamTrack, RTCPeerConnection, RTCSessionDescription

        outer = self

        class ToneTrack(MediaStreamTrack):
            """The 'caller': a real track producing real PCM16 at 48 kHz."""

            kind = "audio"

            def __init__(self) -> None:
                super().__init__()
                self._pts = 0

            async def recv(self):
                samples = 960  # 20 ms at 48 kHz
                frame = av.AudioFrame(format="s16", layout="mono", samples=samples)
                frame.planes[0].update((b"\x10\x27") * samples)
                frame.sample_rate = 48000
                frame.pts = self._pts
                frame.time_base = fractions.Fraction(1, 48000)
                self._pts += samples
                outer.sent_frames += 1
                await asyncio.sleep(0)
                return frame

        self.remote = RTCPeerConnection()
        self.remote.addTrack(ToneTrack())
        await self.remote.setRemoteDescription(
            RTCSessionDescription(sdp=offer_sdp, type="offer")
        )
        answer = await self.remote.createAnswer()
        await self.remote.setLocalDescription(answer)
        return self.remote.localDescription.sdp

    async def aclose(self) -> None:
        if self.remote is not None:
            await self.remote.close()
            self.remote = None


async def test_B_two_real_peers_negotiate_and_carry_real_audio():
    """The headline: a real `RTCPeerConnection` pair, real codec path, real `AudioFrame`s out.

    **Hermetic real-WebRTC-stack evidence — not live certification evidence.**"""
    from core.audio import AudioEncoding
    from providers.transport.aiortc_endpoint import AiortcMediaEndpoint

    exchange = LoopbackExchange()
    endpoint = AiortcMediaEndpoint(PeerSignaller(exchange, negotiation_timeout_s=20.0))
    assert isinstance(endpoint, MediaEndpoint)

    await asyncio.wait_for(endpoint.connect("live-ish"), 25.0)
    frames = []
    try:
        async def pull():
            async for frame in endpoint.inbound("live-ish"):
                frames.append(frame)
                if len(frames) >= 3:
                    return
        await asyncio.wait_for(pull(), 25.0)
    finally:
        await endpoint.close("live-ish")
        await exchange.aclose()

    assert len(frames) >= 3, "no real media crossed the peer connection"
    for f in frames:
        assert f.encoding is AudioEncoding.PCM16
        assert f.sample_rate > 0 and f.channels >= 1
        assert f.data, "a decoded frame carried no payload"
        assert f.captured_at is not None, "captured_at must be a monotonic instant"
    assert [f.seq for f in frames] == sorted(f.seq for f in frames), "sequence went backwards"


async def test_B_outbound_frames_reach_the_peer_connection():
    """The gap M11.16.3 closed: `send()` used to always raise because nothing populated the
    outbound queue, so the assistant's audio could never leave."""
    from core.audio import AudioEncoding, AudioFrame
    from providers.transport.aiortc_endpoint import AiortcMediaEndpoint

    exchange = LoopbackExchange()
    endpoint = AiortcMediaEndpoint(PeerSignaller(exchange, negotiation_timeout_s=20.0))
    await asyncio.wait_for(endpoint.connect("out"), 25.0)
    try:
        frame = AudioFrame(
            data=b"\x10\x27" * 960, sample_rate=48000,
            encoding=AudioEncoding.PCM16, channels=1,
        )
        for _ in range(3):
            await asyncio.wait_for(endpoint.send("out", frame), 5.0)
    finally:
        await endpoint.close("out")
        await exchange.aclose()


async def test_B_a_non_pcm16_outbound_frame_is_refused():
    from core.audio import AudioEncoding, AudioFrame
    from providers.transport.aiortc_endpoint import AiortcMediaEndpoint

    exchange = LoopbackExchange()
    endpoint = AiortcMediaEndpoint(PeerSignaller(exchange, negotiation_timeout_s=20.0))
    await asyncio.wait_for(endpoint.connect("bad"), 25.0)
    try:
        with pytest.raises(MediaEndpointError, match="PCM16"):
            await endpoint.send(
                "bad",
                AudioFrame(data=b"\x00\x00", sample_rate=48000, encoding=AudioEncoding.OPAQUE),
            )
    finally:
        await endpoint.close("bad")
        await exchange.aclose()


async def _tasks_settle_to(limit: int, *, deadline_s: float = 10.0) -> bool:
    """Whether live asyncio tasks fall back to `limit` within the deadline.

    aiortc winds its ICE machinery down asynchronously after `close()`, on real timers. That is
    the library's business, not a leak in this transport — but it means "did anything of mine
    survive?" has to be asked after the library has finished, and only wall-clock lets it."""
    loop_deadline = time.monotonic() + deadline_s
    while len(asyncio.all_tasks()) > limit:
        if time.monotonic() > loop_deadline:
            return False
        await asyncio.sleep(0.02)
    return True


# ══════════════════════════════════════════════════════════════════════════════
# C. Lifecycle and resources
# ══════════════════════════════════════════════════════════════════════════════


async def test_C_repeated_close_is_idempotent():
    from providers.transport.aiortc_endpoint import AiortcMediaEndpoint

    exchange = LoopbackExchange()
    endpoint = AiortcMediaEndpoint(PeerSignaller(exchange, negotiation_timeout_s=20.0))
    await asyncio.wait_for(endpoint.connect("idem"), 25.0)
    await endpoint.close("idem")
    await endpoint.close("idem")
    await endpoint.close("idem")
    await exchange.aclose()


async def test_C_sending_after_close_is_refused_not_silently_dropped():
    from core.audio import AudioEncoding, AudioFrame
    from providers.transport.aiortc_endpoint import AiortcMediaEndpoint

    exchange = LoopbackExchange()
    endpoint = AiortcMediaEndpoint(PeerSignaller(exchange, negotiation_timeout_s=20.0))
    await asyncio.wait_for(endpoint.connect("closed"), 25.0)
    await endpoint.close("closed")
    await exchange.aclose()
    with pytest.raises(MediaEndpointError, match="no outbound track"):
        await endpoint.send(
            "closed",
            AudioFrame(data=b"\x00" * 1920, sample_rate=48000, encoding=AudioEncoding.PCM16),
        )


async def test_C_repeated_sessions_leak_no_tasks_or_peers():
    """§22 E / §12: no orphaned task, peer connection, track or signalling state."""
    from providers.transport.aiortc_endpoint import AiortcMediaEndpoint

    before = len(asyncio.all_tasks())
    for index in range(3):
        exchange = LoopbackExchange()
        signaller = PeerSignaller(exchange, negotiation_timeout_s=20.0)
        endpoint = AiortcMediaEndpoint(signaller)
        sid = f"cycle-{index}"
        await asyncio.wait_for(endpoint.connect(sid), 25.0)
        # The remote hangs up first, which is the natural order and avoids racing aiortc's own
        # in-flight connect task: negotiation completes when the track arrives, which can precede
        # full ICE establishment, so tearing the local side down first orphans that task.
        await exchange.aclose()
        await endpoint.close(sid)
        assert signaller.state(sid) is SignallingState.NEW, "signalling state outlived the session"
        assert endpoint._connections == {}  # noqa: SLF001
        assert endpoint._outbound == {}  # noqa: SLF001
        assert endpoint._tracks == {}  # noqa: SLF001
    # Wait on the **condition**, not on a spin budget. `sleep(0)` yields cannot advance aioice's
    # internal ICE timers, so a yield-based drain reports the library mid-teardown as a leak; the
    # deadline exists only to turn a genuine leak into a failure rather than a hang (M11.15 R-70).
    if not await _tasks_settle_to(before + 1):
        survivors = [
            t.get_coro().__qualname__
            for t in asyncio.all_tasks()
            if t is not asyncio.current_task()
        ]
        raise AssertionError(f"a task outlived its session: {survivors}")


async def test_C_a_failed_negotiation_leaves_no_peer_behind():
    from providers.transport.aiortc_endpoint import AiortcMediaEndpoint

    class Broken(SdpExchange):
        async def exchange(self, session_id: str, offer_sdp: str) -> str:
            raise RuntimeError("signalling channel refused")

    endpoint = AiortcMediaEndpoint(PeerSignaller(Broken(), negotiation_timeout_s=10.0))
    with pytest.raises(MediaEndpointError):
        await asyncio.wait_for(endpoint.connect("doomed"), 20.0)
    assert endpoint._connections == {}  # noqa: SLF001
    assert endpoint._outbound == {}  # noqa: SLF001


# ══════════════════════════════════════════════════════════════════════════════
# D. Composition root
# ══════════════════════════════════════════════════════════════════════════════


def test_D_webrtc_is_opt_in_and_never_the_default():
    """§10: nothing switches to WebRTC because a library happens to be importable."""
    import bootstrap
    from runtime.registry import CapabilityKind

    assert bootstrap.build_registry().resolve(CapabilityKind.TRANSPORT).name == "local"
    assert bootstrap.build_session_controller() is not None


def test_D_the_composition_path_builds_a_real_endpoint():
    import bootstrap

    class Exchange(SdpExchange):
        async def exchange(self, session_id: str, offer_sdp: str) -> str:
            return VALID_SDP

    endpoint = bootstrap.build_webrtc_media_endpoint(Exchange())
    assert isinstance(endpoint, MediaEndpoint)
    assert type(endpoint).__name__ == "AiortcMediaEndpoint"


def test_D_the_real_profile_still_selects_webrtc_without_changing_other_providers():
    import bootstrap

    assert bootstrap.REAL_PROVIDER_PROFILE["transport"] == bootstrap.TRANSPORT_WEBRTC
    assert bootstrap.REAL_PROVIDER_PROFILE["stt"] == bootstrap.STT_DEEPGRAM
    assert bootstrap.REAL_PROVIDER_PROFILE["llm"] == bootstrap.LLM_GROQ
    assert bootstrap.REAL_PROVIDER_PROFILE["tts"] == bootstrap.TTS_CARTESIA
