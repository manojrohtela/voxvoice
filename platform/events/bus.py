"""InProcessEventBus — the deterministic, in-process Event Bus (M4.1).

Implements `core.events.EventBus`: the single internal communication mechanism for
runtime events. Producers publish typed `Event` envelopes; the bus fans them out to
matching subscribers in a deterministic, ordered, run-to-completion loop. It owns
**event delivery only** — never runtime state.

Delivery model (deterministic + reentrancy-safe):
  - `publish` stamps an authoritative monotonic `seq`, appends to a FIFO queue, and
    — if a dispatch is not already running — drains the queue to completion.
  - A subscriber that publishes re-entrantly is **queued**, never recursively
    dispatched: the ongoing drain picks it up (recursive-publish protection +
    ordering + a non-blocking producer for the reentrant case).
  - Each event is delivered **exactly once** to each matching subscriber, in
    subscriber-registration order.
  - Subscribers are **isolated**: a raising subscriber is caught + logged; it never
    stops other subscribers, the drain, or corrupts the bus.

Scope (M4.1): in-process only. No durable persistence, no distributed broker, no
external transports (those are M4.2+). Strict producer/consumer async decoupling
(an outbox) is a durable-delivery concern deferred with them (DL-0017).

Durable seam (M10.6, ADR-0035 DW2/DW3): `InProcessEventBus` accepts an **optional**
`DurableEventLog`. When supplied, `publish` durably writes the event **after** assigning
`seq` and **before** enqueueing it, so no subscriber can ever observe an event that is not
already durable — including a reentrant one, which is persisted at its own `publish` call.
When it is omitted (the default) behaviour is exactly the M4.1 behaviour described above.
The bus stays the sole sequence authority, holds only the framework-neutral port, and
imports no storage library (ADR-0034 DS3/DS4). A failed durable write is never raised into
the producer and its event is never dispatched (DW4) — see `DurableWriteFailures`.
"""

from __future__ import annotations

import logging
import time
import uuid
from collections import deque
from collections.abc import Callable
from dataclasses import replace

from core.events import (
    Event,
    EventBus,
    EventHandler,
    EventMetadata,
    EventType,
    EventTypeFilter,
    Subscription,
)

from .errors import DuplicateSubscription, EventBusShutdown, InvalidEvent
from .ports import DurableEventLog, DurableWriteFailureHook

log = logging.getLogger("voxvoice.events.bus")


class DurableWriteFailures:
    """Bounded in-process accounting for durable writes that failed (ADR-0035 DW4).

    **A declared M10.6 limitation, stated plainly rather than implied by silence:** when the
    durable write fails the event is neither durably recorded nor delivered, and *this record
    does not survive process termination*. It is a bounded ring of the most recent failures
    plus a monotonic total — enough for an operator to see that durability is degrading, and
    deliberately not a durable failure history. **M10.7** closes the gap by turning a failed
    write into a persisted, retryable entry; until then this is what exists, and it is not
    presented as more.

    Bounded on purpose: an unbounded list of failures is a memory leak that arrives exactly
    when the process is already unhealthy.
    """

    def __init__(self, *, max_recent: int = 32) -> None:
        self._recent: deque[tuple[str, int, str]] = deque(maxlen=max_recent)
        self._total = 0

    def record(self, event: Event, exc: Exception) -> None:
        self._total += 1
        self._recent.append((event.id, event.seq, repr(exc)))

    @property
    def total(self) -> int:
        """Every durable-write failure since process start (never truncated)."""
        return self._total

    @property
    def recent(self) -> tuple[tuple[str, int, str], ...]:
        """The most recent failures as `(event_id, seq, repr(exception))`, oldest first."""
        return tuple(self._recent)

    def __len__(self) -> int:
        return self._total


