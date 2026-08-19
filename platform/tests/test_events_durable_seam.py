"""Durable write seam on `InProcessEventBus` (M10.6) — the executable form of ADR-0035
DW2/DW3/DW4.

Everything here uses **fake** durable sinks, not a database: the claims under test are about
*ordering and failure behaviour on the publish path*, and a real database would make these
tests slower without making them stricter. The SQL side is proven in
`test_events_outbox.py`, against file-backed SQLite.

No test here sleeps or measures wall-clock time. ED10 is proven by construction — a sink
that blocks or raises does not reach the producer — rather than by a timing threshold the
repository never defined and CI could not hold stable.
"""

from __future__ import annotations

from dataclasses import replace

import pytest

from core.events import Event, EventSource, EventType
from events import InProcessEventBus
from events.ports import DurableEventLog


class FakeDurableLog(DurableEventLog):
    """Records what it was asked to persist, in order."""

    def __init__(self) -> None:
        self.appended: list[Event] = []

    def append(self, event: Event) -> bool:
        self.appended.append(event)
        return True

    def append_many(self, events):
        return sum(1 for e in events if self.append(e))

    def read_session(self, session_id: str):
        return tuple(e for e in self.appended if e.session_id == session_id)

    def read_all(self):
        return tuple(self.appended)

    def count(self) -> int:
        return len(self.appended)

    def ids(self) -> list[str]:
        return [e.id for e in self.appended]


class FailingDurableLog(FakeDurableLog):
    """Fails every append (or only for chosen event ids)."""

    def __init__(self, *, only: set[str] | None = None) -> None:
        super().__init__()
        self.only = only
        self.attempts = 0

    def append(self, event: Event) -> bool:
        self.attempts += 1
        if self.only is None or event.id in self.only:
            raise RuntimeError("disk on fire")
        return super().append(event)


def make_event(event_id: str, *, session_id: str = "s-1", turn: int | None = None) -> Event:
    """A deterministic envelope. `seq=0` — the bus is the sole sequence authority."""
    return Event(
        id=event_id,
        type=EventType.RUNTIME_STARTED,
        ts=1.0,
        seq=0,
        session_id=session_id,
        source=EventSource.RUNTIME,
        org_id="org-1",
        conversation_id="c-1",
        correlation_id=None,
        turn=turn,
        data={},
        metadata={},
    )


# ── A. durable before dispatch, asserted from inside a subscriber ───────────────


def test_event_is_already_durable_when_a_subscriber_observes_it():
    """ADR-0035 DW3, ED6. Asserted from *inside* the subscriber, which is the only place
    the ordering claim is falsifiable: if the write happened after dispatch, the log would
    still be empty at this moment."""
    sink = FakeDurableLog()
    bus = InProcessEventBus(durable=sink)
    seen_durable_at_dispatch: list[bool] = []

    def subscriber(event: Event) -> None:
        seen_durable_at_dispatch.append(event.id in sink.ids())

    bus.subscribe(subscriber)
    bus.publish(make_event("e-1"))

    assert seen_durable_at_dispatch == [True]


def test_durable_write_precedes_enqueue_for_every_event():
    sink = FakeDurableLog()
    bus = InProcessEventBus(durable=sink)
    delivered: list[str] = []
    bus.subscribe(lambda e: delivered.append(e.id))

    for i in range(5):
        bus.publish(make_event(f"e-{i}"))

    assert sink.ids() == [f"e-{i}" for i in range(5)]
    assert delivered == sink.ids()


def test_is_durable_reports_whether_a_sink_is_attached():
    assert InProcessEventBus().is_durable is False
    assert InProcessEventBus(durable=FakeDurableLog()).is_durable is True


# ── B. sequence authority ───────────────────────────────────────────────────────


def test_persisted_seq_equals_the_bus_assigned_seq():
    """ED3 — the bus assigns, the log persists verbatim. The envelope handed to the sink
    must already carry the stamp, never `seq=0`."""
    sink = FakeDurableLog()
    bus = InProcessEventBus(durable=sink)

    stamped = [bus.publish(make_event(f"e-{i}")) for i in range(3)]

    assert [e.seq for e in stamped] == [1, 2, 3]
    assert [e.seq for e in sink.appended] == [1, 2, 3]
    assert all(e.seq != 0 for e in sink.appended)


