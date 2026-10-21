"""Abrupt peer loss driven through a **real** aiortc peer pair (M13.1 R1).

`test_webrtc_peer_loss.py` asserts R1's wiring and its AR-6 semantics against a peer-connection
double, which is the right shape for the wiring but leaves one gap: nothing anywhere proved that a
**real** `RTCPeerConnection` reaching `connectionState == "failed"` actually fires
`connectionstatechange` and actually reaches R1's watcher. Every `connectionState` in the
repository was, until this file, either our own `FakePeerConnection` or browser-side JavaScript.

This file closes exactly that gap, and nothing wider:

* the local peer is a real `RTCPeerConnection` built by the real `AiortcMediaEndpoint`;
* the remote peer is the real second `RTCPeerConnection` that `LoopbackExchange` already creates;
* the failure is produced by **severing the remote peer's ICE transport** through public API
  (`RTCIceTransport.stop()`), never by closing its peer connection — a `close()` would send DTLS
  `close_notify`, which is the *clean* path that already worked;
* the local side then declares failure by its own consent-freshness machinery, which is the
  production mechanism: aioice's probes go unanswered, `Connection.close()` emits
  `ConnectionClosed`, and `RTCIceTransport._monitor` promotes `completed` to `failed`.

No library internals are touched: every hop is a documented public property or method, and nothing
is monkeypatched. The only private reads are of our **own** endpoint's state (`_connections` to
register an independent observer, `_faults`/`_closers` to assert nothing leaked), following the
convention the sibling suites already use.

## What this establishes

That the real library emits the real event with the real state, that R1 is wired to it, and that
the media path therefore leaves as `FAILED` rather than `DISCONNECTED` — with no orphaned task and
no unconsumed fault.

## What it does NOT establish

Nothing about **Chrome** or any browser. Nothing about **real-network, NAT, CGNAT or carrier**
behaviour: `aiortc` ↔ `aiortc` inside one process is **not** a Stage-2 external peer, and §23
forbids confusing the two (see `test_webrtc_signalling.py`'s own header). Nothing about detection
**timing** — the interval is recorded as an observation and never asserted, because it is aioice's
to decide and varies with the path. Nothing about **dead media on a still-live ICE pair**, which
remains indistinguishable from silence. It is evidence that the path *works*, not that it was
*deployed*: it satisfies no live release gate.

The last two tests use peer-connection doubles rather than a real pair, because the two paths they
cover cannot be produced on demand by a real peer: an ICE transport that never reaches a terminal
state at all, and a cancellation arriving while the verdict is still outstanding. They live here,
beside the real-peer tests they protect, rather than in a file of their own.

No `skip`, `skipif` or `xfail`, and nothing sleeps for a duration while waiting on an outcome:
every wait is on a real condition under a bounded deadline (the M11.15 R-70 rule).
"""

from __future__ import annotations

import asyncio
import fractions
import logging
import time

import av
import pytest
from aiortc import MediaStreamTrack

from core.providers.transport import TransportEvent, TransportEventKind
from providers.transport import aiortc_endpoint
from providers.transport.aiortc_endpoint import AiortcMediaEndpoint
from providers.transport.signalling import PeerSignaller
from providers.transport.webrtc import WebRTCTransport
from tests.test_webrtc_peer_loss import FakePeerConnection, FakeSignaller, FakeTrack
from tests.test_webrtc_signalling import LoopbackExchange

#: Deadlines. Generous on purpose: each one exists to turn a hang into a failure, not to measure.
CONNECT_DEADLINE_S = 30.0
MEDIA_DEADLINE_S = 15.0
#: Consent freshness is `CONSENT_FAILURES` (6) attempts, each preceded by
#: `CONSENT_INTERVAL * (0.8..1.2)` (4.0–6.0 s) and timing out after `RETRY_RTO` (0.5 s) because the
#: consent request is issued with `retransmissions=0` — so 27.0–39.0 s, plus teardown. This bound
#: is ≈2.3x the modelled worst case so a loaded machine fails loudly rather than flakily. It is
#: NOT a detection-time assertion: see `test_..._abrupt_ice_failure...`, which records the
#: interval and asserts nothing about it.
FAILURE_DEADLINE_S = 90.0
CLEAN_DEADLINE_S = 30.0
SETTLE_DEADLINE_S = 20.0

