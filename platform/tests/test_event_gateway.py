"""Live Event Gateway tests (M4.2).

Covers connection + subscription lifecycle, WebSocket + SSE delivery of the same
envelope, ordering, scope + type filtering, wildcard, multiple clients, backpressure
(drop-oldest) + slow-consumer disconnect, connection isolation, serialization
failure, graceful shutdown, deterministic replay, and Runtime isolation (the gateway
uses only the bus's public contract; a client failure never reaches the producer).
"""

from __future__ import annotations

import itertools
import json

import pytest

from core.events import Event, EventMetadata, EventSource, EventType
from events import InProcessEventBus
from gateway import (
    DuplicateSubscription,
    EventChannel,
    EventSerializer,
    GatewayShutdown,
    LiveEventGateway,
    OverflowPolicy,
    SSETransport,
    UnknownClient,
    WebSocketTransport,
)
from gateway.connection import ConnectionManager
from gateway.stream import EventStream, OfferResult


def _bus():
    ids = (f"e{i}" for i in itertools.count())
    ticks = itertools.count(1)
    return InProcessEventBus(id_factory=lambda: next(ids), clock=lambda: float(next(ticks)))


def _pub(bus, *, session="s1", source=EventSource.TURN_ENGINE, org="o1", conv=None):
    return bus.publisher(
        EventMetadata(session_id=session, source=source, org_id=org, conversation_id=conv)
    )


class RecordingConn:
    def __init__(self):
        self.sent: list[str] = []
        self.closed = False

    async def send(self, data: str) -> None:
        self.sent.append(data)

    async def close(self) -> None:
        self.closed = True


async def _drain(transport, session, conn):
    session.stream.close()  # terminate the drain loop deterministically
    await transport.run(session, conn)


# ── connection lifecycle ─────────────────────────────────────────────────────
def test_connect_and_disconnect_lifecycle():
    gw = LiveEventGateway()
    c = gw.connect("c1")
    assert c.id == "c1"
    assert len(gw.connections) == 1
    assert gw.metrics.active_connections == 1
    gw.disconnect("c1")
    assert len(gw.connections) == 0
    assert gw.metrics.connections_closed == 1
    gw.disconnect("c1")  # idempotent


def test_auth_hook_can_reject_connection():
    mgr = ConnectionManager(authenticate=lambda cid, ctx: None)  # reject all
    gw = LiveEventGateway(connections=mgr)
    assert gw.connect("c1") is None
    assert len(gw.connections) == 0


# ── subscription lifecycle ───────────────────────────────────────────────────
def test_subscribe_unsubscribe_and_duplicate():
    gw = LiveEventGateway()
    gw.connect("c1")
    ch = EventChannel.session("s1")
    assert gw.subscribe("c1", ch) is True
    with pytest.raises(DuplicateSubscription):
        gw.subscribe("c1", ch)
    gw.unsubscribe("c1", ch)
    gw.unsubscribe("c1", ch)  # idempotent
    assert gw.subscribe("c1", ch) is True  # can re-subscribe after removal


def test_subscribe_unknown_client_raises():
    gw = LiveEventGateway()
    with pytest.raises(UnknownClient):
        gw.subscribe("nope", EventChannel.all())


def test_authorization_hook_can_deny_subscription():
    from gateway.subscription import SubscriptionManager

    subs = SubscriptionManager(authorize=lambda auth, ch: False)
    gw = LiveEventGateway(subscriptions=subs)
    gw.connect("c1")
    assert gw.subscribe("c1", EventChannel.all()) is False


# ── delivery + ordering (WebSocket) ──────────────────────────────────────────
async def test_websocket_delivery_ordered():
    bus = _bus()
    gw = LiveEventGateway()
    gw.attach(bus)
    c = gw.connect("c1")
    gw.subscribe("c1", EventChannel.session("s1"))
    pub = _pub(bus)
    for _ in range(5):
        pub.publish(EventType.TURN_STARTED)
    conn = RecordingConn()
    await _drain(WebSocketTransport(), c, conn)
    seqs = [json.loads(f)["seq"] for f in conn.sent]
    assert seqs == [1, 2, 3, 4, 5]  # ordered, exactly-once
    assert conn.closed is True