def test_the_durable_sink_is_never_asked_to_assign_a_sequence():
    """The port has no sequence method at all; this asserts the bus does not route around
    that by publishing an unstamped envelope and expecting the log to fill it in."""
    sink = FakeDurableLog()
    bus = InProcessEventBus(durable=sink)
    bus.publish(make_event("e-1"))
    assert sink.appended[0].seq == bus.last_seq == 1


# ── C. reentrant publish ────────────────────────────────────────────────────────


def test_reentrant_event_is_durable_before_it_is_dispatched():
    """DW3 step 6. The inner event is persisted at *its own* publish call, so it is durable
    long before the outer drain reaches it — no special case in the bus."""
    sink = FakeDurableLog()
    bus = InProcessEventBus(durable=sink)
    order: list[str] = []

    def outer(event: Event) -> None:
        if event.id == "outer":
            order.append("dispatch:outer")
            bus.publish(make_event("inner"))
            # The inner event is already durable here, and has NOT yet been dispatched.
            assert "inner" in sink.ids()
            assert "dispatch:inner" not in order
        else:
            order.append("dispatch:inner")
            assert event.id in sink.ids()

    bus.subscribe(outer)
    bus.publish(make_event("outer"))

    assert order == ["dispatch:outer", "dispatch:inner"]
    assert sink.ids() == ["outer", "inner"]


def test_reentrant_publish_still_queues_rather_than_recursing():
    """The M4.1 guarantee, re-asserted with the seam attached."""
    sink = FakeDurableLog()
    bus = InProcessEventBus(durable=sink)
    depth = {"current": 0, "max": 0}

    def handler(event: Event) -> None:
        depth["current"] += 1
        depth["max"] = max(depth["max"], depth["current"])
        if event.id == "a":
            bus.publish(make_event("b"))
        depth["current"] -= 1

    bus.subscribe(handler)
    bus.publish(make_event("a"))
    assert depth["max"] == 1


# ── D. durable-write failure (DW4) ──────────────────────────────────────────────


def test_a_failed_durable_write_is_not_raised_into_the_producer():
    """`core.events.EventBus.publish`: "Must not raise into the producer." ED10, AR-31."""
    bus = InProcessEventBus(durable=FailingDurableLog())
    stamped = bus.publish(make_event("e-1"))  # must not raise
    assert stamped.id == "e-1"
    assert stamped.seq == 1  # the contract still returns the stamped envelope


def test_a_failed_durable_write_is_never_dispatched():
    """ED6 stays exceptionless: no subscriber may observe a non-durable event."""
    bus = InProcessEventBus(durable=FailingDurableLog())
    delivered: list[str] = []
    bus.subscribe(lambda e: delivered.append(e.id))

    bus.publish(make_event("e-1"))

    assert delivered == []


def test_the_failure_hook_fires_exactly_once_per_failed_write():
    calls: list[tuple[str, str]] = []
    bus = InProcessEventBus(
        durable=FailingDurableLog(),
        on_durable_error=lambda event, exc: calls.append((event.id, str(exc))),
    )

    bus.publish(make_event("e-1"))

    assert len(calls) == 1
    assert calls[0][0] == "e-1"
    assert "disk on fire" in calls[0][1]


def test_seq_is_not_rewound_or_reused_after_a_failure():
    """DW4 point 3. A gap is honest; reuse would give two events one identity in the
    ordering dimension (ED3)."""
    sink = FailingDurableLog(only={"bad"})
    bus = InProcessEventBus(durable=sink)
    delivered: list[tuple[str, int]] = []
    bus.subscribe(lambda e: delivered.append((e.id, e.seq)))

    first = bus.publish(make_event("good-1"))
    failed = bus.publish(make_event("bad"))
    third = bus.publish(make_event("good-2"))

    assert (first.seq, failed.seq, third.seq) == (1, 2, 3)
    assert bus.last_seq == 3
    assert delivered == [("good-1", 1), ("good-2", 3)]  # 2 is a gap, not reused
    assert sink.ids() == ["good-1", "good-2"]


def test_failures_are_counted_in_bounded_in_process_accounting():
    bus = InProcessEventBus(durable=FailingDurableLog())

    for i in range(3):
        bus.publish(make_event(f"e-{i}"))

    failures = bus.durable_failures
    assert failures.total == 3
    assert [event_id for event_id, _seq, _err in failures.recent] == ["e-0", "e-1", "e-2"]
    assert all("disk on fire" in err for _id, _seq, err in failures.recent)


