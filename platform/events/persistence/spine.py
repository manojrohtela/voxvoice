"""`SqlDurableEventSpine` (M10.6, ADR-0035) — the durable write side the bus attaches to.

Implements **both** Epic 10 write-side seams over one `EventDatabase`:

  * `events.ports.DurableEventLog` — the append-only history (`evt_event`); and
  * `events.ports.DurableOutbox` — the mutable delivery state (`evt_outbox`).

Two ports, one class, one transaction. The shape is `SqlAuditStore`'s (M8.9), which
likewise implements a write seam and a read seam over a shared session: *"an emitted audit
row is part of the same transaction as the business write that produced it."*

**Why both rows are written together (ADR-0035 DW1/DW5).** ED6 requires an event and the
state change that produced it to share one transaction. A runtime producer has no
originating transaction — `platform/runtime/**` has no `Database`, `Session` or
`UnitOfWork` — so for those events *the durable append is the state change*, and the outbox
entry announcing it must not be able to exist without it, or vice versa. Writing them in
one commit is what makes that true rather than merely intended. **ED6 is not weakened**: a
producer that does hold a transaction still writes its state and its event through one
session, exactly as `SqlUnitOfWork` + `SqlAuditStore` already do.

**Why the log is not modified to do this.** `SqlDurableEventLog` (M10.2) stays exactly what
it was — the pure append-only log — because a caller that wants history without delivery
state should not be forced to acquire an outbox. This class composes it for reads and does
its own two-row staging for writes, so the read queries exist once.

**Scope.** No retry, no backoff, no dead-letter, no quarantine, no failure history, no
recovery worker and no re-dispatch: M10.7 owns the ladder and M10.8 owns recovery
(ADR-0035 DW6/DW7). `mark_delivered` is the state transition M10.6 owns; its *caller*
arrives with the durable consumers at M10.7/M10.10, exactly as the bus seam ships without
runtime wiring (DW10).
"""

from __future__ import annotations

import time
from collections.abc import Callable, Iterable, Sequence

from sqlalchemy import func
from sqlmodel import select

from core.events import Event
from events.ports import DurableEventLog, DurableOutbox, OutboxEntry, OutboxState

from .database import EventDatabase
from .durable_log import SqlDurableEventLog
from .mapping import to_row
from .models import DurableEventRow, DurableOutboxRow


def _to_entry(row: DurableOutboxRow) -> OutboxEntry:
    return OutboxEntry(
        event_id=row.event_id,
        seq=row.seq,
        state=OutboxState(row.state),
        created_at=row.created_at,
        delivered_at=row.delivered_at,
    )


class SqlDurableEventSpine(DurableEventLog, DurableOutbox):
    """Durable event log + delivery outbox, written atomically.

    The clock is injected as `Callable[[], float]` — the idiom `events/` already uses
    (ADR-0034 DS11). It supplies `recorded_at` and the outbox timestamps; the envelope's own
    `ts` is the producer's and is never overwritten.
    """

    def __init__(
        self,
        database: EventDatabase,
        *,
        clock: Callable[[], float] | None = None,
    ) -> None:
        self._db = database
        self._clock = clock or time.time
        # Reads are the log's; composing it keeps one definition of every ordered query.
        self._log = SqlDurableEventLog(database, clock=self._clock)

    # ── DurableEventLog: write (both rows, one transaction) ─────────────────
    def append(self, event: Event) -> bool:
        session = self._db.session
        if self._exists(event.id):
            return False  # idempotent on identity (ED2) — redelivery is ordinary traffic
        try:
            self._stage(event, now=self._clock())
            session.commit()
        except Exception:
            session.rollback()
            raise
        return True

    def append_many(self, events: Iterable[Event]) -> int:
        session = self._db.session
        now = self._clock()
        added = 0
        # De-duplicate within the batch as well as against the table, for the M10.2 reason:
        # two rows with one primary key would fail the whole transaction at flush.
        seen: set[str] = set()
        try:
            for event in events:
                if event.id in seen or self._exists(event.id):
                    continue
                seen.add(event.id)
                self._stage(event, now=now)
                added += 1
            session.commit()
        except Exception:
            session.rollback()
            raise
        return added

    # ── DurableEventLog: read (delegated) ───────────────────────────────────
    def read_session(self, session_id: str) -> Sequence[Event]:
        return self._log.read_session(session_id)

    def read_all(self) -> Sequence[Event]:
        return self._log.read_all()

    def count(self) -> int:
        return self._log.count()

    # ── DurableOutbox ───────────────────────────────────────────────────────
    def mark_delivered(self, event_id: str) -> bool:
        session = self._db.session
        row = session.get(DurableOutboxRow, event_id)
        if row is None or row.state == OutboxState.DELIVERED.value:
            return False  # unknown or already delivered — idempotent (ED2)
        try:
            row.state = OutboxState.DELIVERED.value
            row.delivered_at = self._clock()
            session.add(row)
            session.commit()
        except Exception:
            session.rollback()
            raise
        return True

    def get(self, event_id: str) -> OutboxEntry | None:
        row = self._db.session.get(DurableOutboxRow, event_id)
        return None if row is None else _to_entry(row)

    def read_pending(self) -> Sequence[OutboxEntry]:
        rows = self._db.session.exec(
            select(DurableOutboxRow)
            .where(DurableOutboxRow.state == OutboxState.PENDING.value)
            .order_by(DurableOutboxRow.seq, DurableOutboxRow.event_id)
        ).all()
        return tuple(_to_entry(r) for r in rows)

    def count_pending(self) -> int:
        return int(
            self._db.session.exec(
                select(func.count())
                .select_from(DurableOutboxRow)
                .where(DurableOutboxRow.state == OutboxState.PENDING.value)
            ).one()
        )

    def count_entries(self) -> int:
        """Outbox rows. `count()` is `DurableEventLog`'s and counts *events*. One row per
        event means the two agree in the normal case; they could diverge only if retention
        (M10.5) ever removed one and not the other."""
        return int(
            self._db.session.exec(
                select(func.count()).select_from(DurableOutboxRow)
            ).one()
        )

    # ── internals ───────────────────────────────────────────────────────────
    def _stage(self, event: Event, *, now: float) -> None:
        """Stage both rows on the session **without committing** — the caller owns the
        transaction boundary, which is what makes the pair atomic (ADR-0035 DW1)."""
        session = self._db.session
        session.add(to_row(event, recorded_at=now))
        session.add(
            DurableOutboxRow(
                event_id=event.id,
                seq=event.seq,
                state=OutboxState.PENDING.value,
                created_at=now,
                delivered_at=None,
            )
        )

    def _exists(self, event_id: str) -> bool:
        return self._db.session.get(DurableEventRow, event_id) is not None
