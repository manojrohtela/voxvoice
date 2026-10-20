"""Abrupt peer loss: ICE reports `failed`, and the media path ends as a **failure** (M13.1).

## The two ways a peer goes away, and why only one of them used to be noticed

A **clean** close — tab closed, navigation, `pc.close()` — sends DTLS `close_notify`, every DTLS
transport reaches `closed`, and aiortc auto-closes the connection by itself. The receiver stops,
`recv()` raises, `inbound()` returns, and the transport emits `DISCONNECTED`. That path already
worked and must keep working exactly as it did.

An **abrupt** loss — sleeping laptop, dropped link, killed process — sends nothing. aioice's
consent-freshness checks fail, ICE moves to `failed`, `connectionstatechange` fires, and aiortc
leaves the connection open (it has no `disconnected` state and does not close a failed one). With
nobody listening, the receiver was never stopped, so `recv()` never raised and the session lived
on with no media and no event.

These tests drive both paths against a peer-connection double, so they assert the wiring and the
AR-6 semantics without a network and without waiting on a real consent timeout. **They do not
measure detection time**: that is aioice's to decide, it varies with the path, and it is an
expected bound to validate on a real host rather than anything this file can promise.

No `skip`, `skipif` or `xfail`, and nothing sleeps for a duration.
"""

from __future__ import annotations

import asyncio

import pytest
from aiortc.mediastreams import MediaStreamError

from core.providers.transport import TransportEventKind
from providers.transport.aiortc_endpoint import AiortcMediaEndpoint
from providers.transport.media_endpoint import MediaEndpointError
from providers.transport.webrtc import WebRTCTransport

SESSION = "ses_peer_loss"
DEADLINE_S = 5.0


class FakeTrack:
    """An inbound track that ends — as aiortc's does — only once it has been stopped."""

    def __init__(self) -> None:
        self.stopped = asyncio.Event()

    async def recv(self):
        await self.stopped.wait()
        raise MediaStreamError


class FakePeerConnection:
    """The surface the endpoint touches, plus a way to drive connection-state changes."""

    def __init__(self, track: FakeTrack) -> None:
        self.connectionState = "new"  # noqa: N815 — the library's spelling
        self._track = track
        self._handlers: dict[str, object] = {}
        self.closed = 0

    def on(self, event: str, handler=None):
        if handler is None:  # pragma: no cover — the endpoint uses the two-argument form

            def register(fn):
                self._handlers[event] = fn
                return fn

            return register
        self._handlers[event] = handler
        return handler

    def addTrack(self, track) -> None:  # noqa: N802 — the library's spelling
        pass

    def _emit_state(self) -> None:
        handler = self._handlers.get("connectionstatechange")
        if handler is not None:
            handler()

    def ice_failed(self) -> None:
        """What aioice's consent-freshness failure eventually produces."""
        self.connectionState = "failed"
        self._emit_state()

    def peer_closed_cleanly(self) -> None:
        """What a DTLS `close_notify` produces: aiortc closes the connection itself."""
        self._track.stopped.set()
        self.connectionState = "closed"
        self._emit_state()

    async def close(self) -> None:
        self.closed += 1
        self._track.stopped.set()
        self.connectionState = "closed"
        self._emit_state()


class FakeSignaller:
    def __init__(self, track: FakeTrack) -> None:
        self._track = track
        self.released: list[str] = []

    async def negotiate(self, session_id: str, pc):
        return self._track

    def release(self, session_id: str) -> None:
        self.released.append(session_id)


def _endpoint(monkeypatch) -> tuple[AiortcMediaEndpoint, FakePeerConnection, FakeTrack]:
    track = FakeTrack()
    endpoint = AiortcMediaEndpoint(FakeSignaller(track))
    pc = FakePeerConnection(track)
    monkeypatch.setattr(endpoint, "_new_peer_connection", lambda: pc)
    return endpoint, pc, track


async def _kinds(transport: WebRTCTransport, events: list, *, until) -> set:
    """Wait, bounded, until `until` holds, then report the event kinds seen."""

    async def wait() -> None:
        while not until():
            await asyncio.sleep(0)

    await asyncio.wait_for(wait(), DEADLINE_S)
    return {event.kind for event in events}


# ══ the endpoint ══════════════════════════════════════════════════════════════


async def test_ice_failure_closes_the_connection(monkeypatch):
    endpoint, pc, _ = _endpoint(monkeypatch)
    await endpoint.connect(SESSION)
    assert pc.closed == 0

    pc.ice_failed()
    await asyncio.wait_for(_closed(pc), DEADLINE_S)
    assert pc.closed == 1


async def _closed(pc: FakePeerConnection) -> None:
    while pc.closed == 0:
        await asyncio.sleep(0)


async def test_repeated_failure_events_close_the_connection_exactly_once(monkeypatch):
    """The state change is emitted again by our own close; it must not re-enter teardown."""
    endpoint, pc, _ = _endpoint(monkeypatch)
    await endpoint.connect(SESSION)

    pc.ice_failed()
    pc.ice_failed()
    pc.ice_failed()
    await asyncio.wait_for(_closed(pc), DEADLINE_S)
    await asyncio.sleep(0)
    assert pc.closed == 1, "teardown ran more than once"


