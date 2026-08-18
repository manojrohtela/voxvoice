"""Revenue Recognition repositories (M9.18) — additive to the M9.4 persistence layer.

Five SQL implementations of the M9.18 Foundation contracts, reusing the M9.4 machinery unchanged:
the same `_Repo` session handle, the same codec, the same projection-plus-payload mapping, the same
"never commit, never roll back" rule, and the same org-scoped-by-construction reads (§A11.2).

## The three race-critical writes

`SqlRevenueScheduleRepository.create` is an insert on an id derived from the **basis digest**,
defended the three ways every idempotent write since M9.11 has been: a derived id, a
read-before-write, and a `SAVEPOINT` whose `IntegrityError` becomes a read of the winner rather than
a destroyed transaction.

`SqlRevenueScheduleRepository.advance` is the compare-and-set on `version` — the stale-worker
defence. A worker that read a schedule before it was superseded cannot recognize against it.

`SqlRecognitionEntryRepository.recognize` is **the double-recognition defence**, and it is the most
important method in the milestone: `UPDATE ... WHERE id = ? AND state = 'pending'`. Two workers that
both see a matured period both try it; the database picks exactly one, and the loser raises
`ScheduleStale` *before* any ledger posting is attempted. An application-level `if
already_recognized` cannot do this, because between the check and the write there is a window, and
the window is a double-counted month of revenue.

## What no repository here does

None decides a state, allocates money, chooses a schedule, or computes a balance. And none writes
to an invoice, ledger, payment, collection or subscription table — an architecture test scans the
write targets of the whole Application to prove it.
"""

from __future__ import annotations

from sqlalchemy.exc import IntegrityError
from sqlmodel import select

from billing.foundation import (
    EntryState,
    RecognitionActivity,
    RecognitionActivityRepository,
    RecognitionAdjustment,
    RecognitionAdjustmentRepository,
    RecognitionEntry,
    RecognitionEntryRepository,
    RecognitionPolicy,
    RecognitionPolicyRepository,
    RevenueSchedule,
    RevenueScheduleRepository,
    ScheduleStale,
)

from . import revenue_models as rows
from .codec import encode
from .mapping import from_row, update_row
from .repositories import _Repo


def _policy_projection(p: RecognitionPolicy) -> dict:
    return {
        "id": p.id,
        "org_id": p.org_id,
        "code": p.code,
        "version": p.version,
        "method": p.method.value,
        "granularity": p.granularity.value,
        "remainder": p.remainder.value,
        "is_active": p.is_active,
        "declared_at": p.declared_at,
        "declared_by": p.declared_by,
    }


def _schedule_projection(s: RevenueSchedule) -> dict:
    basis = s.basis
    return {
        "id": s.id,
        "org_id": s.org_id,
        "account_id": s.account_id,
        "document_id": basis.document_id,
        "revision": basis.revision,
        "snapshot_digest": basis.snapshot_digest,
        "basis_digest": basis.digest,
        "state": s.state.value,
        "method": basis.method.value,
        "currency": basis.currency.value,
        "amount_minor": basis.amount.minor,
        "service_start": basis.service_period.start,
        "service_end": basis.service_period.end,
        "policy_id": basis.policy_id,
        "policy_code": basis.policy_code,
        "policy_version": basis.policy_version,
        "entries_planned": s.entries_planned,
        "entries_recognized": s.entries_recognized,
        "version": s.version,
        "superseded_by": s.superseded_by,
        "subscription_id": basis.subscription_id,
        "created_at": s.created_at,
        "closed_at": s.closed_at,
    }


def _entry_projection(e: RecognitionEntry) -> dict:
    return {
        "id": e.id,
        "org_id": e.org_id,
        "schedule_id": e.schedule_id,
        "account_id": e.account_id,
        "sequence": e.sequence,
        "period_start": e.period.start,
        "period_end": e.period.end,
        "amount_minor": e.amount.minor,
        "currency": e.amount.currency.value,
        "recognize_on": e.recognize_on,
        "state": e.state.value,
        "recognition": e.recognition.value,
        "ledger_entry_id": e.ledger_entry_id,
        "ledger_period_key": e.ledger_period_key,
        "ledger_disposition": e.ledger_disposition,
        "recognized_at": e.recognized_at,
        "posted_at": e.posted_at,
    }


