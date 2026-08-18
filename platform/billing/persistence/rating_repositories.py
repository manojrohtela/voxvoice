"""Rating Engine repositories (M9.12) — additive to the M9.4 persistence layer.

Five SQL implementations of the M9.12 Foundation contracts, reusing the M9.4 machinery unchanged:
the same `_Repo` session handle, the same codec, the same projection-plus-payload mapping, the same
"never commit, never roll back" rule, and the same org-scoped-by-construction reads (§A11.2).

Three are append-only and expose no update or delete method at all: rated charges, rating
snapshots, and rating activity.

## The concurrency-safe write

`SqlRatingRunRepository.append` is where idempotent rerating stops being a hope. Three defences,
in order — the same three M9.11 used for commercial usage, for the same reason:

1. **a derived primary key** — two processes rating the same period from the same inputs compute
   the *same* id, so they collide rather than both succeeding;
2. **a read-before-write** — the common case (a nightly job over an unchanged period) returns the
   stored run without attempting an insert at all;
3. **a `SAVEPOINT` around the insert plus an `IntegrityError` catch** — the genuine race, where a
   scheduled run and a manual rerate both pass step 2. One wins; the other's failure is caught, the
   savepoint is rolled back so the *outer* transaction survives, and the winner's row is read back
   and returned.

Step 3 is the one that matters and the one usually missing. Without the savepoint, a caught
`IntegrityError` leaves the session in a failed state and the whole surrounding transaction —
including the activity entry and the staged event — dies with it.

No repository here calculates a charge, folds a tier, compares two runs, or decides whether a
rerate is needed. Those are Foundation decisions, and a repository that made one would be putting
revenue logic where nobody would look for it.
"""

from __future__ import annotations

from sqlalchemy.exc import IntegrityError
from sqlmodel import select

from billing.foundation import (
    MinimumCommitment,
    MinimumCommitmentRepository,
    RatedCharge,
    RatedChargeRepository,
    RatingActivity,
    RatingActivityRepository,
    RatingRun,
    RatingRunRepository,
    RatingSnapshot,
    RatingSnapshotRepository,
    RatingState,
)

from . import rating_models as rows
from .codec import encode
from .mapping import from_row, update_row
from .repositories import _Repo

#: Runs that are no longer the figure in force for their period. Read here rather than recomputed:
#: `SETTLED_RATING_STATES` includes FINALIZED, which *is* in force, so the two sets differ.
_RETIRED_RUN_STATES = (RatingState.SUPERSEDED.value, RatingState.DISCARDED.value)


def _run_projection(run: RatingRun) -> dict:
    return {
        "id": run.id,
        "org_id": run.org_id,
        "account_id": run.account_id,
        "subscription_id": run.subscription_id,
        "period_start": run.period.start,
        "period_end": run.period.end,
        "currency": run.currency.value,
        "state": run.state.value,
        "sequence": run.sequence,
        "basis_digest": run.basis_digest,
        "result_digest": run.result_digest,
        "total_minor": run.total.minor,
        "charge_count": run.charge_count,
        "reason": run.reason.value if run.reason else "",
        "supersedes": run.supersedes,
        "adjusts": run.adjusts,
        "superseded_by": run.superseded_by,
        "rated_at": run.rated_at,
        "rated_by": run.rated_by,
        "finalized_at": run.finalized_at,
        "finalized_by": run.finalized_by,
        "updated_at": run.updated_at,
    }


def _charge_projection(charge: RatedCharge, rating_run_id: str) -> dict:
    return {
        "id": charge.id,
        "org_id": charge.org_id,
        "account_id": charge.account_id,
        "rating_run_id": rating_run_id,
        "subscription_id": charge.provenance.subscription_id,
        "kind": charge.kind.value,
        "description": charge.description,
        "currency": charge.currency.value,
        "quantity": charge.quantity,
        "unit": charge.unit,
        "list_minor": charge.list_amount.minor if charge.list_amount else 0,
        "gross_minor": charge.gross_amount.minor if charge.gross_amount else 0,
        "discount_minor": charge.discount_amount.minor if charge.discount_amount else 0,
        "net_minor": charge.amount.minor,
        "price_code": charge.provenance.price_code,
        "price_version": charge.provenance.price_version,
        "price_book_code": charge.provenance.price_book_code,
        "meter_code": charge.provenance.meter_code,
        "item_id": charge.provenance.item_id,
        "usage_digest": charge.provenance.usage_digest,
        "component_count": len(charge.components),
        "discount_count": len(charge.discounts),
        "digest": charge.digest,
    }


