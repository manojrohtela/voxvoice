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

**M10.7 (ADR-0036).** The class now also *records* ladder transitions: `begin_attempt`,
`record_failure` (which persists the transition **and** its `evt_outbox_failure` evidence in
one commit), `failures` and the `due(now)` query. The decision itself is not made here — the
pure `OutboxLadder` makes it, and this class validates it a second time through
`core.fsm` before writing, so two independent statements of the lifecycle must agree.

**Scope — what this class still does not do.** It never delivers anything, never polls,
never sleeps, never loops and never schedules. `due(now)` is a **query**: nothing in M10.7
calls it on a timer or acts on what it returns. **Recovery, restart re-dispatch and the
driver that performs them are M10.8** (ADR-0035 DW7, reaffirmed by ADR-0036 RL1) — and the
absence of a driver here is the reason that boundary holds. There is no redrive out of
`DEAD_LETTERED` and no operator quarantine (RL3/RL4); retention is M10.5 and metering M10.10.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Iterable, Sequence

from sqlalchemy import func
from sqlmodel import select

from core.events import Event
from core.fsm import StateMachine, TerminalStateError
from core.policies import RetryPolicy
from events.ladder import OutboxLadder
from events.ports import (
    OUTBOX_LIFECYCLE,
    TERMINAL_OUTBOX_STATES,
    DeliveryOutcome,
    DurableEventLog,
    DurableOutbox,
    OutboxEntry,
    OutboxFailure,
    OutboxState,
    OutboxTrigger,
)

from .database import EventDatabase
from .durable_log import SqlDurableEventLog
from .mapping import to_row
from .models import DurableEventRow, DurableOutboxFailureRow, DurableOutboxRow


def _to_entry(row: DurableOutboxRow) -> OutboxEntry:
    return OutboxEntry(
        event_id=row.event_id,
        seq=row.seq,
        state=OutboxState(row.state),
        created_at=row.created_at,
        delivered_at=row.delivered_at,
        attempt_count=row.attempt_count,
        next_attempt_at=row.next_attempt_at,
        dead_lettered_at=row.dead_lettered_at,
        quarantined_at=row.quarantined_at,
        quarantine_reason=row.quarantine_reason,
    )


