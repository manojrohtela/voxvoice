"""events.persistence — durable storage for the event spine (M10.2).

**The one sub-package in `events/` permitted to import `sqlmodel`/`sqlalchemy`**
(ADR-0034 DS3/DS4). Everything else under `events/` keeps its existing budget, and
`events/bus.py` in particular stays a pure in-process dispatcher with no notion of storage.

That scoping is the decision, not an accident of layout: ADR-0031 IR1a granted Billing the
same exception for `billing/persistence/**` and *tightened* the boundary suite into
per-layer checks rather than relaxing it globally. ADR-0034 generalizes that pattern to
`core`-only edge packages, and `tests/test_events_boundary.py` is where it is enforced —
allowed here, forbidden one directory up.

Contents: the plane's own `EventDatabase` (never another plane's — DS5), the `evt_*` tables,
row ↔ envelope mapping, the `SqlDurableEventLog` implementation of `events.ports.DurableEventLog`,
and the migration chain continuing at `0034`.

**M10.7 (ADR-0036)** adds `evt_outbox_failure` (append-only per-attempt failure evidence),
migration `0036` — the first in this chain to `ALTER` rather than only create — and the ladder
transitions on `SqlDurableEventSpine`. It adds **no driver**: recovery and restart re-dispatch
are M10.8.

**M10.6 (ADR-0035)** adds `evt_outbox` (mutable per-event delivery state, `PENDING`/`DELIVERED`
only), migration `0035`, and `SqlDurableEventSpine` — which implements `DurableEventLog` **and**
`DurableOutbox` and writes both rows in **one transaction**, so an event and the entry announcing
it cannot disagree about whether they happened.

**M10.5 (ADR-0041)** adds `SqlRetentionStore` — the **only module in the repository permitted a
deletion statement**, and the executable form of EV4's *"outside the retention policy"* clause.
It is named explicitly in the EV4 guard's allowlist; every other module here remains delete-free,
and no module here (this one included) may update or merge. It needs **no migration**: the window
scan uses the already-indexed `evt_event.recorded_at` and the drained read uses `evt_outbox.state`,
so the chain stays at `0036`.

No SQLModel type escapes this package: callers hold the port, and the port speaks
`core.events.Event`.
"""

from __future__ import annotations

from .database import EventDatabase
from .durable_log import SqlDurableEventLog
from .mapping import to_event, to_row
from .models import (
    ALL_EVENT_TABLES,
    EVENT_TABLES,
    OUTBOX_FAILURE_TABLES,
    OUTBOX_RETRY_COLUMNS,
    OUTBOX_TABLES,
    DurableEventRow,
    DurableOutboxFailureRow,
    DurableOutboxRow,
)
from .retention import SqlRetentionStore
from .spine import SqlDurableEventSpine

__all__ = [
    "EventDatabase",
    "SqlDurableEventLog",
    "SqlDurableEventSpine",
    "SqlRetentionStore",
    "DurableEventRow",
    "DurableOutboxRow",
    "DurableOutboxFailureRow",
    "EVENT_TABLES",
    "OUTBOX_TABLES",
    "OUTBOX_FAILURE_TABLES",
    "OUTBOX_RETRY_COLUMNS",
    "ALL_EVENT_TABLES",
    "to_event",
    "to_row",
]