# ── SSE delivery: same envelope, different framing ───────────────────────────
async def test_sse_delivery_same_envelope_different_framing():
    bus = _bus()
    gw = LiveEventGateway()
    gw.attach(bus)
    c = gw.connect("c1")
    gw.subscribe("c1", EventChannel.all())
    _pub(bus).publish(EventType.TURN_STARTED, {"turn": 1})
    conn = RecordingConn()
    await _drain(SSETransport(), c, conn)
    assert len(conn.sent) == 1
    wire = conn.sent[0]
    assert wire.startswith("id: 1\ndata: ")
    assert wire.endswith("\n\n")
    payload = wire.split("data: ", 1)[1].rstrip("\n")
    # Identical envelope to what a WS client would receive.
    assert json.loads(payload)["type"] == EventType.TURN_STARTED.value


async def test_ws_and_sse_carry_identical_envelope():
    bus = _bus()
    gw = LiveEventGateway()
    gw.attach(bus)
    a = gw.connect("ws")
    b = gw.connect("sse")
    gw.subscribe("ws", EventChannel.all())
    gw.subscribe("sse", EventChannel.all())
    _pub(bus).publish(EventType.SPEECH_STARTED, {"x": 1})
    wsc, ssec = RecordingConn(), RecordingConn()
    await _drain(WebSocketTransport(), a, wsc)
    await _drain(SSETransport(), b, ssec)
    ws_json = json.loads(wsc.sent[0])
    sse_json = json.loads(ssec.sent[0].split("data: ", 1)[1].rstrip("\n"))
    assert ws_json == sse_json  # exactly the same EventEnvelope


# ── scope + type filtering ───────────────────────────────────────────────────
async def test_scope_and_type_filtering():
    bus = _bus()
    gw = LiveEventGateway()
    gw.attach(bus)
    c = gw.connect("c1")
    # Only TURN_COMPLETED events for session s1.
    gw.subscribe("c1", EventChannel.session("s1", types=frozenset({EventType.TURN_COMPLETED})))
    _pub(bus, session="s1").publish(EventType.TURN_STARTED)  # wrong type
    _pub(bus, session="s2").publish(EventType.TURN_COMPLETED)  # wrong session
    _pub(bus, session="s1").publish(EventType.TURN_COMPLETED)  # match
    assert c.stream.depth == 1


async def test_wildcard_receives_everything():
    bus = _bus()
    gw = LiveEventGateway()
    gw.attach(bus)
    c = gw.connect("c1")
    gw.subscribe("c1", EventChannel.all())
    _pub(bus, session="a").publish(EventType.TURN_STARTED)
    _pub(bus, session="b").publish(EventType.SESSION_ENDED)
    assert c.stream.depth == 2


async def test_org_scope():
    bus = _bus()
    gw = LiveEventGateway()
    gw.attach(bus)
    c = gw.connect("c1")
    gw.subscribe("c1", EventChannel.org("acme"))
    _pub(bus, org="acme").publish(EventType.TURN_STARTED)
    _pub(bus, org="other").publish(EventType.TURN_STARTED)
    assert c.stream.depth == 1


# ── multiple clients + no duplication ────────────────────────────────────────
async def test_multiple_clients_isolated_and_deduped():
    bus = _bus()
    gw = LiveEventGateway()
    gw.attach(bus)
    a = gw.connect("a")
    b = gw.connect("b")
    gw.subscribe("a", EventChannel.session("s1"))
    # b subscribes twice-matching channels — must still receive each event once.
    gw.subscribe("b", EventChannel.all())
    gw.subscribe("b", EventChannel.session("s1"))
    _pub(bus, session="s1").publish(EventType.TURN_STARTED)
    assert a.stream.depth == 1
    assert b.stream.depth == 1  # deduped despite two matching channels


# ── backpressure + slow consumer ─────────────────────────────────────────────
def test_backpressure_drop_oldest():
    s = EventStream(maxsize=2, policy=OverflowPolicy.DROP_OLDEST)
    assert s.offer("1") is OfferResult.DELIVERED
    assert s.offer("2") is OfferResult.DELIVERED
    assert s.offer("3") is OfferResult.DROPPED  # evicts "1"
    assert s.depth == 2
    assert s.dropped == 1


