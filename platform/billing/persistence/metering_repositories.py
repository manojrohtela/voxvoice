"""Metering Engine repositories (M9.11) — additive to the M9.4 persistence layer.

Seven SQL implementations of the M9.11 Foundation contracts, reusing the M9.4 machinery
unchanged: the same `_Repo` session handle, the same codec, the same projection-plus-payload
mapping, the same "never commit, never roll back" rule, and the same org-scoped-by-construction
reads (§A11.2).

Three are append-only and expose no update or delete method at all: commercial usage, usage
corrections, and metering activity.

## The concurrency-safe write

`SqlCommercialUsageRepository.append` is where exactly-once stops being a hope. Three defences,
in order:

1. **a derived primary key** — two processes handling the same source event compute the *same*
   id, so they collide rather than both succeeding;
2. **a read-before-write** — the common case (a redelivery) returns the stored record without
   attempting an insert at all;
3. **a `SAVEPOINT` around the insert plus an `IntegrityError` catch** — the genuine race, where
   two processes both pass step 2 and both attempt the insert. One wins; the other's failure is
   caught, the savepoint is rolled back so the *outer* transaction survives, and the winner's row
   is read back and returned.

Step 3 is the one that matters and the one usually missing. Without the savepoint, a caught
`IntegrityError` leaves the session in a failed state and the whole surrounding transaction —
including the activity entry and the staged event — dies with it. With it, the loser's
transaction commits normally having done exactly what the winner did: nothing new.

No repository here normalizes a quantity, decides a disposition, folds a window, or computes a
seal digest. Those are Foundation decisions, and a repository that made one would be putting
revenue logic where nobody would look for it.
"""

from __future__ import annotations

from sqlalchemy.exc import IntegrityError
from sqlmodel import select

from billing.foundation import (
    CommercialMeter,
    CommercialMeterRepository,
    CommercialUsage,
    CommercialUsageRepository,
    MeteringActivity,
    MeteringActivityRepository,
    QuarantineEntry,
    QuarantineEntryRepository,
    UsageAggregateSnapshot,
    UsageAggregateSnapshotRepository,
    UsageCorrection,
    UsageCorrectionRepository,
    UsagePeriod,
    UsagePeriodRepository,
)

from . import metering_models as rows
from .codec import encode
from .mapping import from_row, update_row
from .repositories import _Repo


def _meter_projection(m: CommercialMeter) -> dict:
    return {
        "id": m.id,
        "org_id": m.org_id,
        "code": m.code,
        "display_name": m.display_name,
        "unit": m.unit,
        "aggregation": m.aggregation.value,
        "source": m.source.value,
        "state": m.state.value,
        "version": m.version,
        "late_arrival_window_seconds": m.late_arrival_window_seconds,
        "normalization_numerator": m.normalization.numerator,
        "normalization_denominator": m.normalization.denominator,
        "normalization_rounding": m.normalization.rounding.value,
        "created_at": m.created_at,
        "updated_at": m.updated_at,
        "activated_at": m.activated_at,
        "retired_at": m.retired_at,
    }


def _usage_projection(u: CommercialUsage) -> dict:
    return {
        "id": u.id,
        "org_id": u.org_id,
        "account_id": u.account_id,
        "source_event_id": u.source.event_id,
        "meter_code": u.meter_code,
        "meter_version": u.meter_version,
        "units": u.quantity.units,
        "unit": u.quantity.unit,
        "scope_id": u.scope.narrowest_id,
        "period_start": u.period.start,
        "period_end": u.period.end,
        "observed_at": u.source.observed_at,
        "recorded_at": u.recorded_at,
        "status": u.status.value,
        "disposition": u.provenance.disposition.value,
        "subscription_id": u.subscription_id,
        "corrects_usage_id": u.corrects_usage_id,
        "digest": u.digest,
    }


def _period_projection(p: UsagePeriod) -> dict:
    return {
        "id": p.id,
        "org_id": p.org_id,
        "account_id": p.account_id,
        "period_start": p.period.start,
        "period_end": p.period.end,
        "state": p.state.value,
        "record_count": p.record_count,
        "adjustment_count": p.adjustment_count,
        "quarantined_count": p.quarantined_count,
        "seal_digest": p.seal_digest,
        "opened_at": p.opened_at,
        "closing_at": p.closing_at,
        "sealed_at": p.sealed_at,
        "sealed_by": p.sealed_by,
        "updated_at": p.updated_at,
    }