class EventRegistry:
    """Insertion-ordered subscriber registry (ordered delivery). Rejects registering
    the same handler twice (runtime-safe duplicate handling)."""

    def __init__(self) -> None:
        self._subs: dict[int, tuple[EventHandler, EventTypeFilter]] = {}
        self._by_handler: dict[int, int] = {}  # id(handler) -> subscription id
        self._next = 0

    def add(self, handler: EventHandler, types: EventTypeFilter) -> Subscription:
        key = id(handler)
        if key in self._by_handler:
            raise DuplicateSubscription(
                f"handler {getattr(handler, '__qualname__', handler)!r} already subscribed"
            )
        self._next += 1
        sid = self._next
        self._subs[sid] = (handler, types)
        self._by_handler[key] = sid
        return Subscription(id=sid)

    def remove(self, sid: int) -> None:
        entry = self._subs.pop(sid, None)  # idempotent
        if entry is not None:
            self._by_handler.pop(id(entry[0]), None)

    def clear(self) -> None:
        self._subs.clear()
        self._by_handler.clear()

    def matching(self, event: Event) -> list[EventHandler]:
        """Handlers whose filter admits this event, in registration order."""
        return [h for (h, f) in self._subs.values() if f is None or event.type in f]

    def __len__(self) -> int:
        return len(self._subs)


class EventDispatcher:
    """Fan-out with per-subscriber isolation. Never raises."""

    def __init__(
        self,
        registry: EventRegistry,
        *,
        on_error: Callable[[EventHandler, Event, Exception], None] | None = None,
    ) -> None:
        self._registry = registry
        self._on_error = on_error

    def dispatch(self, event: Event) -> None:
        for handler in self._registry.matching(event):
            try:
                handler(event)
            except Exception as exc:  # noqa: BLE001 — subscriber isolation
                log.warning(
                    "event subscriber %r raised on %s (isolated): %s",
                    getattr(handler, "__qualname__", handler), event.type.value, exc,
                )
                if self._on_error is not None:
                    try:
                        self._on_error(handler, event, exc)
                    except Exception:  # noqa: BLE001 — error hook must not corrupt the bus
                        log.exception("event on_error hook raised (ignored)")


class InProcessEventBus(EventBus):
    def __init__(
        self,
        *,
        clock: Callable[[], float] | None = None,
        id_factory: Callable[[], str] | None = None,
        on_error: Callable[[EventHandler, Event, Exception], None] | None = None,
        durable: DurableEventLog | None = None,
        on_durable_error: DurableWriteFailureHook | None = None,
    ) -> None:
        self._registry = EventRegistry()
        self._dispatcher = EventDispatcher(self._registry, on_error=on_error)
        self._queue: deque[Event] = deque()
        self._seq = 0
        self._dispatching = False
        self._is_shutdown = False
        self._clock = clock or time.time
        self._id_factory = id_factory or (lambda: uuid.uuid4().hex)
        # Durable seam (M10.6, ADR-0035 DW2). Optional and **disabled by default**: with
        # `durable=None` this class is the M4.1 bus, unchanged. Typed by the port, so the
        # bus never learns that storage exists (ADR-0034 DS3/DS4).
        self._durable = durable
        self._on_durable_error = on_durable_error
        self._durable_failures = DurableWriteFailures()

    # ── read-only exposure ──────────────────────────────────────────────────
    @property
    def is_shutdown(self) -> bool:
        return self._is_shutdown

    @property
    def last_seq(self) -> int:
        return self._seq

    @property
    def subscriber_count(self) -> int:
        return len(self._registry)

    @property
    def is_durable(self) -> bool:
        """Whether a durable sink is attached (ADR-0035 DW2). `False` is the default."""
        return self._durable is not None

    @property
    def durable_failures(self) -> DurableWriteFailures:
        """Bounded in-process accounting for failed durable writes (ADR-0035 DW4)."""
        return self._durable_failures

    # ── EventBus contract ───────────────────────────────────────────────────
    def publish(self, event: Event) -> Event:
        if not isinstance(event.type, EventType):
            raise InvalidEvent(f"event.type must be an EventType, got {event.type!r}")
        if self._is_shutdown:
            log.debug("bus is shut down; dropping event %s", event.type.value)
            return event
        self._seq += 1
        stamped = replace(event, seq=self._seq)
        # Durable write (ADR-0035 DW3): after `seq` is assigned, so the log persists the
        # bus's authoritative value verbatim (ED3), and before the enqueue below, so no
        # subscriber can observe an event that is not already durable (ED6). A reentrant
        # publish is covered by the same line at its own call, not by a special case.
        if self._durable is not None and not self._persist(stamped):
            # DW4: not durable ⇒ not dispatched. `seq` is consumed and NOT rewound — reusing
            # it would give two events one identity in the ordering dimension (ED3). A gap is
            # honest evidence that a write failed. The producer is not raised into.
            return stamped
        self._queue.append(stamped)
        if self._dispatching:
            return stamped  # reentrant publish → the running drain picks it up (ordered)
        self._dispatching = True
        try:
            while self._queue and not self._is_shutdown:
                self._dispatcher.dispatch(self._queue.popleft())
        finally:
            self._dispatching = False
            if self._is_shutdown:
                self._queue.clear()
        return stamped

    def _persist(self, stamped: Event) -> bool:
        """Durably record one stamped event. Returns whether it may now be dispatched.

        Never raises: `core.events.EventBus.publish` is contractually *"must not raise into
        the producer"* (ADR-0032 ED10, AR-31), and a producer here is a live conversation.
        An already-recorded event is a **success** — the log is idempotent on identity and
        delivery is at-least-once (ED2), so redelivery is ordinary traffic, not a failure.
        """
        try:
            self._durable.append(stamped)
        except Exception as exc:  # noqa: BLE001 — DW4: the producer must never see this
            self._durable_failures.record(stamped, exc)
            log.error(
                "durable write failed for %s (seq=%d, id=%s); event NOT dispatched: %s",
                stamped.type.value, stamped.seq, stamped.id, exc,
            )
            if self._on_durable_error is not None:
                try:
                    self._on_durable_error(stamped, exc)
                except Exception:  # noqa: BLE001 — the hook must not corrupt the bus either
                    log.exception("durable-write failure hook raised (ignored)")
            return False
        return True

    def subscribe(
        self, handler: EventHandler, *, types: EventTypeFilter = None
    ) -> Subscription:
        if self._is_shutdown:
            raise EventBusShutdown("cannot subscribe on a shut-down bus")
        return self._registry.add(handler, types)

    def unsubscribe(self, subscription: Subscription) -> None:
        self._registry.remove(subscription.id)  # idempotent

    def shutdown(self) -> None:
        self._is_shutdown = True
        if not self._dispatching:
            self._queue.clear()
        self._registry.clear()

    # ── convenience factories (M4.1 components) ─────────────────────────────
    def publisher(self, metadata: EventMetadata) -> EventPublisher:
        """A publish handle bound to a source subsystem + session context."""
        return EventPublisher(self, metadata, clock=self._clock, id_factory=self._id_factory)

    def subscriber(
        self, handler: EventHandler, *, types: EventTypeFilter = None
    ) -> EventSubscriber:
        """Subscribe and return a self-unsubscribing handle."""
        return EventSubscriber(self, self.subscribe(handler, types=types), handler)


