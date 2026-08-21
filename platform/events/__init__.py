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

**M10.6 (ADR-0035)** adds the `DurableOutbox` port — mutable per-event delivery state,
`PENDING` and `DELIVERED` only — and the durable write seam on `InProcessEventBus`, which is
optional and **disabled by default**. `events.persistence` stays unimported here for the same
M10.2 reason.

**M10.7 (ADR-0036)** completes the lifecycle — `RETRYING`, `DEAD_LETTERED`, `QUARANTINED`, the
`OUTBOX_LIFECYCLE` machine, `DeliveryOutcome`, the `DeliveryTransport` contract (no
implementation) and the pure `OutboxLadder`. **No worker, scheduler or driver lives here**:
recovery and restart re-dispatch are M10.8.

**M10.5 (ADR-0041)** adds retention — the **sole sanctioned exception** to the append-only
invariant (ED9/EV4). `EventRetention` deletes an event only when all five guards hold: a policy
is configured, `recorded_at` is inside a **closed** window from an injected clock, the
`evt_outbox` row is `DELIVERED` (the only drained state — `DEAD_LETTERED` and `QUARANTINED` are
terminal *failures* and keep their evidence), and a supplied `MeteredWatermark` covers it.
Anything absent or unreadable means **retain** (RT8). The attestation is a port `events/`
declares and never implements — no Billing import, no metering algorithm. The sweep is
caller-driven like `OutboxRecovery`, and the SQL adapter stays in `events.persistence`,
unimported here for the M10.2 reason.

**M10.8 (ADR-0037)** adds `OutboxRecovery` — the driver that finally *delivers*. It is
**caller-driven**: `recover(now, limit)` sweeps `PENDING` and due `RETRYING` entries once, in
`(seq, event_id)` order, on the same precedent `context`/`memory` use for their TTL sweeps.
Still **no scheduler, worker, thread, timer, `sleep` or polling loop**, and still **no concrete
`DeliveryTransport`** — the transport is injected, so `events/webhooks.py` stays untouched and
no second delivery mechanism appears.
"""

from .bus import (
    DurableWriteFailures,
    EventDispatcher,
    EventPublisher,
    EventRegistry,
    EventSubscriber,
    InProcessEventBus,
)
from .errors import DuplicateSubscription, EventBusError, EventBusShutdown, InvalidEvent
from .ladder import DELIVERY_RETRY_POLICY, LadderDecision, OutboxLadder
from .ports import (
    OUTBOX_LIFECYCLE,
    TERMINAL_OUTBOX_STATES,
    DeliveryClassifier,
    DeliveryOutcome,
    DeliveryTransport,
    DurableEventLog,
    DurableEventLogError,
    DurableOutbox,
    DurableWriteFailureHook,
    MeteredWatermark,
    NullMeteredWatermark,
    OutboxEntry,
    OutboxFailure,
    OutboxState,
    OutboxTrigger,
    RetentionCandidate,
    RetentionStore,
    RetentionWindow,
)
from .recovery import OutboxRecovery, default_classifier
from .retention import DRAINED_STATES, EventRetention, RetentionPolicy, drained
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
    "DurableOutbox",
    "DurableWriteFailureHook",
    "DurableWriteFailures",
    "OutboxEntry",
    "OutboxState",
    "OutboxTrigger",
    "OutboxFailure",
    "OUTBOX_LIFECYCLE",
    "TERMINAL_OUTBOX_STATES",
    "DeliveryOutcome",
    "DeliveryClassifier",
    "DeliveryTransport",
    "OutboxLadder",
    "OutboxRecovery",
    "MeteredWatermark",
    "NullMeteredWatermark",
    "RetentionCandidate",
    "RetentionStore",
    "RetentionWindow",
    "RetentionPolicy",
    "EventRetention",
    "DRAINED_STATES",
    "drained",
    "default_classifier",
    "LadderDecision",
    "DELIVERY_RETRY_POLICY",
    "emit_event",
    "sign_payload",
]