def _snapshot_projection(snapshot: RatingSnapshot) -> dict:
    return {
        "id": snapshot.id,
        "org_id": snapshot.org_id,
        "account_id": snapshot.account_id,
        "subscription_id": snapshot.subscription_id,
        "rating_run_id": snapshot.rating_run_id,
        "period_start": snapshot.period.start,
        "period_end": snapshot.period.end,
        "sequence": snapshot.sequence,
        "result_digest": snapshot.result_digest,
        "basis_digest": snapshot.basis_digest,
        "total_minor": snapshot.total.minor if snapshot.total else 0,
        "charge_count": snapshot.charge_count,
        "taken_at": snapshot.taken_at,
        "taken_by": snapshot.taken_by,
        "reason": snapshot.reason,
    }


def _commitment_projection(commitment: MinimumCommitment) -> dict:
    return {
        "id": commitment.id,
        "org_id": commitment.org_id,
        "account_id": commitment.account_id,
        "code": commitment.code,
        "amount_minor": commitment.amount.minor,
        "currency": commitment.amount.currency.value,
        "subscription_id": commitment.subscription_id,
        "interval": commitment.interval.value,
        "contract_id": commitment.contract_id,
        "active": commitment.active,
        "effective_from": commitment.effective_from,
        "effective_until": commitment.effective_until,
        "created_at": commitment.created_at,
        "retired_at": commitment.retired_at,
    }


def _activity_projection(activity: RatingActivity) -> dict:
    return {
        "id": activity.id,
        "org_id": activity.org_id,
        "action": activity.action.value,
        "actor_id": activity.actor_id,
        "at": activity.at,
        "account_id": activity.account_id,
        "subscription_id": activity.subscription_id,
        "rating_run_id": activity.rating_run_id,
        "period_start": activity.period_start,
        "period_end": activity.period_end,
        "total_minor": activity.total_minor,
        "currency": activity.currency.value if activity.currency else "",
        "reason": activity.reason,
    }