#: One 20 ms frame of PCM16 at 48 kHz — the packetisation WebRTC audio uses.
FRAME_SAMPLES = 960
FRAME_RATE = 48000
FRAME_S = FRAME_SAMPLES / FRAME_RATE


class PacedTone(MediaStreamTrack):
    """A real audio track that produces one frame per frame-duration, and no faster.

    `LoopbackExchange`'s own tone track yields with `sleep(0)`, and `RTCRtpSender._run_rtp` adds no
    pacing of its own, so the pair encodes and sends as fast as the event loop allows. Over the ~1 s
    the existing layer-B tests live that is invisible; over the ~33 s this file waits for consent
    freshness to expire it is a busy loop that would peg a core and perturb the very asyncio timers
    the consent model depends on. So once media is confirmed flowing, the remote's sender is moved
    onto this track through the public `replaceTrack`.

    `pts` starts far ahead of whatever the replaced track reached: it cannot be read (it is a
    closure local), and a forward jump is a benign timestamp gap where a backwards one would be
    reordering. Nothing here asserts on frames decoded after the swap."""

    kind = "audio"

    def __init__(self, *, pts_origin: int = FRAME_RATE * 60) -> None:
        super().__init__()
        self._pts = pts_origin

    async def recv(self) -> av.AudioFrame:
        await asyncio.sleep(FRAME_S)
        frame = av.AudioFrame(format="s16", layout="mono", samples=FRAME_SAMPLES)
        frame.planes[0].update(b"\x10\x27" * FRAME_SAMPLES)
        frame.sample_rate = FRAME_RATE
        frame.pts = self._pts
        frame.time_base = fractions.Fraction(1, FRAME_RATE)
        self._pts += FRAME_SAMPLES
        return frame


class AbruptLossExchange(LoopbackExchange):
    """`LoopbackExchange`, plus a way to sever the remote peer's ICE without closing it."""

    async def sever_ice(self) -> str:
        """Stop the remote peer's ICE transport, and return its resulting state.

        Deliberately **not** `pc.close()`: that sends DTLS `close_notify`, every DTLS transport
        reaches `closed`, and aiortc auto-closes the connection — the clean hangup path, which this
        file asserts separately and which proves nothing about ICE failure. Stopping ICE sends
        nothing at all, which is what an abrupt loss looks like on the wire.

        Every hop is asserted rather than assumed. The negotiated layout is one audio transceiver
        per peer: `addTrack` reuses an existing transceiver of the same kind, and
        `setRemoteDescription` matches the single audio m-line to that same transceiver, so the
        answerer never creates a second one."""
        assert self.remote is not None, "the remote peer is already gone"
        transceivers = self.remote.getTransceivers()
        assert len(transceivers) == 1, [(t.kind, t.mid) for t in transceivers]
        transceiver = transceivers[0]
        assert transceiver.kind == "audio", transceiver.kind
        assert transceiver.currentDirection == "sendrecv", transceiver.currentDirection
        dtls = transceiver.receiver.transport
        assert dtls is not None, "the receiver has no DTLS transport"
        assert dtls.state == "connected", dtls.state
        ice = dtls.transport
        assert ice is not None, "the DTLS transport has no ICE transport"
        # The exact predicate `RTCIceTransport._monitor` tests before promoting to `failed`. If
        # this does not hold, the test is not arming the mechanism it claims to arm.
        assert ice.state == "completed", ice.state
        await ice.stop()
        return ice.state


class EventRecorder:
    """Records transport events, and lets a test wait on a kind without spinning on the loop."""

    def __init__(self) -> None:
        self.events: list[TransportEvent] = []
        self._arrived: dict[TransportEventKind, asyncio.Event] = {}

    def __call__(self, event: TransportEvent) -> None:
        self.events.append(event)
        self._flag(event.kind).set()

    def _flag(self, kind: TransportEventKind) -> asyncio.Event:
        return self._arrived.setdefault(kind, asyncio.Event())

    async def wait_for(self, kind: TransportEventKind, *, deadline_s: float) -> None:
        """Wait on the **event**, under a deadline. Never polls, never sleeps for a duration."""
        await asyncio.wait_for(self._flag(kind).wait(), deadline_s)

    @property
    def kinds(self) -> list[TransportEventKind]:
        return [event.kind for event in self.events]

    def of(self, kind: TransportEventKind) -> list[TransportEvent]:
        return [event for event in self.events if event.kind is kind]