def _adjustment_projection(a: RecognitionAdjustment) -> dict:
    return {
        "id": a.id,
        "org_id": a.org_id,
        "account_id": a.account_id,
        "schedule_id": a.schedule_id,
        "entry_id": a.entry_id,
        "kind": a.kind.value,
        "amount_minor": a.amount.minor,
        "currency": a.amount.currency.value,
        "document_id": a.document_id,
        "revision": a.revision,
        "policy_code": a.policy_code,
        "policy_version": a.policy_version,
        "effective_at": a.effective_at,
        "ledger_entry_id": a.ledger_entry_id,
        "created_at": a.created_at,
        "created_by": a.created_by,
        "request_key": a.request_key,
    }


def _activity_projection(a: RecognitionActivity) -> dict:
    return {
        "id": a.id,
        "org_id": a.org_id,
        "schedule_id": a.schedule_id,
        "at": a.at,
        "kind": a.kind,
        "actor_id": a.actor_id,
        "entry_id": a.entry_id,
    }


class SqlRecognitionPolicyRepository(_Repo, RecognitionPolicyRepository):
    """Immutable strategy versions. There is no update method, and that is the contract."""

    def add(self, policy: RecognitionPolicy) -> tuple[RecognitionPolicy, bool]:
        existing = self._session.get(rows.BillingRecognitionPolicyRow, policy.id)
        if existing is not None:
            return from_row(RecognitionPolicy, existing), False
        try:
            with self._session.begin_nested():
                self._session.add(
                    rows.BillingRecognitionPolicyRow(
                        payload=encode(policy), **_policy_projection(policy)
                    )
                )
                self._session.flush()
        except IntegrityError:
            winner = self._session.get(rows.BillingRecognitionPolicyRow, policy.id)
            if winner is None:  # pragma: no cover — the primary key guarantees a row
                raise
            return from_row(RecognitionPolicy, winner), False
        return policy, True

    def get(self, org_id: str, policy_id: str) -> RecognitionPolicy | None:
        row = self._one(
            select(rows.BillingRecognitionPolicyRow).where(
                rows.BillingRecognitionPolicyRow.org_id == org_id,
                rows.BillingRecognitionPolicyRow.id == policy_id,
            )
        )
        return from_row(RecognitionPolicy, row) if row else None

    def latest(self, org_id: str, code: str) -> RecognitionPolicy | None:
        row = self._one(
            select(rows.BillingRecognitionPolicyRow)
            .where(
                rows.BillingRecognitionPolicyRow.org_id == org_id,
                rows.BillingRecognitionPolicyRow.code == code,
                rows.BillingRecognitionPolicyRow.is_active == True,  # noqa: E712
            )
            .order_by(rows.BillingRecognitionPolicyRow.version.desc())
        )
        return from_row(RecognitionPolicy, row) if row else None

    def versions(self, org_id: str, code: str) -> tuple[RecognitionPolicy, ...]:
        found = self._all(
            select(rows.BillingRecognitionPolicyRow)
            .where(
                rows.BillingRecognitionPolicyRow.org_id == org_id,
                rows.BillingRecognitionPolicyRow.code == code,
            )
            .order_by(rows.BillingRecognitionPolicyRow.version)
        )
        return tuple(from_row(RecognitionPolicy, r) for r in found)

    def all(self, org_id: str, include_inactive: bool = False) -> tuple[RecognitionPolicy, ...]:
        statement = select(rows.BillingRecognitionPolicyRow).where(
            rows.BillingRecognitionPolicyRow.org_id == org_id
        )
        if not include_inactive:
            statement = statement.where(
                rows.BillingRecognitionPolicyRow.is_active == True  # noqa: E712
            )
        found = self._all(
            statement.order_by(
                rows.BillingRecognitionPolicyRow.code,
                rows.BillingRecognitionPolicyRow.version,
            )
        )
        return tuple(from_row(RecognitionPolicy, r) for r in found)

    def deactivate(self, policy: RecognitionPolicy) -> RecognitionPolicy:
        row = self._session.get(rows.BillingRecognitionPolicyRow, policy.id)
        if row is None:
            return policy
        update_row(row, policy, _policy_projection(policy))
        self._session.flush()
        return policy