class SqlRatingRunRepository(_Repo, RatingRunRepository):
    """Rating runs. **Idempotent on the derived id, safe under concurrent rating.**

    See the module docstring for why the savepoint in `append` is the part that matters."""

    def append(self, run: RatingRun) -> RatingRun:
        existing = self._stored(run.org_id, run.id)
        if existing is not None:
            # The common case: a nightly job re-rating an unchanged period, or a retry. The first
            # write wins and the second call returns it unchanged, so a retry can never produce a
            # second commercial figure for one month.
            return existing
        try:
            # A nested transaction (SAVEPOINT) so that losing the race does not poison the
            # caller's transaction. Without it, the loser's activity entry and staged event would
            # be rolled back along with the failed insert.
            with self._session.begin_nested():
                self._session.add(
                    rows.BillingRatingRunRow(payload=encode(run), **_run_projection(run))
                )
                self._session.flush()
        except IntegrityError:
            # Another instance rated the same period from the same inputs between our read and
            # our write. That is not an error: it is idempotent rerating working under
            # concurrency. Return the winner's run so both callers see the same figure.
            stored = self._stored(run.org_id, run.id)
            if stored is None:  # pragma: no cover — the constraint that fired guarantees a row
                raise
            return stored
        return run

    def get(self, org_id: str, run_id: str) -> RatingRun | None:
        return self._stored(org_id, run_id)

    def latest_for_period(
        self, org_id: str, subscription_id: str, period_start: float, period_end: float
    ) -> RatingRun | None:
        """The figure in force: the newest run that has not been superseded or discarded.

        Ordered by sequence and then id, never by insertion order — two runs written in the same
        transaction must still resolve the same way on every read."""
        found = self._all(
            select(rows.BillingRatingRunRow)
            .where(
                rows.BillingRatingRunRow.org_id == org_id,
                rows.BillingRatingRunRow.subscription_id == subscription_id,
                rows.BillingRatingRunRow.period_start == period_start,
                rows.BillingRatingRunRow.period_end == period_end,
                rows.BillingRatingRunRow.state.notin_(_RETIRED_RUN_STATES),  # type: ignore[attr-defined]
            )
            .order_by(
                rows.BillingRatingRunRow.sequence.desc(),  # type: ignore[attr-defined]
                rows.BillingRatingRunRow.id,
            )
        )
        return from_row(RatingRun, found[0]) if found else None

    def for_period(
        self, org_id: str, subscription_id: str, period_start: float, period_end: float
    ) -> tuple[RatingRun, ...]:
        found = self._all(
            select(rows.BillingRatingRunRow)
            .where(
                rows.BillingRatingRunRow.org_id == org_id,
                rows.BillingRatingRunRow.subscription_id == subscription_id,
                rows.BillingRatingRunRow.period_start == period_start,
                rows.BillingRatingRunRow.period_end == period_end,
            )
            .order_by(rows.BillingRatingRunRow.sequence, rows.BillingRatingRunRow.id)
        )
        return tuple(from_row(RatingRun, r) for r in found)

    def for_account(self, org_id: str, account_id: str, limit: int = 200) -> tuple[RatingRun, ...]:
        found = self._all(
            select(rows.BillingRatingRunRow)
            .where(
                rows.BillingRatingRunRow.org_id == org_id,
                rows.BillingRatingRunRow.account_id == account_id,
            )
            .order_by(
                rows.BillingRatingRunRow.rated_at.desc(),  # type: ignore[attr-defined]
                rows.BillingRatingRunRow.id,
            )
            .limit(limit)
        )
        return tuple(from_row(RatingRun, r) for r in found)

    def all(self, org_id: str, limit: int = 500) -> tuple[RatingRun, ...]:
        found = self._all(
            select(rows.BillingRatingRunRow)
            .where(rows.BillingRatingRunRow.org_id == org_id)
            .order_by(
                rows.BillingRatingRunRow.rated_at.desc(),  # type: ignore[attr-defined]
                rows.BillingRatingRunRow.id,
            )
            .limit(limit)
        )
        return tuple(from_row(RatingRun, r) for r in found)

    def replace(self, run: RatingRun) -> RatingRun:
        existing = self._row(run.org_id, run.id)
        if existing is None:
            return self.append(run)
        update_row(existing, run, _run_projection(run))
        self._session.flush()
        return run

    def next_sequence(
        self, org_id: str, subscription_id: str, period_start: float, period_end: float
    ) -> int:
        return len(self.for_period(org_id, subscription_id, period_start, period_end)) + 1

    # ── internals ───────────────────────────────────────────────────────────────

    def _row(self, org_id: str, run_id: str):
        return self._one(
            select(rows.BillingRatingRunRow).where(
                rows.BillingRatingRunRow.org_id == org_id,
                rows.BillingRatingRunRow.id == run_id,
            )
        )

    def _stored(self, org_id: str, run_id: str) -> RatingRun | None:
        row = self._row(org_id, run_id)
        return from_row(RatingRun, row) if row else None


