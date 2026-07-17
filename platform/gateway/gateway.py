"""LiveEventGateway — external event streaming (M4.2).

Subscribes to the Internal Event Bus (`core.events.EventBus`) and streams runtime
events to external clients over pluggable transports (WebSocket, SSE). It owns
**external delivery only** — never Runtime state, never event creation. The Runtime
publishes exactly as M4.1 and never knows a gateway, a client, or a transport
exists: the gateway attaches from the outside via the bus's public contract.

Delivery is **non-blocking for the Runtime**: the bus subscriber (`_on_event`, run
synchronously during bus dispatch) serializes once and *offers* the frame to each
matching client's bounded `EventStream` — it never awaits, never does I/O, and is
fully isolated (a serialization or client failure becomes a metric + local
disconnect, never an exception into the bus). Each client's transport drains its
stream asynchronously, so a slow client cannot affect the Runtime or other clients.
"""

from __future__ import annotations

from core.events import Event, EventBus, Subscription

from .connection import ConnectionManager
from .errors import GatewayShutdown, UnknownClient
from .metrics import GatewayMetrics
from .scope import EventChannel
from .serialization import EventSerializer
from .session import ClientSession
from .stream import OfferResult
from .subscription import SubscriptionManager


class LiveEventGateway:
    def __init__(
        self,
        *,
        serializer: EventSerializer | None = None,
        connections: ConnectionManager | None = None,
        subscriptions: SubscriptionManager | None = None,
        metrics: GatewayMetrics | None = None,
    ) -> None:
        # NB: ConnectionManager defines __len__, so an empty one is falsy — use
        # explicit `is not None` checks, never `or`, for the manager defaults.
        self._metrics = metrics if metrics is not None else GatewayMetrics()
        self._serializer = serializer if serializer is not None else EventSerializer()
        self._connections = (
            connections if connections is not None
            else ConnectionManager(metrics=self._metrics)
        )
        self._subscriptions = (
            subscriptions if subscriptions is not None
            else SubscriptionManager(metrics=self._metrics)
        )
        self._attachments: list[tuple[EventBus, Subscription]] = []
        self._is_shutdown = False

    # ── read-only exposure ──────────────────────────────────────────────────
    @property
    def metrics(self) -> GatewayMetrics:
        return self._metrics

    @property
    def connections(self) -> ConnectionManager:
        return self._connections

    @property
    def subscriptions(self) -> SubscriptionManager:
        return self._subscriptions

    @property
    def serializer(self) -> EventSerializer:
        return self._serializer

    @property
    def is_shutdown(self) -> bool:
        return self._is_shutdown

    # ── bus attachment (Runtime never initiates this) ──────────────────────
    def attach(self, bus: EventBus) -> Subscription:
        """Subscribe to an Internal Event Bus. May be attached to many buses (e.g.
        one per session). The Runtime is unaware — this uses the bus's public API."""
        if self._is_shutdown:
            raise GatewayShutdown("cannot attach a shut-down gateway")
        sub = bus.subscribe(self._on_event)
        self._attachments.append((bus, sub))
        return sub

    def detach_all(self) -> None:
        for bus, sub in self._attachments:
            try:
                bus.unsubscribe(sub)
            except Exception:  # noqa: BLE001 — detach is best-effort
                pass
        self._attachments.clear()

    # ── client lifecycle (delegated) ────────────────────────────────────────
    def connect(self, client_id: str, *, context: dict | None = None) -> ClientSession:
        if self._is_shutdown:
            raise GatewayShutdown("cannot connect to a shut-down gateway")
        return self._connections.connect(client_id, context=context)

    def disconnect(self, client_id: str) -> None:
        self._connections.disconnect(client_id)

    def subscribe(self, client_id: str, channel: EventChannel) -> bool:
        session = self._connections.get(client_id)
        if session is None:
            raise UnknownClient(f"no connected client {client_id!r}")
        return self._subscriptions.subscribe(session, channel)

    def unsubscribe(self, client_id: str, channel: EventChannel) -> None:
        session = self._connections.get(client_id)
        if session is None:
            raise UnknownClient(f"no connected client {client_id!r}")
        self._subscriptions.unsubscribe(session, channel)

    # ── the bus subscriber (sync, non-blocking, fully isolated) ─────────────
    def _on_event(self, event: Event) -> None:
        if self._is_shutdown:
            return
        try:
            self._metrics.events_received += 1
            frame = self._serializer.serialize(event)
        except Exception:  # noqa: BLE001 — never raise into the bus/Runtime
            self._metrics.serialization_failures += 1
            return
        to_disconnect: list[str] = []
        for session in self._subscriptions.matching(self._connections.sessions(), event):
            try:
                result = session.stream.offer(frame)
            except Exception:  # noqa: BLE001 — one client never affects others/Runtime
                to_disconnect.append(session.id)
                continue
            if result is OfferResult.OVERFLOW:  # slow-consumer (DISCONNECT policy)
                self._metrics.slow_disconnects += 1
                to_disconnect.append(session.id)
            elif result is OfferResult.DROPPED:
                self._metrics.events_dropped += 1
            else:
                self._metrics.events_delivered += 1
        for cid in to_disconnect:
            self._connections.disconnect(cid)

    # ── shutdown ─────────────────────────────────────────────────────────────
    def shutdown(self) -> None:
        self._is_shutdown = True
        self.detach_all()
        self._connections.close_all()
