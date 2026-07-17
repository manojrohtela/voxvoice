"""events — Event delivery infrastructure (Constitution §A5, §A14; ADR-0004/0006).

Holds Event Bus *implementations* (in-process now, distributed broker later —
behind the `core.events.EventBus` interface), the delivery outbox, and the
webhook dispatcher. Infra layer: imports `core` and the stdlib, **never** `api`
or `runtime` (AR — no upward imports; this broke the Phase-1 `api⇄webhooks`
cycle at M1).

At M1 only the webhook dispatcher was present (moved and decoupled from `api`). The
in-process **Event Bus** landed at **M4.1** (`InProcessEventBus` + dispatcher /
registry / publisher / subscriber, behind `core.events.EventBus`). The durable
outbox + distributed broker are M4.2+.
"""

from .bus import (
    EventDispatcher,
    EventPublisher,
    EventRegistry,
    EventSubscriber,
    InProcessEventBus,
)
from .errors import DuplicateSubscription, EventBusError, EventBusShutdown, InvalidEvent
from .webhooks import emit_event, sign_payload

__all__ = [
    "InProcessEventBus",
    "EventDispatcher",
    "EventRegistry",
    "EventPublisher",
    "EventSubscriber",
    "EventBusError",
    "DuplicateSubscription",
    "InvalidEvent",
    "EventBusShutdown",
    "emit_event",
    "sign_payload",
]
