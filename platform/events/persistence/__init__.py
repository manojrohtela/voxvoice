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

No SQLModel type escapes this package: callers hold the port, and the port speaks
`core.events.Event`.
"""

from __future__ import annotations

from .database import EventDatabase
from .durable_log import SqlDurableEventLog
from .mapping import to_event, to_row
from .models import EVENT_TABLES, DurableEventRow

__all__ = [
    "EventDatabase",
    "SqlDurableEventLog",
    "DurableEventRow",
    "EVENT_TABLES",
    "to_event",
    "to_row",
]
