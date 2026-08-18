"""`SqlDurableEventLog` (M10.2) — the durable, append-only implementation of
`events.ports.DurableEventLog`.

Appends `core.events.Event` envelopes to `evt_event` and reads them back in the order the
bus assigned. It is an **append point only**: there is no update path and no delete path,
mirroring `SqlAuditStore` (M8.9) and `SqlBillingEventStore` (M9.4), the two append-only
stores this repository already runs.

Named a *log*, not a store or a replay: "event store" is Billing's noun
(`SqlBillingEventStore`) and "replay" is reserved for `platform/replay/` (ADR-0032 ED11,
ADR-0034 DS-terminology).

**What this class deliberately does not do at M10.2.** It does not subscribe to the
`EventBus`, does not dispatch, does not retry, does not dead-letter, does not quarantine
and does not expire anything. Delivery is M10.6/M10.7 and retention is M10.5. This is the
persistence primitive those milestones build on, and keeping it that narrow is what stops
"durable persistence" and "durable delivery" collapsing into one class that does neither
well (ADR-0032, "What this ADR does not decide").

**Transactions.** Every write is committed through the caller's session and rolls back
whole. `append_many` is one transaction: a failure records none of the batch, so a caller
never has to reason about a half-written batch (ADR-0032 ED6).

**Idempotence.** Delivery is at-least-once (ED2), so re-appending an already-recorded
`event.id` is ordinary traffic and is a no-op — not an error. This is checked before insert
rather than by catching an `IntegrityError`, because a failed insert poisons the session's
transaction and would take the rest of a batch down with it.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable, Iterable, Sequence

from sqlalchemy import func
from sqlmodel import select

from core.events import Event
from events.ports import DurableEventLog

from .database import EventDatabase
from .mapping import to_event, to_row
from .models import DurableEventRow

log = logging.getLogger("voxvoice.events.durable_log")


class SqlDurableEventLog(DurableEventLog):
    """Durable append-only event log over `evt_event`.

    The clock is injected as `Callable[[], float]` — the idiom `events/` already uses
    (`InProcessEventBus(clock=..., id_factory=...)`) rather than importing another plane's
    `Clock` ABC, which `events/` may not do. It supplies `recorded_at` only; the envelope's
    own `ts` is the producer's and is never overwritten.
    """

    def __init__(
        self,
        database: EventDatabase,
        *,
        clock: Callable[[], float] | None = None,
    ) -> None:
        self._db = database
        self._clock = clock or time.time

    # ── append ──────────────────────────────────────────────────────────────
    def append(self, event: Event) -> bool:
        session = self._db.session
        if self._exists(event.id):
            return False
        session.add(to_row(event, recorded_at=self._clock()))
        session.commit()
        return True

    def append_many(self, events: Iterable[Event]) -> int:
        session = self._db.session
        recorded_at = self._clock()
        added = 0
        # De-duplicate within the batch as well as against the table: the same event
        # arriving twice inside one batch is the same at-least-once case, and two rows
        # with one primary key would fail the whole transaction at flush.
        seen: set[str] = set()
        try:
            for event in events:
                if event.id in seen or self._exists(event.id):
                    continue
                seen.add(event.id)
                session.add(to_row(event, recorded_at=recorded_at))
                added += 1
            session.commit()
        except Exception:
            session.rollback()
            raise
        return added

    # ── read ────────────────────────────────────────────────────────────────
    def read_session(self, session_id: str) -> Sequence[Event]:
        rows = self._db.session.exec(
            select(DurableEventRow)
            .where(DurableEventRow.session_id == session_id)
            .order_by(DurableEventRow.seq, DurableEventRow.event_id)
        ).all()
        return tuple(to_event(r) for r in rows)

    def read_all(self) -> Sequence[Event]:
        rows = self._db.session.exec(
            select(DurableEventRow).order_by(DurableEventRow.seq, DurableEventRow.event_id)
        ).all()
        return tuple(to_event(r) for r in rows)

    def count(self) -> int:
        return int(
            self._db.session.exec(select(func.count()).select_from(DurableEventRow)).one()
        )

    # ── internals ───────────────────────────────────────────────────────────
    def _exists(self, event_id: str) -> bool:
        return self._db.session.get(DurableEventRow, event_id) is not None