def _to_failure(row: DurableOutboxFailureRow) -> OutboxFailure:
    return OutboxFailure(
        id=row.id,
        event_id=row.event_id,
        attempt=row.attempt,
        outcome=DeliveryOutcome(row.outcome),
        occurred_at=row.occurred_at,
        error=row.error,
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
        policy: RetryPolicy | None = None,
    ) -> None:
        self._db = database
        self._clock = clock or time.time
        # Reads are the log's; composing it keeps one definition of every ordered query.
        self._log = SqlDurableEventLog(database, clock=self._clock)
        # M10.7: the ladder decides, this class records. Injected so the bound is configurable
        # and so no retry number ever appears in the branches below (ADR-0032 ED7).
        self._ladder = OutboxLadder(policy)

    @property
    def ladder(self) -> OutboxLadder:
        """The retry ladder these transitions apply. Read-only."""
        return self._ladder

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
        """`PENDING → DELIVERED` or, since M10.7, `RETRYING → DELIVERED`.

        Already-delivered is `False`, not an error: at-least-once (ED2) makes a repeated mark
        ordinary traffic, and this is the repository's idempotent-scheduler convention — already
        in the established state means *nothing to do*, while **any other refusal stays loud**.
        A dead-lettered or quarantined entry is not silently marked delivered; it raises.
        """
        session = self._db.session
        row = session.get(DurableOutboxRow, event_id)
        if row is None or row.state == OutboxState.DELIVERED.value:
            return False  # unknown or already delivered — idempotent (ED2)
        self._guard_not_terminal(row, "mark delivered")
        self._fire(row, OutboxTrigger.DELIVER)
        try:
            row.state = OutboxState.DELIVERED.value
            row.delivered_at = self._clock()
            # A delivered entry is not due for anything; leaving a stale instant here would make
            # `due(now)` report a finished entry forever.
            row.next_attempt_at = None
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
        return self._count_state(OutboxState.PENDING)

    # ── M10.12 operator surfaces (ADR-0042 HD7/HD8) ─────────────────────────

    def count_dead_lettered(self) -> int:
        """DLQ depth — a read-only, point-in-time total."""
        return self._count_state(OutboxState.DEAD_LETTERED)

    def count_quarantined(self) -> int:
        """Quarantine count — a read-only, point-in-time total."""
        return self._count_state(OutboxState.QUARANTINED)

    def _count_state(self, state: OutboxState) -> int:
        """`COUNT(*)` over one outbox state, served by `ix_evt_outbox_state_seq`.

        One helper rather than three near-identical queries: the three counts differ only in the
        state they filter on, and writing that difference once is what stops them drifting apart.
        **Reads only** — no `add`, no `commit`, no flush of pending work, so a count can never
        move an entry (HD9, HI1, HI2).
        """
        return int(
            self._db.session.exec(
                select(func.count())
                .select_from(DurableOutboxRow)
                .where(DurableOutboxRow.state == state.value)
            ).one()
        )

    # ── DurableOutbox: M10.7 ladder transitions (ADR-0036) ──────────────────

    def begin_attempt(self, event_id: str) -> OutboxEntry | None:
        """Count an attempt that is **starting** (ADR-0036 RL8)."""
        session = self._db.session
        row = session.get(DurableOutboxRow, event_id)
        if row is None:
            return None
        self._guard_not_terminal(row, "begin an attempt on")
        try:
            row.attempt_count += 1
            session.add(row)
            session.commit()
        except Exception:
            session.rollback()
            raise
        return _to_entry(row)

    def record_failure(
        self,
        event_id: str,
        outcome: DeliveryOutcome,
        *,
        error: str = "",
        policy: RetryPolicy | None = None,
    ) -> OutboxEntry | None:
        """Apply one failed attempt's transition and append its evidence — **one transaction**.

        The ladder decides; this only records. A `RETRYING` row whose failure row is missing (or
        a failure row with no transition) is the fact/event disagreement ED6 forbids, one layer
        up, so the two writes share a commit and roll back together.
        """
        if outcome is DeliveryOutcome.SUCCESS:
            raise ValueError("record_failure is for failures; use mark_delivered for success")

        session = self._db.session
        row = session.get(DurableOutboxRow, event_id)
        if row is None:
            return None
        self._guard_not_terminal(row, "record a failure against")

        ladder = OutboxLadder(policy) if policy is not None else self._ladder
        now = self._clock()
        decision = ladder.decide(outcome, attempt_count=row.attempt_count, now=now)
        # Validate through the machine rather than trusting the ladder: two independent
        # statements of the lifecycle that must agree, and a mismatch raises instead of writing.
        self._fire(row, decision.trigger)

        try:
            # The evidence records the attempt that failed. A permanent failure does not consume
            # the ladder (ED8), so `attempt_count` is left exactly where it was.
            session.add(
                DurableOutboxFailureRow(
                    event_id=event_id,
                    attempt=row.attempt_count,
                    outcome=outcome.value,
                    occurred_at=now,
                    error=error,
                )
            )
            row.state = decision.state.value
            row.next_attempt_at = decision.next_attempt_at
            if decision.state is OutboxState.DEAD_LETTERED:
                row.dead_lettered_at = now
            elif decision.state is OutboxState.QUARANTINED:
                row.quarantined_at = now
                row.quarantine_reason = error
            session.add(row)
            session.commit()
        except Exception:
            session.rollback()
            raise
        return _to_entry(row)

    def failures(self, event_id: str) -> Sequence[OutboxFailure]:
        rows = self._db.session.exec(
            select(DurableOutboxFailureRow)
            .where(DurableOutboxFailureRow.event_id == event_id)
            .order_by(DurableOutboxFailureRow.attempt, DurableOutboxFailureRow.id)
        ).all()
        return tuple(_to_failure(r) for r in rows)

    def due(self, now: float) -> Sequence[OutboxEntry]:
        """Entries whose backoff has elapsed — **a query, never an action** (ADR-0036 RL1).

        Nothing in M10.7 calls this on a timer or delivers what it returns; the recovery driver
        is M10.8. Ordered by `(seq, event_id)` so the answer is deterministic across calls and
        across restarts.
        """
        rows = self._db.session.exec(
            select(DurableOutboxRow)
            .where(DurableOutboxRow.state == OutboxState.RETRYING.value)
            .where(DurableOutboxRow.next_attempt_at != None)  # noqa: E711 — SQL NULL, not `is`
            .where(DurableOutboxRow.next_attempt_at <= now)
            .order_by(DurableOutboxRow.seq, DurableOutboxRow.event_id)
        ).all()
        return tuple(_to_entry(r) for r in rows)

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

    # ── M10.7 lifecycle guards (ADR-0036 RL2) ───────────────────────────────

    def _guard_not_terminal(self, row: DurableOutboxRow, action: str) -> None:
        """Refuse to touch a finished entry, loudly.

        `DELIVERED`, `DEAD_LETTERED` and `QUARANTINED` are terminal, and terminality is read off
        the machine rather than from a list here — so this cannot drift from `OUTBOX_LIFECYCLE`.
        Silence would let a stale caller resurrect a closed entry, which is the failure the
        Collections lifecycle refuses by construction.
        """
        state = OutboxState(row.state)
        if state in TERMINAL_OUTBOX_STATES:
            raise TerminalStateError(
                f"outbox entry {row.event_id!r} is {state.value}; cannot {action} it"
            )

    def _fire(self, row: DurableOutboxRow, trigger: OutboxTrigger) -> OutboxState:
        """Validate a transition through `core.fsm` **before** writing it.

        The ladder computed the target independently; running the machine as well means two
        statements of the lifecycle have to agree, and a disagreement raises
        (`InvalidTransition` / `TerminalStateError`) instead of persisting a state nobody
        authorized. No row is written here — this is validation, not mutation.
        """
        machine = StateMachine(
            OUTBOX_LIFECYCLE,
            OutboxState(row.state),
            machine_id=f"evt_outbox:{row.event_id}",
            clock=self._clock,
        )
        return machine.fire(trigger)