def _aggregate_projection(a: UsageAggregateSnapshot) -> dict:
    return {
        "id": a.id,
        "org_id": a.org_id,
        "account_id": a.account_id,
        "meter_code": a.meter_code,
        "scope_id": a.scope.narrowest_id,
        "granularity": a.window.granularity.value,
        "window_start": a.window.start,
        "window_end": a.window.end,
        "period_start": a.period.start,
        "period_end": a.period.end,
        "aggregation": a.aggregation.value,
        "units": a.quantity.units,
        "unit": a.quantity.unit,
        "record_count": a.record_count,
        "adjustment_count": a.adjustment_count,
        "source_digest": a.source_digest,
        "sealed": a.sealed,
        "computed_at": a.computed_at,
    }


def _quarantine_projection(q: QuarantineEntry) -> dict:
    return {
        "id": q.id,
        "org_id": q.org_id,
        "source_event_id": q.source.event_id,
        "event_type": q.source.event_type,
        "meter_code": q.meter_code,
        "reason": q.reason.value,
        "state": q.state.value,
        "account_id": q.account_id,
        "scope_id": q.scope.narrowest_id if q.scope else "",
        "raw_units": q.raw_units,
        "raw_unit": q.raw_unit,
        "observed_at": q.source.observed_at,
        "received_at": q.received_at,
        "resolved_at": q.resolved_at,
        "resolved_by": q.resolved_by,
        "resulting_usage_id": q.resulting_usage_id,
    }


def _correction_projection(c: UsageCorrection) -> dict:
    return {
        "id": c.id,
        "org_id": c.org_id,
        "corrects_usage_id": c.corrects_usage_id,
        "kind": c.kind.value,
        "delta_units": c.delta_units,
        "unit": c.unit,
        "reason_code": c.reason_code,
        "period_start": c.period.start if c.period else None,
        "period_end": c.period.end if c.period else None,
        "created_at": c.created_at,
        "created_by": c.created_by,
        "resulting_usage_id": c.resulting_usage_id,
    }


def _activity_projection(a: MeteringActivity) -> dict:
    return {
        "id": a.id,
        "org_id": a.org_id,
        "action": a.action.value,
        "actor_id": a.actor_id,
        "at": a.at,
        "account_id": a.account_id,
        "meter_code": a.meter_code,
        "event_id": a.event_id,
        "usage_id": a.usage_id,
        "period_start": a.period_start,
        "period_end": a.period_end,
    }


class SqlCommercialMeterRepository(_Repo, CommercialMeterRepository):
    """The meter registry. Retirement is a state, not a deletion — a meter that named what an
    issued invoice billed must remain readable."""

    def add(self, meter: CommercialMeter) -> CommercialMeter:
        self._add(rows.BillingCommercialMeterRow(payload=encode(meter), **_meter_projection(meter)))
        return meter

    def get(self, org_id: str, meter_id: str) -> CommercialMeter | None:
        row = self._one(
            select(rows.BillingCommercialMeterRow).where(
                rows.BillingCommercialMeterRow.org_id == org_id,
                rows.BillingCommercialMeterRow.id == meter_id,
            )
        )
        return from_row(CommercialMeter, row) if row else None

    def by_code(self, org_id: str, code: str) -> CommercialMeter | None:
        row = self._one(
            select(rows.BillingCommercialMeterRow).where(
                rows.BillingCommercialMeterRow.org_id == org_id,
                rows.BillingCommercialMeterRow.code == code,
            )
        )
        return from_row(CommercialMeter, row) if row else None

    def all(self, org_id: str) -> tuple[CommercialMeter, ...]:
        found = self._all(
            select(rows.BillingCommercialMeterRow)
            .where(rows.BillingCommercialMeterRow.org_id == org_id)
            .order_by(rows.BillingCommercialMeterRow.code)
        )
        return tuple(from_row(CommercialMeter, r) for r in found)

    def replace(self, meter: CommercialMeter) -> CommercialMeter:
        existing = self._one(
            select(rows.BillingCommercialMeterRow).where(
                rows.BillingCommercialMeterRow.org_id == meter.org_id,
                rows.BillingCommercialMeterRow.id == meter.id,
            )
        )
        if existing is None:
            return self.add(meter)
        update_row(existing, meter, _meter_projection(meter))
        self._session.flush()
        return meter