def test_failure_accounting_is_bounded_and_never_grows_without_limit():
    """The record is deliberately bounded — an unbounded list of failures is a memory leak
    that arrives exactly when the process is already unhealthy."""
    bus = InProcessEventBus(durable=FailingDurableLog())
    bus.durable_failures._recent = type(bus.durable_failures._recent)(maxlen=2)

    for i in range(10):
        bus.publish(make_event(f"e-{i}"))

    assert bus.durable_failures.total == 10  # the total is never truncated
    assert len(bus.durable_failures.recent) == 2  # the ring is


def test_a_raising_failure_hook_never_reaches_the_producer():
    def exploding_hook(event: Event, exc: Exception) -> None:
        raise ValueError("hook is broken too")

    bus = InProcessEventBus(durable=FailingDurableLog(), on_durable_error=exploding_hook)
    bus.publish(make_event("e-1"))  # must not raise
    assert bus.durable_failures.total == 1


def test_an_already_recorded_event_is_a_success_not_a_failure():
    """ED2 — the log is idempotent on identity, so a redelivered append returns `False`
    (already present) and the event must still be dispatched. Treating it as a failure would
    turn the expected at-least-once case into a dropped event."""

    class AlwaysAlreadyPresent(FakeDurableLog):
        def append(self, event: Event) -> bool:
            super().append(event)
            return False

    bus = InProcessEventBus(durable=AlwaysAlreadyPresent())
    delivered: list[str] = []
    bus.subscribe(lambda e: delivered.append(e.id))

    bus.publish(make_event("e-1"))

    assert delivered == ["e-1"]
    assert bus.durable_failures.total == 0


# ── ED10: a slow or dead sink never stalls or raises into the producer ─────────


def test_a_sink_that_always_fails_never_stalls_the_producer():
    """ED10/AR-31 proven by construction, not by a stopwatch: every publish returns
    normally and the conversation continues, no matter how badly durability is doing."""
    bus = InProcessEventBus(durable=FailingDurableLog())
    returned = [bus.publish(make_event(f"e-{i}")) for i in range(50)]

    assert len(returned) == 50
    assert [e.seq for e in returned] == list(range(1, 51))
    assert bus.durable_failures.total == 50


def test_a_slow_sink_does_not_change_producer_facing_behaviour():
    """A sink that does arbitrary work before returning is indistinguishable to the producer
    from a fast one — no timing assertion, so no flake."""
    class SlowButWorkingLog(FakeDurableLog):
        def append(self, event: Event) -> bool:
            for _ in range(10_000):  # deterministic work, not a sleep
                pass
            return super().append(event)

    bus = InProcessEventBus(durable=SlowButWorkingLog())
    delivered: list[str] = []
    bus.subscribe(lambda e: delivered.append(e.id))

    stamped = bus.publish(make_event("e-1"))

    assert stamped.seq == 1
    assert delivered == ["e-1"]


# ── E. default behaviour: no sink ⇒ the M4.1 bus, unchanged ─────────────────────


def test_the_seam_is_disabled_by_default():
    bus = InProcessEventBus()
    assert bus.is_durable is False
    assert bus.durable_failures.total == 0


def test_without_a_sink_publish_dispatches_exactly_as_before():
    bus = InProcessEventBus()
    delivered: list[tuple[str, int]] = []
    bus.subscribe(lambda e: delivered.append((e.id, e.seq)))

    bus.publish(make_event("e-1"))
    bus.publish(make_event("e-2"))

    assert delivered == [("e-1", 1), ("e-2", 2)]
    assert bus.last_seq == 2


def test_without_a_sink_a_raising_subscriber_is_still_isolated():
    bus = InProcessEventBus()
    survived: list[str] = []
    bus.subscribe(lambda e: (_ for _ in ()).throw(RuntimeError("boom")))
    bus.subscribe(lambda e: survived.append(e.id))

    bus.publish(make_event("e-1"))

    assert survived == ["e-1"]


def test_invalid_event_still_raises_before_any_durable_write():
    """The pre-existing precondition check (`InvalidEvent`) is unchanged and runs *before*
    the seam, so a malformed envelope never reaches storage."""
    from events.errors import InvalidEvent

    sink = FakeDurableLog()
    bus = InProcessEventBus(durable=sink)
    bad = replace(make_event("e-1"), type="not-an-event-type")

    with pytest.raises(InvalidEvent):
        bus.publish(bad)
    assert sink.appended == []


def test_a_shut_down_bus_does_not_write_durably():
    sink = FakeDurableLog()
    bus = InProcessEventBus(durable=sink)
    bus.shutdown()

    bus.publish(make_event("e-1"))

    assert sink.appended == []