class SqlRatedChargeRepository(_Repo, RatedChargeRepository):
    """**Append-only.** There is no update method and no delete method — none exists to call."""

    def append_all(self, charges, *, rating_run_id: str) -> tuple[RatedCharge, ...]:
        """Idempotent: charge ids are derived from the run id, so re-appending a run's charges
        after a retry writes nothing new rather than duplicating every line."""
        stored: list[RatedCharge] = []
        for charge in charges:
            existing = self._row(charge.org_id, charge.id)
            if existing is not None:
                stored.append(from_row(RatedCharge, existing))
                continue
            try:
                with self._session.begin_nested():
                    self._session.add(
                        rows.BillingRatedChargeRow(
                            payload=encode(charge),
                            **_charge_projection(charge, rating_run_id),
                        )
                    )
                    self._session.flush()
            except IntegrityError:
                winner = self._row(charge.org_id, charge.id)
                if winner is None:  # pragma: no cover — the constraint guarantees a row
                    raise
                stored.append(from_row(RatedCharge, winner))
                continue
            stored.append(charge)
        return tuple(stored)

    def for_run(self, org_id: str, rating_run_id: str) -> tuple[RatedCharge, ...]:
        found = self._all(
            select(rows.BillingRatedChargeRow)
            .where(
                rows.BillingRatedChargeRow.org_id == org_id,
                rows.BillingRatedChargeRow.rating_run_id == rating_run_id,
            )
            .order_by(rows.BillingRatedChargeRow.item_id, rows.BillingRatedChargeRow.id)
        )
        return tuple(from_row(RatedCharge, r) for r in found)

    def get(self, org_id: str, charge_id: str) -> RatedCharge | None:
        row = self._row(org_id, charge_id)
        return from_row(RatedCharge, row) if row else None

    def for_account(
        self, org_id: str, account_id: str, limit: int = 500
    ) -> tuple[RatedCharge, ...]:
        found = self._all(
            select(rows.BillingRatedChargeRow)
            .where(
                rows.BillingRatedChargeRow.org_id == org_id,
                rows.BillingRatedChargeRow.account_id == account_id,
            )
            .order_by(rows.BillingRatedChargeRow.id)
            .limit(limit)
        )
        return tuple(from_row(RatedCharge, r) for r in found)

    def _row(self, org_id: str, charge_id: str):
        return self._one(
            select(rows.BillingRatedChargeRow).where(
                rows.BillingRatedChargeRow.org_id == org_id,
                rows.BillingRatedChargeRow.id == charge_id,
            )
        )


class SqlRatingSnapshotRepository(_Repo, RatingSnapshotRepository):
    """Append-only rating snapshots. No delete: a snapshot is what an invoice was raised from."""

    def append(self, snapshot: RatingSnapshot) -> RatingSnapshot:
        self._add(
            rows.BillingRatingSnapshotRow(
                payload=encode(snapshot), **_snapshot_projection(snapshot)
            )
        )
        return snapshot

    def get(self, org_id: str, snapshot_id: str) -> RatingSnapshot | None:
        row = self._one(
            select(rows.BillingRatingSnapshotRow).where(
                rows.BillingRatingSnapshotRow.org_id == org_id,
                rows.BillingRatingSnapshotRow.id == snapshot_id,
            )
        )
        return from_row(RatingSnapshot, row) if row else None

    def for_run(self, org_id: str, rating_run_id: str) -> tuple[RatingSnapshot, ...]:
        found = self._all(
            select(rows.BillingRatingSnapshotRow)
            .where(
                rows.BillingRatingSnapshotRow.org_id == org_id,
                rows.BillingRatingSnapshotRow.rating_run_id == rating_run_id,
            )
            .order_by(rows.BillingRatingSnapshotRow.sequence, rows.BillingRatingSnapshotRow.id)
        )
        return tuple(from_row(RatingSnapshot, r) for r in found)

    def for_subscription(
        self, org_id: str, subscription_id: str, limit: int = 100
    ) -> tuple[RatingSnapshot, ...]:
        found = self._all(
            select(rows.BillingRatingSnapshotRow)
            .where(
                rows.BillingRatingSnapshotRow.org_id == org_id,
                rows.BillingRatingSnapshotRow.subscription_id == subscription_id,
            )
            .order_by(
                rows.BillingRatingSnapshotRow.taken_at.desc(),  # type: ignore[attr-defined]
                rows.BillingRatingSnapshotRow.id,
            )
            .limit(limit)
        )
        return tuple(from_row(RatingSnapshot, r) for r in found)