async def _tasks_settle_to(limit: int, *, deadline_s: float) -> bool:
    """Whether live asyncio tasks fall back to `limit` within the deadline.

    The shape is `test_webrtc_signalling.py`'s `_tasks_settle_to`, restated here rather than
    imported: reaching into another test module for a private helper is worse coupling than twelve
    duplicated lines. aiortc winds its ICE machinery down asynchronously on real timers, so "did
    anything of mine survive?" can only be asked after the library has finished, and only
    wall-clock lets it. The deadline exists to turn a genuine leak into a failure rather than a
    hang (M11.15 R-70)."""
    loop_deadline = time.monotonic() + deadline_s
    while len(asyncio.all_tasks()) > limit:
        if time.monotonic() > loop_deadline:
            return False
        await asyncio.sleep(0.02)
    return True


def _local_ice(pc) -> object:
    """The local peer's ICE transport, through the same public chain the endpoint walks."""
    transceivers = pc.getTransceivers()
    assert len(transceivers) == 1, [(t.kind, t.mid) for t in transceivers]
    dtls = transceivers[0].receiver.transport
    assert dtls is not None
    ice = dtls.transport
    assert ice is not None
    return ice


def _survivors() -> list[str]:
    return [
        task.get_coro().__qualname__
        for task in asyncio.all_tasks()
        if task is not asyncio.current_task()
    ]


# ══════════════════════════════════════════════════════════════════════════════
# The abrupt path: real ICE failure, on a real peer connection
# ══════════════════════════════════════════════════════════════════════════════


async def test_a_real_abrupt_ice_failure_is_reported_as_failed_not_disconnected():
    """A real `connectionState == "failed"` reaches R1 and leaves as `FAILED`, not `DISCONNECTED`.

    **Hermetic real-WebRTC-stack evidence. Not Chrome, not a real network, not live
    certification evidence.**"""
    session = "ses_abrupt_real"
    before = len(asyncio.all_tasks())
    exchange = AbruptLossExchange()
    endpoint = AiortcMediaEndpoint(PeerSignaller(exchange, negotiation_timeout_s=20.0))
    transport = WebRTCTransport(endpoint)
    recorder = EventRecorder()
    transport.observe(session, recorder)
    pc_states: list[str] = []
    ice_states: list[str] = []
    elapsed: float | None = None

    try:
        await asyncio.wait_for(transport.start(session_id=session), CONNECT_DEADLINE_S)

        # Registered at the first instant the peer connection exists, and long before a failure is
        # possible: the only route to `failed` here is consent freshness, which cannot expire for
        # at least 27 s after the sever below. A second listener for the same event is additive —
        # R1's own handler stays registered and still runs — so this observes the library's real
        # emission without standing in for it.
        local = endpoint._connections[session]  # noqa: SLF001
        local.on("connectionstatechange", lambda: pc_states.append(local.connectionState))
        # The ICE transport is where the failure is actually reported — see `_watch_ice_state`.
        # Observed independently of the endpoint's own listener, so this asserts the **library's**
        # transition rather than our record of it.
        ice = _local_ice(local)
        ice.on("statechange", lambda: ice_states.append(ice.state))

        # Media actually flowing is the precondition that makes the rest meaningful: it means ICE
        # genuinely reached `completed`, so the consent task genuinely exists.
        await recorder.wait_for(TransportEventKind.MEDIA_READY, deadline_s=MEDIA_DEADLINE_S)

        senders = exchange.remote.getSenders()
        assert len(senders) == 1, [(s.track and s.track.kind) for s in senders]
        senders[0].replaceTrack(PacedTone())

        started = time.monotonic()
        assert await exchange.sever_ice() == "closed"
        await recorder.wait_for(TransportEventKind.FAILED, deadline_s=FAILURE_DEADLINE_S)
        elapsed = time.monotonic() - started
    finally:
        await transport.stop(session_id=session)
        await exchange.aclose()

    # ── behavioural: the AR-6 fact this change exists to preserve ────────────
    assert recorder.kinds == [
        TransportEventKind.CONNECTED,
        TransportEventKind.MEDIA_READY,
        TransportEventKind.FAILED,
    ], recorder.kinds
    assert TransportEventKind.DISCONNECTED not in recorder.kinds, (
        "a dead ICE connection was reported as a clean disconnect"
    )
    failures = recorder.of(TransportEventKind.FAILED)
    assert len(failures) == 1, recorder.kinds
    # Exact, not a substring: `_faults` is assigned at exactly one site in the repository, so this
    # string is reachable only through R1. A refactor that reroutes the fault must fail here rather
    # than pass on a looser match.
    assert failures[0].detail == (
        "media path failed: inbound media ended: ice connection failed"
    ), failures[0].detail

    # ── behavioural: the real library really reported the failure ────────────
    # The ICE transport is the observer of record. `connectionState` is captured too, and
    # deliberately **not** asserted: on this path aiortc masks `failed` behind its own close, and
    # whether a future version stops doing so changes nothing about the contract above.
    assert "failed" in ice_states, {"ice": ice_states, "pc": pc_states}

    # ── implementation-coupled, and justified: a leak with no external symptom ──
    assert await _tasks_settle_to(before + 1, deadline_s=SETTLE_DEADLINE_S), _survivors()
    assert endpoint._faults == {}, "the fault was recorded but never consumed"  # noqa: SLF001
    assert endpoint._closers == {}, "R1's teardown task outlived the session"  # noqa: SLF001
    assert endpoint._ice_settled == {}, "the settle event outlived the session"  # noqa: SLF001

    # ── observation only: never an assertion, never an SLA ───────────────────
    print(f"\n[observation] real in-process ICE failure detected in {elapsed:.1f}s "
          f"(aioice consent freshness; varies with the path, not a guarantee)")