async def test_a_state_change_after_teardown_starts_no_task(monkeypatch):
    """A watcher firing after `stop()` has removed the session must leave no orphan (§12)."""
    endpoint, pc, _ = _endpoint(monkeypatch)
    await endpoint.connect(SESSION)
    await endpoint.close(SESSION)

    pc.ice_failed()
    await asyncio.sleep(0)
    assert endpoint._closers == {}, "an orphaned teardown task was scheduled"  # noqa: SLF001


async def test_closing_while_a_watcher_teardown_is_in_flight_does_not_double_close(monkeypatch):
    """The shutdown race: an explicit close concurrent with the watcher's own."""
    endpoint, pc, _ = _endpoint(monkeypatch)
    await endpoint.connect(SESSION)

    pc.ice_failed()  # schedules the teardown without awaiting it
    await endpoint.close(SESSION)  # must await that task rather than race it
    assert pc.closed == 1
    assert endpoint._closers == {}  # noqa: SLF001 — the task must not outlive the session


async def test_an_ice_failure_during_negotiation_surfaces_as_a_media_endpoint_error(monkeypatch):
    """`connect` already closes and raises on a failed negotiation; that path is unchanged."""
    track = FakeTrack()
    endpoint = AiortcMediaEndpoint(FakeSignaller(track))
    pc = FakePeerConnection(track)
    monkeypatch.setattr(endpoint, "_new_peer_connection", lambda: pc)

    async def failing_negotiate(session_id, peer):
        pc.ice_failed()
        raise RuntimeError("ice died mid-negotiation")

    monkeypatch.setattr(endpoint._signaller, "negotiate", failing_negotiate)  # noqa: SLF001
    with pytest.raises(MediaEndpointError, match="negotiation failed"):
        await endpoint.connect(SESSION)
    assert SESSION not in endpoint._connections  # noqa: SLF001


async def test_a_fresh_session_inherits_no_previous_fault(monkeypatch):
    endpoint, pc, _ = _endpoint(monkeypatch)
    await endpoint.connect(SESSION)
    pc.ice_failed()
    await asyncio.wait_for(_closed(pc), DEADLINE_S)

    track = FakeTrack()
    fresh = FakePeerConnection(track)
    monkeypatch.setattr(endpoint, "_new_peer_connection", lambda: fresh)
    monkeypatch.setattr(endpoint._signaller, "_track", track)  # noqa: SLF001
    await endpoint.connect(SESSION)
    assert SESSION not in endpoint._faults  # noqa: SLF001


# ══ the AR-6 fact the transport reports ═══════════════════════════════════════


async def test_abrupt_ice_failure_is_reported_as_failed_not_disconnected(monkeypatch):
    """The semantic this change exists to preserve.

    Closing the connection ends the track cleanly, which would otherwise read as a hangup. A dead
    network path is not a hangup, so it must leave as `FAILED`."""
    endpoint, pc, _ = _endpoint(monkeypatch)
    events: list = []
    transport = WebRTCTransport(endpoint)
    transport.observe(SESSION, events.append)

    await transport.start(session_id=SESSION)
    pc.ice_failed()
    kinds = await _kinds(
        transport,
        events,
        until=lambda: TransportEventKind.FAILED in {e.kind for e in events},
    )
    assert TransportEventKind.FAILED in kinds
    assert TransportEventKind.DISCONNECTED not in kinds, (
        "a dead ICE connection was reported as a clean disconnect"
    )
    await transport.stop(session_id=SESSION)


async def test_a_clean_peer_close_is_still_reported_as_disconnected(monkeypatch):
    """The path that already worked, asserted so this change cannot have inverted it."""
    endpoint, pc, _ = _endpoint(monkeypatch)
    events: list = []
    transport = WebRTCTransport(endpoint)
    transport.observe(SESSION, events.append)

    await transport.start(session_id=SESSION)
    pc.peer_closed_cleanly()
    kinds = await _kinds(
        transport,
        events,
        until=lambda: TransportEventKind.DISCONNECTED in {e.kind for e in events},
    )
    assert TransportEventKind.DISCONNECTED in kinds
    assert TransportEventKind.FAILED not in kinds, "a clean hangup was reported as a failure"
    await transport.stop(session_id=SESSION)


async def test_the_failure_is_reported_once_and_leaves_no_task_behind(monkeypatch):
    endpoint, pc, _ = _endpoint(monkeypatch)
    events: list = []
    transport = WebRTCTransport(endpoint)
    transport.observe(SESSION, events.append)

    await transport.start(session_id=SESSION)
    pc.ice_failed()
    await _kinds(
        transport,
        events,
        until=lambda: TransportEventKind.FAILED in {e.kind for e in events},
    )
    await transport.stop(session_id=SESSION)
    failures = [e for e in events if e.kind is TransportEventKind.FAILED]
    assert len(failures) == 1, [e.kind for e in events]
    assert endpoint._closers == {}  # noqa: SLF001
    assert endpoint._faults == {}, "the fault was recorded but never consumed"  # noqa: SLF001