class SqlCommercialUsageRepository(_Repo, CommercialUsageRepository):
    """**Append-only and exactly-once, safe under concurrent ingestion.**

    There is no update method and no delete method — none exists to call. See the module
    docstring for why the savepoint in `append` is the part that matters."""

    def append(self, usage: CommercialUsage) -> CommercialUsage:
        existing = self._stored(
            usage.org_id, usage.source.event_id, usage.meter_code, usage.corrects_usage_id
        )
        if existing is not None:
            # The common case: a redelivery. The first write wins and the replay returns it
            # unchanged, so a retry can never bill twice (ADR-0023 M4).
            return existing
        try:
            # A nested transaction (SAVEPOINT) so that losing the race does not poison the
            # caller's transaction. Without it, the loser's activity entry and staged event
            # would be rolled back along with the failed insert.
            with self._session.begin_nested():
                self._session.add(
                    rows.BillingCommercialUsageRow(
                        payload=encode(usage), **_usage_projection(usage)
                    )
                )
                self._session.flush()
        except IntegrityError:
            # Another instance inserted the same source event between our read and our write.
            # That is not an error: it is exactly-once working under concurrency. Return the
            # winner's record so both callers see the same billable fact.
            stored = self._stored(
                usage.org_id, usage.source.event_id, usage.meter_code, usage.corrects_usage_id
            )
            if stored is None:  # pragma: no cover — the constraint that fired guarantees a row
                raise
            return stored
        return usage

    def get(self, org_id: str, usage_id: str) -> CommercialUsage | None:
        row = self._one(
            select(rows.BillingCommercialUsageRow).where(
                rows.BillingCommercialUsageRow.org_id == org_id,
                rows.BillingCommercialUsageRow.id == usage_id,
            )
        )
        return from_row(CommercialUsage, row) if row else None

    def exists(self, org_id: str, event_id: str, meter_code: str) -> bool:
        """Whether an **original** record exists for this source event.

        Corrections are excluded deliberately: they carry the same source event by design, and
        counting one as "already ingested" would make a corrected event un-reingestible after a
        replay. The ingestion gate asks about originals; the constraint covers both."""
        return self._stored(org_id, event_id, meter_code, "") is not None

    def for_period(
        self, org_id: str, account_id: str, period_start: float, period_end: float
    ) -> tuple[CommercialUsage, ...]:
        found = self._all(
            select(rows.BillingCommercialUsageRow)
            .where(
                rows.BillingCommercialUsageRow.org_id == org_id,
                rows.BillingCommercialUsageRow.account_id == account_id,
                rows.BillingCommercialUsageRow.period_start == period_start,
                rows.BillingCommercialUsageRow.period_end == period_end,
            )
            .order_by(
                rows.BillingCommercialUsageRow.observed_at,
                rows.BillingCommercialUsageRow.id,
            )
        )
        return tuple(from_row(CommercialUsage, r) for r in found)

    def all(self, org_id: str, limit: int = 1000) -> tuple[CommercialUsage, ...]:
        found = self._all(
            select(rows.BillingCommercialUsageRow)
            .where(rows.BillingCommercialUsageRow.org_id == org_id)
            .order_by(
                rows.BillingCommercialUsageRow.observed_at,
                rows.BillingCommercialUsageRow.id,
            )
            .limit(limit)
        )
        return tuple(from_row(CommercialUsage, r) for r in found)

    def _stored(
        self, org_id: str, event_id: str, meter_code: str, corrects: str = ""
    ) -> CommercialUsage | None:
        row = self._one(
            select(rows.BillingCommercialUsageRow).where(
                rows.BillingCommercialUsageRow.org_id == org_id,
                rows.BillingCommercialUsageRow.source_event_id == event_id,
                rows.BillingCommercialUsageRow.meter_code == meter_code,
                rows.BillingCommercialUsageRow.corrects_usage_id == corrects,
            )
        )
        return from_row(CommercialUsage, row) if row else None