class SqlMinimumCommitmentRepository(_Repo, MinimumCommitmentRepository):
    """Spend floors. Retirement is a state, not a deletion."""

    def add(self, commitment: MinimumCommitment) -> MinimumCommitment:
        self._add(
            rows.BillingMinimumCommitmentRow(
                payload=encode(commitment), **_commitment_projection(commitment)
            )
        )
        return commitment

    def get(self, org_id: str, commitment_id: str) -> MinimumCommitment | None:
        row = self._one(
            select(rows.BillingMinimumCommitmentRow).where(
                rows.BillingMinimumCommitmentRow.org_id == org_id,
                rows.BillingMinimumCommitmentRow.id == commitment_id,
            )
        )
        return from_row(MinimumCommitment, row) if row else None

    def by_code(self, org_id: str, code: str) -> MinimumCommitment | None:
        row = self._one(
            select(rows.BillingMinimumCommitmentRow).where(
                rows.BillingMinimumCommitmentRow.org_id == org_id,
                rows.BillingMinimumCommitmentRow.code == code,
            )
        )
        return from_row(MinimumCommitment, row) if row else None

    def for_account(self, org_id: str, account_id: str) -> tuple[MinimumCommitment, ...]:
        found = self._all(
            select(rows.BillingMinimumCommitmentRow)
            .where(
                rows.BillingMinimumCommitmentRow.org_id == org_id,
                rows.BillingMinimumCommitmentRow.account_id == account_id,
            )
            .order_by(rows.BillingMinimumCommitmentRow.code)
        )
        return tuple(from_row(MinimumCommitment, r) for r in found)

    def all(self, org_id: str) -> tuple[MinimumCommitment, ...]:
        found = self._all(
            select(rows.BillingMinimumCommitmentRow)
            .where(rows.BillingMinimumCommitmentRow.org_id == org_id)
            .order_by(rows.BillingMinimumCommitmentRow.code)
        )
        return tuple(from_row(MinimumCommitment, r) for r in found)

    def replace(self, commitment: MinimumCommitment) -> MinimumCommitment:
        existing = self._one(
            select(rows.BillingMinimumCommitmentRow).where(
                rows.BillingMinimumCommitmentRow.org_id == commitment.org_id,
                rows.BillingMinimumCommitmentRow.id == commitment.id,
            )
        )
        if existing is None:
            return self.add(commitment)
        update_row(existing, commitment, _commitment_projection(commitment))
        self._session.flush()
        return commitment


class SqlRatingActivityRepository(_Repo, RatingActivityRepository):
    """Append-only rating history."""

    def append(self, activity: RatingActivity) -> RatingActivity:
        self._add(
            rows.BillingRatingActivityRow(
                payload=encode(activity), **_activity_projection(activity)
            )
        )
        return activity

    def history(
        self, org_id: str, subscription_id: str = "", limit: int = 100
    ) -> tuple[RatingActivity, ...]:
        statement = select(rows.BillingRatingActivityRow).where(
            rows.BillingRatingActivityRow.org_id == org_id
        )
        if subscription_id:
            statement = statement.where(
                rows.BillingRatingActivityRow.subscription_id == subscription_id
            )
        found = self._all(
            statement.order_by(
                rows.BillingRatingActivityRow.at.desc(),  # type: ignore[attr-defined]
                rows.BillingRatingActivityRow.id.desc(),  # type: ignore[attr-defined]
            ).limit(limit)
        )
        return tuple(from_row(RatingActivity, r) for r in found)

    def for_run(self, org_id: str, rating_run_id: str) -> tuple[RatingActivity, ...]:
        found = self._all(
            select(rows.BillingRatingActivityRow)
            .where(
                rows.BillingRatingActivityRow.org_id == org_id,
                rows.BillingRatingActivityRow.rating_run_id == rating_run_id,
            )
            .order_by(rows.BillingRatingActivityRow.at, rows.BillingRatingActivityRow.id)
        )
        return tuple(from_row(RatingActivity, r) for r in found)
