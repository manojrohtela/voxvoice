"""`SqlRetentionStore` (M10.5, ADR-0041) — the one sanctioned deletion path in the repository.

**This module is the sole exception to the append-only invariant** (ADR-0032 ED5/ED9/**EV4**:
*"no update or delete path exists on the durable event log **outside the retention policy**"*).
It is named in the EV4 guard's `PERMITTED_DELETION_MODULES` allowlist, and every other module
under `events/persistence/` is still forbidden a deletion statement. The allowlist covers
**removal only**: an update or merge path stays forbidden here exactly as it is everywhere else,
because EV4 sanctions removing a recorded event, never rewriting one (**ED5**).

**Deliberately dumb.** It selects on the window and deletes by id, and it evaluates **none** of
the five RT7 guards. The predicate lives whole in `events/retention.py`, where it can be read
in one place and tested without a database; a store that also decided would be a second, silent
copy of the rule. Over-selection here therefore cannot cause a deletion — the policy re-checks
the window in Python before any id reaches `delete` (**RT8**).

**Scope of the deletion.** `evt_event` rows, and nothing else. Not the `evt_outbox` row, not
`evt_outbox_failure` history, not any event outside the supplied ids. RT7 sanctions deleting
*an `evt_event` row*, and the exception is read no wider than it is written —
`DurableOutbox.count_entries` already anticipates exactly this, noting the two counts could
diverge *"only if retention (M10.5) ever removed one and not the other"*.

**No migration.** `evt_event.recorded_at` is already indexed and `evt_outbox.state` already
exists, so the window scan and the drained read are index work against the M10.2/M10.6 schema
(**RT11**). The chain stays at `0036`.
"""

from __future__ import annotations

from collections.abc import Sequence

from sqlmodel import select

from events.ports import OutboxState, RetentionCandidate, RetentionStore, RetentionWindow

from .database import EventDatabase
from .models import DurableEventRow, DurableOutboxRow


def _state(raw: str | None) -> OutboxState | None:
    """The outbox state, or `None` when it cannot be established.

    `None` covers both "no `evt_outbox` row" and "a value this build does not recognize". Both
    are unreadable delivery state, and unreadable is **not** drained (**RT8**) — a future state
    added additively (AR-32) by a later milestone must not become deletable here merely because
    this build cannot name it.
    """
    if raw is None:
        return None
    try:
        return OutboxState(raw)
    except ValueError:
        return None


class SqlRetentionStore(RetentionStore):
    """Retention reads and the sanctioned deletion over `evt_event` / `evt_outbox`.

    Holds the same `EventDatabase` the log and spine hold, and commits through the caller's
    session in the shape those two already established. It is **not** a `DurableEventLog` and
    does not implement one: keeping the capability on its own port is what stops every holder
    of the log from acquiring a removal path (ADR-0041 RT11).
    """

    def __init__(self, database: EventDatabase) -> None:
        self._db = database

    def candidates(self, window: RetentionWindow) -> Sequence[RetentionCandidate]:
        """Events recorded inside `window`, each with its outbox state — **a query only**.

        A LEFT OUTER JOIN, not an inner one: an event with no `evt_outbox` row must still be
        *seen* and then retained on its merits (`outbox_state=None` ⇒ un-drained). An inner
        join would silently drop exactly those rows, turning "cannot prove it was delivered"
        into "never considered", which is the same blindness with better-looking output.

        Ordered by `(recorded_at, event_id)` so a sweep with a `limit` selects the same set on
        every call and across restarts (**ED4**).
        """
        rows = self._db.session.exec(
            select(
                DurableEventRow.event_id,
                DurableEventRow.recorded_at,
                DurableOutboxRow.state,
            )
            .join(
                DurableOutboxRow,
                DurableOutboxRow.event_id == DurableEventRow.event_id,
                isouter=True,
            )
            .where(DurableEventRow.recorded_at >= window.start)
            .where(DurableEventRow.recorded_at < window.end)
            .order_by(DurableEventRow.recorded_at, DurableEventRow.event_id)
        ).all()
        return tuple(
            RetentionCandidate(
                event_id=event_id,
                recorded_at=recorded_at,
                outbox_state=_state(raw_state),
            )
            for event_id, recorded_at, raw_state in rows
        )

    def delete(self, event_ids: Sequence[str]) -> int:
        """Remove exactly these `evt_event` rows in **one transaction**. Returns how many.

        All-or-nothing, on the `append_many` precedent (**ED6**): a failure removes none of
        them, so a caller never has to reason about a half-swept batch. An id with no row is
        skipped rather than raising — retention is idempotent, and a second sweep over an
        already-deleted event is ordinary traffic, not an incident.
        """
        session = self._db.session
        removed = 0
        try:
            for event_id in event_ids:
                row = session.get(DurableEventRow, event_id)
                if row is None:
                    continue
                session.delete(row)
                removed += 1
            session.commit()
        except Exception:
            session.rollback()
            raise
        return removed
