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

**M10.2 (Epic 10)** adds the durable-storage *port* — `DurableEventLog` — exported here
so callers depend on the seam, not on storage (ADR-0034). The SQL implementation lives in
`events.persistence` and is **deliberately not imported here**: it is the one sub-package
permitted `sqlmodel`/`sqlalchemy`, and re-exporting it would drag a storage library into
every importer of `events` and dissolve the scoping the ADR exists to create. Import it
explicitly (`from events.persistence import SqlDurableEventLog`) at a composition root.
"""

from .bus import (
    EventDispatcher,
    EventPublisher,
    EventRegistry,
    EventSubscriber,
    InProcessEventBus,
)
from .errors import DuplicateSubscription, EventBusError, EventBusShutdown, InvalidEvent
from .ports import DurableEventLog, DurableEventLogError
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
    "DurableEventLog",
    "DurableEventLogError",
    "emit_event",
    "sign_payload",
]