# ══════════════════════════════════════════════════════════════════════════════
# The clean path, on the same real pair, so this change cannot have inverted it
# ══════════════════════════════════════════════════════════════════════════════


async def test_a_clean_remote_close_is_still_disconnected_on_real_peers():
    """The converse, which no double can establish: real `close_notify` must stay `DISCONNECTED`.

    DTLS reaches `closed` rather than `failed` on teardown — `failed` is set only during the
    handshake — so `connectionState` goes `connected` -> `closed`, R1 ignores it, the track ends
    with no fault recorded, and the media path ends rather than breaks."""
    session = "ses_clean_real"
    before = len(asyncio.all_tasks())
    exchange = LoopbackExchange()
    endpoint = AiortcMediaEndpoint(PeerSignaller(exchange, negotiation_timeout_s=20.0))
    transport = WebRTCTransport(endpoint)
    recorder = EventRecorder()
    transport.observe(session, recorder)
    ice_states: list[str] = []

    try:
        await asyncio.wait_for(transport.start(session_id=session), CONNECT_DEADLINE_S)
        local = endpoint._connections[session]  # noqa: SLF001
        ice = _local_ice(local)
        ice.on("statechange", lambda: ice_states.append(ice.state))
        await recorder.wait_for(TransportEventKind.MEDIA_READY, deadline_s=MEDIA_DEADLINE_S)

        # A genuine `pc.close()` on the remote: real DTLS shutdown, real `close_notify`.
        await exchange.aclose()
        await recorder.wait_for(TransportEventKind.DISCONNECTED, deadline_s=CLEAN_DEADLINE_S)
    finally:
        # Unlike the abrupt path, nothing has told the endpoint anything: aiortc closed its own
        # peer connection and the endpoint still holds the session, so its state is only settled
        # once this runs.
        await transport.stop(session_id=session)
        await exchange.aclose()

    assert recorder.kinds == [
        TransportEventKind.CONNECTED,
        TransportEventKind.MEDIA_READY,
        TransportEventKind.DISCONNECTED,
    ], recorder.kinds
    assert TransportEventKind.FAILED not in recorder.kinds, (
        "a clean hangup was reported as a failure"
    )
    # The positive form of the same fact, at the layer that decides it: a clean close must never
    # put the ICE transport into `failed`, or the settle verdict would turn a hangup into a fault.
    assert "failed" not in ice_states, ice_states
    assert await _tasks_settle_to(before + 1, deadline_s=SETTLE_DEADLINE_S), _survivors()
    assert endpoint._faults == {}, "a clean close recorded a fault"  # noqa: SLF001
    assert endpoint._closers == {}, "a clean close started a teardown task"  # noqa: SLF001
    assert endpoint._ice_settled == {}, "the settle event outlived the session"  # noqa: SLF001