class SqlRevenueScheduleRepository(_Repo, RevenueScheduleRepository):
    """**Two arbitrators.** `create` decides who schedules; `advance` decides who changes."""

    def create(self, schedule: RevenueSchedule) -> tuple[RevenueSchedule, bool]:
        existing = self._session.get(rows.BillingRevenueScheduleRow, schedule.id)
        if existing is not None:
            return from_row(RevenueSchedule, existing), False
        try:
            with self._session.begin_nested():
                self._session.add(
                    rows.BillingRevenueScheduleRow(
                        payload=encode(schedule), **_schedule_projection(schedule)
                    )
                )
                self._session.flush()
        except IntegrityError:
            # Another worker derived the same basis between the read and the insert. The basis
            # digest guarantees they computed the same schedule, so this caller reads the winner's.
            winner = self._session.get(rows.BillingRevenueScheduleRow, schedule.id)
            if winner is None:  # pragma: no cover — the primary key guarantees a row
                raise
            return from_row(RevenueSchedule, winner), False
        return schedule, True

    def advance(self, schedule: RevenueSchedule, *, expected_version: int) -> RevenueSchedule:
        """Compare-and-set on `version`. **The stale-worker defence.**

        A filtered UPDATE rather than a read followed by a write, because the read would be exactly
        the stale read the check exists to catch. Zero rows is not retryable: the caller must
        re-read and re-decide, since whatever it was about to recognize was decided against a basis
        that may since have been superseded."""
        result = self._session.execute(
            rows.BillingRevenueScheduleRow.__table__.update()
            .where(
                rows.BillingRevenueScheduleRow.__table__.c.id == schedule.id,
                rows.BillingRevenueScheduleRow.__table__.c.org_id == schedule.org_id,
                rows.BillingRevenueScheduleRow.__table__.c.version == expected_version,
            )
            .values(payload=encode(schedule), **_schedule_projection(schedule))
        )
        if result.rowcount != 1:
            stored = self._session.get(rows.BillingRevenueScheduleRow, schedule.id)
            current = stored.version if stored is not None else "missing"
            raise ScheduleStale(
                f"revenue schedule {schedule.id!r} moved while this worker was deciding "
                f"(expected version {expected_version}, found {current}); re-read and re-decide"
            )
        self._session.expire_all()
        return schedule

    def get(self, org_id: str, schedule_id: str) -> RevenueSchedule | None:
        row = self._one(
            select(rows.BillingRevenueScheduleRow).where(
                rows.BillingRevenueScheduleRow.org_id == org_id,
                rows.BillingRevenueScheduleRow.id == schedule_id,
            )
        )
        return from_row(RevenueSchedule, row) if row else None

    def for_document(self, org_id: str, document_id: str) -> tuple[RevenueSchedule, ...]:
        found = self._all(
            select(rows.BillingRevenueScheduleRow)
            .where(
                rows.BillingRevenueScheduleRow.org_id == org_id,
                rows.BillingRevenueScheduleRow.document_id == document_id,
            )
            .order_by(
                rows.BillingRevenueScheduleRow.revision,
                rows.BillingRevenueScheduleRow.created_at,
            )
        )
        return tuple(from_row(RevenueSchedule, r) for r in found)

    def for_account(
        self, org_id: str, account_id: str, limit: int = 200
    ) -> tuple[RevenueSchedule, ...]:
        found = self._all(
            select(rows.BillingRevenueScheduleRow)
            .where(
                rows.BillingRevenueScheduleRow.org_id == org_id,
                rows.BillingRevenueScheduleRow.account_id == account_id,
            )
            .order_by(rows.BillingRevenueScheduleRow.created_at.desc())
            .limit(limit)
        )
        return tuple(from_row(RevenueSchedule, r) for r in found)

    def by_state(
        self, org_id: str, states: tuple[str, ...], limit: int = 200
    ) -> tuple[RevenueSchedule, ...]:
        statement = select(rows.BillingRevenueScheduleRow).where(
            rows.BillingRevenueScheduleRow.org_id == org_id
        )
        if states:
            statement = statement.where(rows.BillingRevenueScheduleRow.state.in_(states))
        found = self._all(
            statement.order_by(rows.BillingRevenueScheduleRow.created_at.desc()).limit(limit)
        )
        return tuple(from_row(RevenueSchedule, r) for r in found)

    def all(self, org_id: str, limit: int = 500) -> tuple[RevenueSchedule, ...]:
        found = self._all(
            select(rows.BillingRevenueScheduleRow)
            .where(rows.BillingRevenueScheduleRow.org_id == org_id)
            .order_by(rows.BillingRevenueScheduleRow.created_at.desc())
            .limit(limit)
        )
        return tuple(from_row(RevenueSchedule, r) for r in found)