class SqlUsagePeriodRepository(_Repo, UsagePeriodRepository):
    def add(self, period: UsagePeriod) -> UsagePeriod:
        self._add(rows.BillingUsagePeriodRow(payload=encode(period), **_period_projection(period)))
        return period

    def get(self, org_id: str, period_id: str) -> UsagePeriod | None:
        row = self._one(
            select(rows.BillingUsagePeriodRow).where(
                rows.BillingUsagePeriodRow.org_id == org_id,
                rows.BillingUsagePeriodRow.id == period_id,
            )
        )
        return from_row(UsagePeriod, row) if row else None

    def for_account(
        self, org_id: str, account_id: str, period_start: float, period_end: float
    ) -> UsagePeriod | None:
        row = self._one(
            select(rows.BillingUsagePeriodRow).where(
                rows.BillingUsagePeriodRow.org_id == org_id,
                rows.BillingUsagePeriodRow.account_id == account_id,
                rows.BillingUsagePeriodRow.period_start == period_start,
                rows.BillingUsagePeriodRow.period_end == period_end,
            )
        )
        return from_row(UsagePeriod, row) if row else None

    def all(self, org_id: str, account_id: str = "") -> tuple[UsagePeriod, ...]:
        statement = select(rows.BillingUsagePeriodRow).where(
            rows.BillingUsagePeriodRow.org_id == org_id
        )
        if account_id:
            statement = statement.where(rows.BillingUsagePeriodRow.account_id == account_id)
        found = self._all(statement.order_by(rows.BillingUsagePeriodRow.period_start))
        return tuple(from_row(UsagePeriod, r) for r in found)

    def replace(self, period: UsagePeriod) -> UsagePeriod:
        existing = self._one(
            select(rows.BillingUsagePeriodRow).where(
                rows.BillingUsagePeriodRow.org_id == period.org_id,
                rows.BillingUsagePeriodRow.id == period.id,
            )
        )
        if existing is None:
            return self.add(period)
        update_row(existing, period, _period_projection(period))
        self._session.flush()
        return period


class SqlUsageAggregateSnapshotRepository(_Repo, UsageAggregateSnapshotRepository):
    """Derived aggregates: replaceable by construction, because they are recomputable from usage
    at any time and a rebuild must *replace* rather than accumulate."""

    def upsert(self, aggregate: UsageAggregateSnapshot) -> UsageAggregateSnapshot:
        existing = self._one(
            select(rows.BillingUsageAggregateSnapshotRow).where(
                rows.BillingUsageAggregateSnapshotRow.org_id == aggregate.org_id,
                rows.BillingUsageAggregateSnapshotRow.id == aggregate.id,
            )
        )
        if existing is None:
            self._add(
                rows.BillingUsageAggregateSnapshotRow(
                    payload=encode(aggregate), **_aggregate_projection(aggregate)
                )
            )
            return aggregate
        update_row(existing, aggregate, _aggregate_projection(aggregate))
        self._session.flush()
        return aggregate

    def get(self, org_id: str, aggregate_id: str) -> UsageAggregateSnapshot | None:
        row = self._one(
            select(rows.BillingUsageAggregateSnapshotRow).where(
                rows.BillingUsageAggregateSnapshotRow.org_id == org_id,
                rows.BillingUsageAggregateSnapshotRow.id == aggregate_id,
            )
        )
        return from_row(UsageAggregateSnapshot, row) if row else None

    def for_period(
        self, org_id: str, account_id: str, period_start: float, period_end: float
    ) -> tuple[UsageAggregateSnapshot, ...]:
        found = self._all(
            select(rows.BillingUsageAggregateSnapshotRow)
            .where(
                rows.BillingUsageAggregateSnapshotRow.org_id == org_id,
                rows.BillingUsageAggregateSnapshotRow.account_id == account_id,
                rows.BillingUsageAggregateSnapshotRow.period_start == period_start,
                rows.BillingUsageAggregateSnapshotRow.period_end == period_end,
            )
            .order_by(
                rows.BillingUsageAggregateSnapshotRow.meter_code,
                rows.BillingUsageAggregateSnapshotRow.window_start,
            )
        )
        return tuple(from_row(UsageAggregateSnapshot, r) for r in found)