# ══════════════════════════════════════════════════════════════════════════════
# Doubles — the two verdict paths a real peer cannot be asked to produce
# ══════════════════════════════════════════════════════════════════════════════


class _FakeIce:
    """The public surface `_ice_transports` walks, plus a way to drive `statechange`."""

    def __init__(self, state: str = "completed") -> None:
        self.state = state
        self.handlers: list = []

    def on(self, event: str, handler):
        assert event == "statechange", event
        self.handlers.append(handler)
        return handler

    def drive(self, state: str) -> None:
        """What aioice's consent expiry eventually produces, without the ~33 s wait."""
        self.state = state
        for handler in list(self.handlers):
            handler()


class _FakeDtls:
    def __init__(self, ice: _FakeIce) -> None:
        self.transport = ice


class _FakeReceiver:
    def __init__(self, dtls: _FakeDtls) -> None:
        self.transport = dtls


class _FakeTransceiver:
    kind = "audio"
    mid = "0"

    def __init__(self, receiver: _FakeReceiver) -> None:
        self.receiver = receiver


class IcePeerConnection(FakePeerConnection):
    """`FakePeerConnection` plus the public transceiver chain that carries an ICE transport.

    `extra_transceivers` share the **same** transport, which is what aiortc does when the
    negotiation bundles them — the shape the watcher must deduplicate."""

    def __init__(self, track: FakeTrack, *, extra_transceivers: int = 0) -> None:
        super().__init__(track)
        self.ice = _FakeIce()
        self._transceivers = [
            _FakeTransceiver(_FakeReceiver(_FakeDtls(self.ice)))
            for _ in range(1 + extra_transceivers)
        ]

    def getTransceivers(self):  # noqa: N802 — the library's spelling
        return list(self._transceivers)


def _double(monkeypatch, factory):
    track = FakeTrack()
    endpoint = AiortcMediaEndpoint(FakeSignaller(track))
    pc = factory(track)
    monkeypatch.setattr(endpoint, "_new_peer_connection", lambda: pc)
    return endpoint, pc, track


async def test_a_verdict_arriving_after_the_track_ended_still_leaves_as_failed(monkeypatch):
    """The production ordering, without the consent wait: the track ends **first**.

    This is the whole point of the settle hand-off. Measured against a real pair the verdict
    follows the track's end by a fraction of a millisecond; here it is driven explicitly, so the
    assertion holds regardless of scheduling."""
    session = "ses_double_late_verdict"
    endpoint, pc, _track = _double(monkeypatch, IcePeerConnection)
    transport = WebRTCTransport(endpoint)
    recorder = EventRecorder()
    transport.observe(session, recorder)
    try:
        await transport.start(session_id=session)
        pc.peer_closed_cleanly()  # ends the track, exactly as aiortc's own close would
        pc.ice.drive("failed")  # ...and only then does ICE report why
        await recorder.wait_for(TransportEventKind.FAILED, deadline_s=5.0)
    finally:
        await transport.stop(session_id=session)

    assert TransportEventKind.DISCONNECTED not in recorder.kinds, recorder.kinds
    failures = recorder.of(TransportEventKind.FAILED)
    assert len(failures) == 1, recorder.kinds
    assert failures[0].detail == (
        "media path failed: inbound media ended: ice connection failed"
    ), failures[0].detail
    assert endpoint._faults == {}, "the fault was recorded but never consumed"  # noqa: SLF001
    assert endpoint._ice_settled == {}, "the settle event outlived the session"  # noqa: SLF001


async def test_a_verdict_that_never_arrives_fails_open_to_disconnected(monkeypatch, caplog):
    """Fail-open: an ICE transport that reports nothing must not hold the media path open.

    The deadline is patched small so the suite never waits the real 100 ms."""
    monkeypatch.setattr(aiortc_endpoint, "ICE_SETTLE_DEADLINE_S", 0.01)
    session = "ses_double_no_verdict"
    endpoint, _pc, track = _double(monkeypatch, IcePeerConnection)
    transport = WebRTCTransport(endpoint)
    recorder = EventRecorder()
    transport.observe(session, recorder)
    try:
        await transport.start(session_id=session)
        with caplog.at_level(logging.WARNING, logger="voxvoice.providers.transport.aiortc"):
            track.stopped.set()  # the track ends; the ICE transport says nothing, ever
            await recorder.wait_for(TransportEventKind.DISCONNECTED, deadline_s=5.0)
    finally:
        await transport.stop(session_id=session)

    assert TransportEventKind.FAILED not in recorder.kinds, (
        "a hangup became a failure because no verdict arrived"
    )
    assert any("gave no verdict" in record.getMessage() for record in caplog.records), (
        "the timeout was silent"
    )