def test_slow_consumer_disconnect_policy():
    bus = _bus()
    mgr = ConnectionManager(stream_maxsize=1, overflow_policy=OverflowPolicy.DISCONNECT)
    gw = LiveEventGateway(connections=mgr)
    gw.attach(bus)
    gw.connect("slow")
    gw.subscribe("slow", EventChannel.all())
    pub = _pub(bus)
    pub.publish(EventType.TURN_STARTED)  # fills the 1-slot buffer
    pub.publish(EventType.TURN_COMPLETED)  # overflow → disconnect the slow client
    assert gw.connections.get("slow") is None
    assert gw.metrics.slow_disconnects == 1


# ── serialization failure isolated ───────────────────────────────────────────
def test_serialization_failure_is_isolated():
    class BadSerializer(EventSerializer):
        def serialize(self, event):
            raise ValueError("cannot encode")

    bus = _bus()
    gw = LiveEventGateway(serializer=BadSerializer())
    gw.attach(bus)
    c = gw.connect("c1")
    gw.subscribe("c1", EventChannel.all())
    _pub(bus).publish(EventType.TURN_STARTED)  # must not raise into the bus
    assert gw.metrics.serialization_failures == 1
    assert c.stream.depth == 0


# ── graceful shutdown ────────────────────────────────────────────────────────
def test_shutdown_detaches_and_closes_all():
    bus = _bus()
    gw = LiveEventGateway()
    gw.attach(bus)
    gw.connect("c1")
    gw.subscribe("c1", EventChannel.all())
    gw.shutdown()
    assert gw.is_shutdown is True
    assert len(gw.connections) == 0
    # After shutdown the bus no longer routes to the gateway (detached).
    _pub(bus).publish(EventType.TURN_STARTED)  # no raise
    with pytest.raises(GatewayShutdown):
        gw.connect("c2")


# ── Runtime isolation: gateway failure never reaches the producer ────────────
def test_gateway_never_raises_into_the_bus():
    bus = _bus()
    gw = LiveEventGateway()
    gw.attach(bus)

    # A client whose stream.offer explodes must not break publish.
    c = gw.connect("c1")
    gw.subscribe("c1", EventChannel.all())

    def boom(_frame):
        raise RuntimeError("offer failed")

    c.stream.offer = boom  # type: ignore[method-assign]
    # publish returns normally; the bad client is disconnected, the bus is intact.
    _pub(bus).publish(EventType.TURN_STARTED)
    assert gw.connections.get("c1") is None
    other: list[Event] = []
    bus.subscribe(other.append)
    _pub(bus).publish(EventType.TURN_COMPLETED)
    assert len(other) == 1  # the bus still works


# ── heartbeat / idle ─────────────────────────────────────────────────────────
def test_idle_sweep_disconnects_stale_clients():
    clock = {"t": 100.0}
    mgr = ConnectionManager(clock=lambda: clock["t"])
    gw = LiveEventGateway(connections=mgr)
    gw.connect("c1")
    clock["t"] = 100.0 + 31.0  # advance past a 30s idle threshold
    assert mgr.idle_clients(30.0) == ["c1"]
    assert mgr.sweep_idle(30.0) == 1
    assert len(gw.connections) == 0


async def test_heartbeat_payloads():
    ws, sse = WebSocketTransport(), SSETransport()
    wsc, ssec = RecordingConn(), RecordingConn()
    await ws.heartbeat(wsc)
    await sse.heartbeat(ssec)
    assert wsc.sent == ['{"type":"ping"}']
    assert ssec.sent == [": ping\n\n"]


# ── deterministic replay ─────────────────────────────────────────────────────
async def test_deterministic_replay():
    async def run():
        bus = _bus()
        gw = LiveEventGateway()
        gw.attach(bus)
        c = gw.connect("c1")
        gw.subscribe("c1", EventChannel.all())
        pub = _pub(bus)
        for t in (EventType.TURN_STARTED, EventType.SPEECH_STARTED, EventType.TURN_COMPLETED):
            pub.publish(t)
        conn = RecordingConn()
        await _drain(WebSocketTransport(), c, conn)
        return conn.sent

    assert await run() == await run()