class EventPublisher:
    """A thin, source-bound publish handle. Producers hold one and emit `(type,
    payload)` — the envelope's identity/ordering/context are stamped here, so a
    producer never constructs a full `Event` and never knows its consumers."""

    def __init__(
        self,
        bus: EventBus,
        metadata: EventMetadata,
        *,
        clock: Callable[[], float] | None = None,
        id_factory: Callable[[], str] | None = None,
    ) -> None:
        self._bus = bus
        self._meta = metadata
        self._clock = clock or time.time
        self._id_factory = id_factory or (lambda: uuid.uuid4().hex)

    @property
    def metadata(self) -> EventMetadata:
        return self._meta

    def publish(
        self,
        type: EventType,
        data: dict | None = None,
        *,
        turn: int | None = None,
        correlation_id: str | None = None,
        metadata: dict | None = None,
    ) -> Event:
        event = Event(
            id=self._id_factory(),
            type=type,
            ts=self._clock(),
            seq=0,  # authoritative seq assigned by the bus at publish
            session_id=self._meta.session_id,
            source=self._meta.source,
            org_id=self._meta.org_id,
            conversation_id=self._meta.conversation_id,
            correlation_id=correlation_id or self._meta.correlation_id,
            turn=turn,
            data=data or {},
            metadata=metadata or {},
        )
        return self._bus.publish(event)  # returns the bus-stamped envelope (with seq)


class EventSubscriber:
    """A self-unsubscribing subscription handle (holds the underlying `Subscription`)."""

    def __init__(self, bus: EventBus, subscription: Subscription, handler: EventHandler) -> None:
        self._bus = bus
        self.subscription = subscription
        self.handler = handler

    @property
    def id(self) -> int:
        return self.subscription.id

    def unsubscribe(self) -> None:
        self._bus.unsubscribe(self.subscription)