class SqlRecognitionEntryRepository(_Repo, RecognitionEntryRepository):
    """Period slices. **`recognize` is the double-recognition defence.**"""

    def append(self, entry: RecognitionEntry) -> tuple[RecognitionEntry, bool]:
        existing = self._session.get(rows.BillingRecognitionEntryRow, entry.id)
        if existing is not None:
            return from_row(RecognitionEntry, existing), False
        try:
            with self._session.begin_nested():
                self._session.add(
                    rows.BillingRecognitionEntryRow(
                        payload=encode(entry), **_entry_projection(entry)
                    )
                )
                self._session.flush()
        except IntegrityError:
            winner = self._session.get(rows.BillingRecognitionEntryRow, entry.id)
            if winner is None:  # pragma: no cover — the primary key guarantees a row
                raise
            return from_row(RecognitionEntry, winner), False
        return entry, True

    def recognize(self, entry: RecognitionEntry, *, expected_state: str) -> RecognitionEntry:
        """Conditional `pending → recognized`. **One worker, one recognition, decided by the DB.**

        The loser learns before it posts anything, which is the whole point: two ledger entries for
        one month of revenue cannot be undone by noticing later."""
        result = self._session.execute(
            rows.BillingRecognitionEntryRow.__table__.update()
            .where(
                rows.BillingRecognitionEntryRow.__table__.c.id == entry.id,
                rows.BillingRecognitionEntryRow.__table__.c.org_id == entry.org_id,
                rows.BillingRecognitionEntryRow.__table__.c.state == expected_state,
            )
            .values(payload=encode(entry), **_entry_projection(entry))
        )
        if result.rowcount != 1:
            stored = self._session.get(rows.BillingRecognitionEntryRow, entry.id)
            current = stored.state if stored is not None else "missing"
            raise ScheduleStale(
                f"recognition entry {entry.id!r} is no longer {expected_state!r} "
                f"(found {current!r}); another worker recognized this period"
            )
        self._session.expire_all()
        return entry

    def settle(self, entry: RecognitionEntry) -> RecognitionEntry:
        row = self._session.get(rows.BillingRecognitionEntryRow, entry.id)
        if row is None:
            return self.append(entry)[0]
        update_row(row, entry, _entry_projection(entry))
        self._session.flush()
        return entry

    def get(self, org_id: str, entry_id: str) -> RecognitionEntry | None:
        row = self._one(
            select(rows.BillingRecognitionEntryRow).where(
                rows.BillingRecognitionEntryRow.org_id == org_id,
                rows.BillingRecognitionEntryRow.id == entry_id,
            )
        )
        return from_row(RecognitionEntry, row) if row else None

    def for_schedule(self, org_id: str, schedule_id: str) -> tuple[RecognitionEntry, ...]:
        found = self._all(
            select(rows.BillingRecognitionEntryRow)
            .where(
                rows.BillingRecognitionEntryRow.org_id == org_id,
                rows.BillingRecognitionEntryRow.schedule_id == schedule_id,
            )
            .order_by(rows.BillingRecognitionEntryRow.sequence)
        )
        return tuple(from_row(RecognitionEntry, r) for r in found)

    def due(self, org_id: str, at: float, limit: int = 200) -> tuple[RecognitionEntry, ...]:
        """Slices that have matured. Hits `ix_cp_billing_recognition_entry_due`.

        A query, not a scheduler. Whoever calls it on a timer lives outside this package."""
        found = self._all(
            select(rows.BillingRecognitionEntryRow)
            .where(
                rows.BillingRecognitionEntryRow.org_id == org_id,
                rows.BillingRecognitionEntryRow.state == EntryState.PENDING.value,
                rows.BillingRecognitionEntryRow.recognize_on <= at,
            )
            .order_by(rows.BillingRecognitionEntryRow.recognize_on)
            .limit(limit)
        )
        return tuple(from_row(RecognitionEntry, r) for r in found)

    def unposted(self, org_id: str, limit: int = 200) -> tuple[RecognitionEntry, ...]:
        found = self._all(
            select(rows.BillingRecognitionEntryRow)
            .where(
                rows.BillingRecognitionEntryRow.org_id == org_id,
                rows.BillingRecognitionEntryRow.state == EntryState.UNPOSTED.value,
            )
            .order_by(rows.BillingRecognitionEntryRow.recognized_at)
            .limit(limit)
        )
        return tuple(from_row(RecognitionEntry, r) for r in found)