async def test_cancellation_while_the_verdict_is_outstanding_is_not_a_hangup(monkeypatch):
    """Shutdown is withdrawal, not a transport fact (ADR-0046): `CancelledError` must propagate.

    The suppression around the settle wait catches `TimeoutError` alone; were it broader, a
    cancelled call would be reported as a clean disconnect."""
    monkeypatch.setattr(aiortc_endpoint, "ICE_SETTLE_DEADLINE_S", 30.0)
    session = "ses_double_cancelled"
    endpoint, _pc, track = _double(monkeypatch, IcePeerConnection)
    outcomes: list[str] = []

    async def consume() -> None:
        try:
            async for _frame in endpoint.inbound(session):
                pass
        except asyncio.CancelledError:
            outcomes.append("cancelled")
            raise
        outcomes.append("returned")

    try:
        await endpoint.connect(session)
        task = asyncio.create_task(consume())
        track.stopped.set()
        # It must still be waiting on the verdict. Proving an absence needs a deadline: finishing
        # within it would mean the wait had been skipped, since this transport reports nothing and
        # the settle deadline is thirty seconds away.
        with pytest.raises(TimeoutError):
            await asyncio.wait_for(asyncio.shield(task), 0.05)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    finally:
        await endpoint.close(session)

    assert outcomes == ["cancelled"], outcomes


async def test_one_listener_is_registered_per_distinct_ice_transport(monkeypatch):
    """Bundled transceivers share one transport; it is watched once, by identity."""
    session = "ses_double_bundled"
    endpoint, pc, _track = _double(
        monkeypatch, lambda track: IcePeerConnection(track, extra_transceivers=2)
    )
    try:
        await endpoint.connect(session)
        assert len(pc.getTransceivers()) == 3
        assert len(pc.ice.handlers) == 1, "a bundled transport was watched more than once"
    finally:
        await endpoint.close(session)


async def test_a_peer_connection_without_transceivers_waits_for_no_verdict(monkeypatch):
    """The compatibility control for the existing peer-connection doubles.

    A double exposes no transceivers, so there is no ICE transport, no verdict can ever arrive,
    and the session must therefore hold no settle event — otherwise every track end in
    `test_webrtc_peer_loss.py` would pay the full deadline for an answer that cannot come."""
    session = "ses_double_plain"
    endpoint, _pc, _track = _double(monkeypatch, FakePeerConnection)
    try:
        await endpoint.connect(session)
        assert endpoint._ice_settled == {}, (  # noqa: SLF001
            "a session with no observable ICE transport kept a settle event"
        )
    finally:
        await endpoint.close(session)


async def test_repeated_and_concurrent_close_leave_no_event_or_closer(monkeypatch):
    """Teardown is idempotent, and the settle event does not survive it.

    `_faults` is deliberately **not** asserted empty here: the fault is consumed by `inbound()`,
    and this session never ran one. That residue is cleared when the session id is reused
    (`connect` drops it) and is pre-existing M13.1 behaviour, not something the settle hand-off
    introduced."""
    session = "ses_double_repeat"
    endpoint, pc, _track = _double(monkeypatch, IcePeerConnection)
    await endpoint.connect(session)
    pc.ice.drive("failed")
    await asyncio.gather(
        endpoint.close(session), endpoint.close(session), endpoint.close(session)
    )
    await endpoint.close(session)

    assert endpoint._ice_settled == {}, "the settle event outlived teardown"  # noqa: SLF001
    assert endpoint._closers == {}, "a teardown task outlived the session"  # noqa: SLF001
    assert endpoint._connections == {}  # noqa: SLF001
    assert endpoint._tracks == {}  # noqa: SLF001
    assert endpoint._outbound == {}  # noqa: SLF001
