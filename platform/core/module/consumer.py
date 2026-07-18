"""BusConsumer — a RuntimeModule that integrates through the Event Bus (M5.0).

The single reusable way an AI runtime module talks to the rest of the platform:
`attach(bus)` to observe events (isolated dispatch) and `publish(...)` to emit them.
Because it publishes standard `core.events.Event` envelopes to the shared bus, the
**Replay recorder observes and the Gateway streams its events automatically** — no
change to either (AR-30). Cross-session by design: `publish` stamps `source` +
per-event `session_id`, unlike the session-bound `EventPublisher`.

Owns bus integration mechanics only — provider-neutral, deterministic, isolated (a
handler failure is counted, never raised into the bus or the Runtime). Domain
modules override `handle_event`.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from typing import Any

from core.events import (
    Event,
    EventBus,
    EventSource,
    EventType,
    EventTypeFilter,
    Subscription,
)

from .module import RuntimeModule


class BusConsumer(RuntimeModule):
    def __init__(
        self,
        *,
        name: str,
        source: EventSource,
        types: EventTypeFilter = None,
        id_factory: Callable[[], str] | None = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(name=name, **kwargs)
        self._source = source
        self._types = types  # the event types this consumer wants (None → all)
        self._id_factory = id_factory or (lambda: uuid.uuid4().hex)
        self._attachments: list[tuple[EventBus, Subscription]] = []

    @property
    def attached(self) -> bool:
        return bool(self._attachments)

    # ── attach / detach (observe the bus) ──────────────────────────────────
    def attach(self, bus: EventBus) -> Subscription:
        """Subscribe this consumer's dispatch to a bus (may attach to several)."""
        sub = bus.subscribe(self._dispatch, types=self._types)
        self._attachments.append((bus, sub))
        return sub

    def detach(self) -> None:
        for bus, sub in self._attachments:
            try:
                bus.unsubscribe(sub)
            except Exception:  # noqa: BLE001 — best-effort
                pass
        self._attachments.clear()

    def subscribe(
        self, bus: EventBus, handler: Callable[[Event], None], *, types: EventTypeFilter = None
    ) -> Subscription:
        """Register an additional handler on a bus (isolation is the caller's)."""
        return bus.subscribe(handler, types=types)

    # ── publish (emit standard envelopes → replay + gateway see them free) ──
    def publish(
        self,
        type: EventType,
        data: dict | None = None,
        *,
        session_id: str,
        org_id: str = "",
        conversation_id: str | None = None,
        correlation_id: str | None = None,
        turn: int | None = None,
        metadata: dict | None = None,
    ) -> Event | None:
        """Build + publish one envelope to every attached bus. Returns the stamped
        event from the first bus (or None if not attached)."""
        out: Event | None = None
        for bus, _sub in self._attachments:
            event = Event(
                id=self._id_factory(),
                type=type,
                ts=self._clock(),
                seq=0,
                session_id=session_id,
                source=self._source,
                org_id=org_id,
                conversation_id=conversation_id,
                correlation_id=correlation_id,
                turn=turn,
                data=data or {},
                metadata=metadata or {},
            )
            stamped = bus.publish(event)
            out = out or stamped
            self.metrics.published += 1
        return out

    # ── dispatch (isolated; the module never breaks the bus) ────────────────
    def _dispatch(self, event: Event) -> None:
        if not self.should_handle(event):
            return
        self.metrics.handled += 1
        self.isolate(lambda: self.handle_event(event), what="event")

    def should_handle(self, event: Event) -> bool:
        """Filter hook (default: handle everything the type filter admitted). A
        common override ignores this consumer's own events to avoid loops."""
        return True

    def handle_event(self, event: Event) -> None:
        """Override to react to an observed event. Runs under isolation."""

    async def on_shutdown(self) -> None:
        self.detach()