class SqlQuarantineEntryRepository(_Repo, QuarantineEntryRepository):
    """No delete: unattributable usage must stay visible (M6)."""

    def add(self, entry: QuarantineEntry) -> QuarantineEntry:
        self._add(
            rows.BillingQuarantineEntryRow(payload=encode(entry), **_quarantine_projection(entry))
        )
        return entry

    def get(self, org_id: str, entry_id: str) -> QuarantineEntry | None:
        row = self._one(
            select(rows.BillingQuarantineEntryRow).where(
                rows.BillingQuarantineEntryRow.org_id == org_id,
                rows.BillingQuarantineEntryRow.id == entry_id,
            )
        )
        return from_row(QuarantineEntry, row) if row else None

    def pending(self, org_id: str, limit: int = 100) -> tuple[QuarantineEntry, ...]:
        found = self._all(
            select(rows.BillingQuarantineEntryRow)
            .where(
                rows.BillingQuarantineEntryRow.org_id == org_id,
                rows.BillingQuarantineEntryRow.state == "pending",
            )
            .order_by(rows.BillingQuarantineEntryRow.received_at)
            .limit(limit)
        )
        return tuple(from_row(QuarantineEntry, r) for r in found)

    def all(self, org_id: str, limit: int = 500) -> tuple[QuarantineEntry, ...]:
        found = self._all(
            select(rows.BillingQuarantineEntryRow)
            .where(rows.BillingQuarantineEntryRow.org_id == org_id)
            .order_by(rows.BillingQuarantineEntryRow.received_at)
            .limit(limit)
        )
        return tuple(from_row(QuarantineEntry, r) for r in found)

    def replace(self, entry: QuarantineEntry) -> QuarantineEntry:
        existing = self._one(
            select(rows.BillingQuarantineEntryRow).where(
                rows.BillingQuarantineEntryRow.org_id == entry.org_id,
                rows.BillingQuarantineEntryRow.id == entry.id,
            )
        )
        if existing is None:
            return self.add(entry)
        update_row(existing, entry, _quarantine_projection(entry))
        self._session.flush()
        return entry


class SqlUsageCorrectionRepository(_Repo, UsageCorrectionRepository):
    """Append-only. The derived id makes applying one correction twice a single record."""

    def append(self, correction: UsageCorrection) -> UsageCorrection:
        existing = self._one(
            select(rows.BillingUsageCorrectionRow).where(
                rows.BillingUsageCorrectionRow.org_id == correction.org_id,
                rows.BillingUsageCorrectionRow.id == correction.id,
            )
        )
        if existing is not None:
            return from_row(UsageCorrection, existing)
        self._add(
            rows.BillingUsageCorrectionRow(
                payload=encode(correction), **_correction_projection(correction)
            )
        )
        return correction

    def for_usage(self, org_id: str, usage_id: str) -> tuple[UsageCorrection, ...]:
        found = self._all(
            select(rows.BillingUsageCorrectionRow)
            .where(
                rows.BillingUsageCorrectionRow.org_id == org_id,
                rows.BillingUsageCorrectionRow.corrects_usage_id == usage_id,
            )
            .order_by(rows.BillingUsageCorrectionRow.created_at)
        )
        return tuple(from_row(UsageCorrection, r) for r in found)


class SqlMeteringActivityRepository(_Repo, MeteringActivityRepository):
    """Append-only metering history."""

    def append(self, activity: MeteringActivity) -> MeteringActivity:
        self._add(
            rows.BillingMeteringActivityRow(
                payload=encode(activity), **_activity_projection(activity)
            )
        )
        return activity

    def history(
        self, org_id: str, account_id: str = "", limit: int = 100
    ) -> tuple[MeteringActivity, ...]:
        statement = select(rows.BillingMeteringActivityRow).where(
            rows.BillingMeteringActivityRow.org_id == org_id
        )
        if account_id:
            statement = statement.where(rows.BillingMeteringActivityRow.account_id == account_id)
        found = self._all(
            statement.order_by(
                rows.BillingMeteringActivityRow.at.desc(),  # type: ignore[attr-defined]
                rows.BillingMeteringActivityRow.id.desc(),  # type: ignore[attr-defined]
            ).limit(limit)
        )
        return tuple(from_row(MeteringActivity, r) for r in found)


METERING_SQL_REPOSITORIES = (
    (CommercialMeterRepository, SqlCommercialMeterRepository),
    (CommercialUsageRepository, SqlCommercialUsageRepository),
    (UsagePeriodRepository, SqlUsagePeriodRepository),
    (UsageAggregateSnapshotRepository, SqlUsageAggregateSnapshotRepository),
    (QuarantineEntryRepository, SqlQuarantineEntryRepository),
    (UsageCorrectionRepository, SqlUsageCorrectionRepository),
    (MeteringActivityRepository, SqlMeteringActivityRepository),
)
