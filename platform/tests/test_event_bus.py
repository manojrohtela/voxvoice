"""Internal Event Bus tests (M4.1).

Covers publish/subscribe/unsubscribe, ordered delivery, multiple subscribers,
subscriber isolation, type filtering, deterministic replay, duplicate registration,
invalid events, dispatcher shutdown, and recursive-publish protection.
"""

from __future__ import annotations

import itertools

import pytest

from core.events import Event, EventMetadata, EventSource, EventType
from events import (
    DuplicateSubscription,
    EventBusShutdown,
    InProcessEventBus,
    InvalidEvent,
)


def _bus():
    # Deterministic ids + clock for replay assertions.
    ids = (f"e{i}" for i in itertools.count())
    ticks = itertools.count(1)
    return InProcessEventBus(id_factory=lambda: next(ids), clock=lambda: float(next(ticks)))


def _pub(bus, source=EventSource.RUNTIME):
    return bus.publisher(EventMetadata(session_id="s1", source=source, org_id="o1"))


# ── publish / subscribe ──────────────────────────────────────────────────────
def test_publish_delivers_to_subscriber():
    bus = _bus()
    seen: list[Event] = []
    bus.subscribe(seen.append)
    ev = _pub(bus).publish(EventType.RUNTIME_STARTED, {"k": 1})
    assert len(seen) == 1
    assert seen[0].type is EventType.RUNTIME_STARTED
    assert seen[0].data == {"k": 1}
    assert seen[0].source is EventSource.RUNTIME
    assert seen[0].session_id == "s1"
    assert seen[0] is ev  # the bus stamped + delivered this envelope
    assert ev.seq == 1


def test_no_subscribers_is_a_noop():
    bus = _bus()
    _pub(bus).publish(EventType.SESSION_STARTED)  # must not raise
    assert bus.last_seq == 1


# ── ordering + monotonic seq ─────────────────────────────────────────────────
def test_ordered_delivery_and_monotonic_seq():
    bus = _bus()
    seen: list[int] = []
    bus.subscribe(lambda e: seen.append(e.seq))
    pub = _pub(bus)
    for _ in range(5):
        pub.publish(EventType.TURN_STARTED)
    assert seen == [1, 2, 3, 4, 5]  # strict order, monotonic seq


# ── multiple subscribers, registration order ────────────────────────────────
def test_multiple_subscribers_receive_in_registration_order():
    bus = _bus()
    order: list[str] = []
    bus.subscribe(lambda e: order.append("a"))
    bus.subscribe(lambda e: order.append("b"))
    bus.subscribe(lambda e: order.append("c"))
    _pub(bus).publish(EventType.RUNTIME_STARTED)
    assert order == ["a", "b", "c"]


# ── subscriber isolation ─────────────────────────────────────────────────────
def test_subscriber_failure_is_isolated():
    bus = _bus()
    got: list[str] = []

    def boom(e):
        raise RuntimeError("subscriber crashed")

    bus.subscribe(boom)
    bus.subscribe(lambda e: got.append("ok"))
    _pub(bus).publish(EventType.RUNTIME_STARTED)  # must not raise
    assert got == ["ok"]  # the healthy subscriber still received the event
    # The bus is uncorrupted — a further publish still works.
    _pub(bus).publish(EventType.SESSION_STARTED)
    assert got == ["ok", "ok"]


# ── type filtering ───────────────────────────────────────────────────────────
def test_type_filter_only_matching_events():
    bus = _bus()
    seen: list[EventType] = []
    bus.subscribe(
        lambda e: seen.append(e.type), types=frozenset({EventType.TURN_COMPLETED})
    )
    pub = _pub(bus)
    pub.publish(EventType.TURN_STARTED)
    pub.publish(EventType.TURN_COMPLETED)
    pub.publish(EventType.SPEECH_STARTED)
    assert seen == [EventType.TURN_COMPLETED]


# ── unsubscribe (idempotent) ─────────────────────────────────────────────────
def test_unsubscribe_stops_delivery_and_is_idempotent():
    bus = _bus()
    seen: list[int] = []
    sub = bus.subscriber(lambda e: seen.append(e.seq))
    _pub(bus).publish(EventType.RUNTIME_STARTED)
    sub.unsubscribe()
    _pub(bus).publish(EventType.RUNTIME_STARTED)
    sub.unsubscribe()  # idempotent — no error
    assert seen == [1]  # only the pre-unsubscribe event


# ── duplicate registration is rejected ───────────────────────────────────────
def test_duplicate_handler_registration_raises():
    bus = _bus()

    def handler(e):
        pass

    bus.subscribe(handler)
    with pytest.raises(DuplicateSubscription):
        bus.subscribe(handler)


# ── invalid event ────────────────────────────────────────────────────────────
def test_invalid_event_type_raises():
    bus = _bus()
    bad = Event(
        id="x", type="not-an-event-type", ts=1.0, seq=0,  # type: ignore[arg-type]
        session_id="s", source=EventSource.RUNTIME,
    )
    with pytest.raises(InvalidEvent):
        bus.publish(bad)


# ── dispatcher shutdown ──────────────────────────────────────────────────────
def test_shutdown_drops_events_and_blocks_subscribe():
    bus = _bus()
    seen: list[int] = []
    bus.subscribe(lambda e: seen.append(e.seq))
    _pub(bus).publish(EventType.RUNTIME_STARTED)
    bus.shutdown()
    assert bus.is_shutdown is True
    _pub(bus).publish(EventType.RUNTIME_STOPPED)  # dropped, no raise
    assert seen == [1]
    with pytest.raises(EventBusShutdown):
        bus.subscribe(lambda e: None)


# ── recursive-publish protection ─────────────────────────────────────────────
def test_recursive_publish_is_queued_not_recursive():
    bus = _bus()
    order: list[str] = []
    pub = _pub(bus)

    def handler(e):
        order.append(e.type.value)
        if e.type is EventType.TURN_STARTED:
            # Re-entrant publish: must be queued and dispatched after the current
            # event's handlers finish — never a recursive/nested dispatch.
            pub.publish(EventType.SPEECH_STARTED)

    bus.subscribe(handler)
    pub.publish(EventType.TURN_STARTED)
    pub.publish(EventType.TURN_COMPLETED)
    assert order == ["turn.started", "speech.started", "turn.completed"]


def test_deep_recursive_publish_does_not_overflow():
    bus = _bus()
    count = {"n": 0}
    pub = _pub(bus)

    def handler(e):
        count["n"] += 1
        if count["n"] < 500:
            pub.publish(EventType.TURN_STARTED)  # would blow the stack if recursive

    bus.subscribe(handler)
    pub.publish(EventType.TURN_STARTED)
    assert count["n"] == 500  # all handled iteratively (queued), no RecursionError


# ── deterministic replay ─────────────────────────────────────────────────────
def test_deterministic_replay():
    def run():
        bus = _bus()
        seen: list[tuple[int, str]] = []
        bus.subscribe(lambda e: seen.append((e.seq, e.type.value)))
        pub = _pub(bus, EventSource.TURN_ENGINE)
        for t in (EventType.TURN_STARTED, EventType.SPEECH_STARTED, EventType.TURN_COMPLETED):
            pub.publish(t)
        return seen

    assert run() == run()