class SqlRecognitionAdjustmentRepository(_Repo, RecognitionAdjustmentRepository):
    """Corrections. Append-only; no update, no delete."""

    def append(self, adjustment: RecognitionAdjustment) -> tuple[RecognitionAdjustment, bool]:
        existing = self._session.get(rows.BillingRecognitionAdjustmentRow, adjustment.id)
        if existing is not None:
            return from_row(RecognitionAdjustment, existing), False
        try:
            with self._session.begin_nested():
                self._session.add(
                    rows.BillingRecognitionAdjustmentRow(
                        payload=encode(adjustment), **_adjustment_projection(adjustment)
                    )
                )
                self._session.flush()
        except IntegrityError:
            winner = self._session.get(rows.BillingRecognitionAdjustmentRow, adjustment.id)
            if winner is None:  # pragma: no cover — the primary key guarantees a row
                raise
            return from_row(RecognitionAdjustment, winner), False
        return adjustment, True

    def get(self, org_id: str, adjustment_id: str) -> RecognitionAdjustment | None:
        row = self._one(
            select(rows.BillingRecognitionAdjustmentRow).where(
                rows.BillingRecognitionAdjustmentRow.org_id == org_id,
                rows.BillingRecognitionAdjustmentRow.id == adjustment_id,
            )
        )
        return from_row(RecognitionAdjustment, row) if row else None

    def for_entry(self, org_id: str, entry_id: str) -> tuple[RecognitionAdjustment, ...]:
        found = self._all(
            select(rows.BillingRecognitionAdjustmentRow)
            .where(
                rows.BillingRecognitionAdjustmentRow.org_id == org_id,
                rows.BillingRecognitionAdjustmentRow.entry_id == entry_id,
            )
            .order_by(rows.BillingRecognitionAdjustmentRow.created_at)
        )
        return tuple(from_row(RecognitionAdjustment, r) for r in found)

    def for_schedule(self, org_id: str, schedule_id: str) -> tuple[RecognitionAdjustment, ...]:
        found = self._all(
            select(rows.BillingRecognitionAdjustmentRow)
            .where(
                rows.BillingRecognitionAdjustmentRow.org_id == org_id,
                rows.BillingRecognitionAdjustmentRow.schedule_id == schedule_id,
            )
            .order_by(rows.BillingRecognitionAdjustmentRow.created_at)
        )
        return tuple(from_row(RecognitionAdjustment, r) for r in found)


class SqlRecognitionActivityRepository(_Repo, RecognitionActivityRepository):
    """Append-only audit trail."""

    def append(self, activity: RecognitionActivity) -> RecognitionActivity:
        self._add(
            rows.BillingRecognitionActivityRow(
                payload=encode(activity), **_activity_projection(activity)
            )
        )
        return activity

    def for_schedule(
        self, org_id: str, schedule_id: str, limit: int = 200
    ) -> tuple[RecognitionActivity, ...]:
        found = self._all(
            select(rows.BillingRecognitionActivityRow)
            .where(
                rows.BillingRecognitionActivityRow.org_id == org_id,
                rows.BillingRecognitionActivityRow.schedule_id == schedule_id,
            )
            .order_by(rows.BillingRecognitionActivityRow.at)
            .limit(limit)
        )
        return tuple(from_row(RecognitionActivity, r) for r in found)


__all__ = [
    "SqlRecognitionActivityRepository",
    "SqlRecognitionAdjustmentRepository",
    "SqlRecognitionEntryRepository",
    "SqlRecognitionPolicyRepository",
    "SqlRevenueScheduleRepository",
]
