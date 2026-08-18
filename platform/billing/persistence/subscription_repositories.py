"""Subscription Engine repositories (M9.9) — additive to the M9.4 persistence layer.

Four SQL implementations of the M9.9 Foundation contracts, reusing the M9.4 machinery
unchanged: the same `_Repo` session handle, the same codec, the same projection-plus-payload
mapping, the same "never commit, never roll back" rule, and the same org-scoped-by-construction
reads (§A11.2).

Two are append-only and expose no update or delete method at all: published subscription
versions and subscription activity. `next_version` is monotonic and never reuses a number —
including across a rollback, which moves *forward* to restore earlier terms.

No repository here decides a lifecycle move, advances a cycle, resolves a trial, or applies a
scheduled change. `for_account` is one indexed row selection; everything else is a Foundation
decision, and a repository that made one would be putting commercial logic where nobody would
look for it.
"""

from __future__ import annotations

from sqlmodel import select

from billing.foundation import (
    CommercialSubscription,
    CommercialSubscriptionRepository,
    ImmutabilityViolation,
    SubscriptionActivity,
    SubscriptionActivityRepository,
    SubscriptionNumberAllocator,
    SubscriptionVersion,
    SubscriptionVersionRepository,
    format_subscription_number,
)

from . import subscription_models as rows
from .codec import encode
from .mapping import from_row, update_row
from .repositories import _Repo


def _subscription_projection(s: CommercialSubscription) -> dict:
    period = s.cycle.current_period
    return {
        "id": s.id,
        "org_id": s.org_id,
        "subscription_number": s.subscription_number,
        "account_id": s.account_id,
        "catalog_version": s.catalog_version,
        "pricing_version": s.pricing_version,
        "currency": s.currency.value,
        "plan_code": s.plan_code,
        "state": s.state.value,
        "version": s.version,
        "interval": s.cycle.interval.value,
        "cycle_number": s.cycle.cycle_number,
        "period_start": period.start if period else None,
        "period_end": period.end if period else None,
        "trial_ends_at": s.trial.ends_at,
        "renewal_mode": s.renewal.mode.value,
        "item_count": len(s.items),
        "seat_count": s.seat_count,
        "pending_change_count": len(s.pending_changes),
        "contract_id": s.contract_id,
        "created_at": s.created_at,
        "updated_at": s.updated_at,
        "cancelled_at": s.cancelled_at,
        "ended_at": s.ended_at,
    }


def _version_projection(v: SubscriptionVersion) -> dict:
    embedded = v.subscription
    return {
        "id": v.id,
        "org_id": v.org_id,
        "subscription_id": v.subscription_id,
        "version": v.version,
        "published_at": v.published_at,
        "published_by": v.published_by,
        "reason": v.reason,
        "rolled_back_from": v.rolled_back_from,
        "catalog_version": embedded.catalog_version if embedded else None,
        "pricing_version": embedded.pricing_version if embedded else None,
        "state": embedded.state.value if embedded else "",
        "item_count": v.item_count,
    }


def _activity_projection(a: SubscriptionActivity) -> dict:
    return {
        "id": a.id,
        "org_id": a.org_id,
        "subscription_id": a.subscription_id,
        "action": a.action.value,
        "actor_id": a.actor_id,
        "at": a.at,
        "from_state": a.from_state,
        "to_state": a.to_state,
        "version": a.version,
    }


class SqlCommercialSubscriptionRepository(_Repo, CommercialSubscriptionRepository):
    """Upsert on the primary key — `add` means *store this agreement*, which is what makes
    `replace` and `add` the same operation for a whole aggregate."""

    def add(self, subscription: CommercialSubscription) -> CommercialSubscription:
        existing = self._one(
            select(rows.BillingCommercialSubscriptionRow).where(
                rows.BillingCommercialSubscriptionRow.org_id == subscription.org_id,
                rows.BillingCommercialSubscriptionRow.id == subscription.id,
            )
        )
        if existing is not None:
            update_row(existing, subscription, _subscription_projection(subscription))
            self._session.flush()
            return subscription
        self._add(
            rows.BillingCommercialSubscriptionRow(
                payload=encode(subscription), **_subscription_projection(subscription)
            )
        )
        return subscription

    def get(self, org_id: str, subscription_id: str) -> CommercialSubscription | None:
        row = self._one(
            select(rows.BillingCommercialSubscriptionRow).where(
                rows.BillingCommercialSubscriptionRow.org_id == org_id,
                rows.BillingCommercialSubscriptionRow.id == subscription_id,
            )
        )
        return from_row(CommercialSubscription, row) if row else None

    def by_number(self, org_id: str, number: str) -> CommercialSubscription | None:
        row = self._one(
            select(rows.BillingCommercialSubscriptionRow).where(
                rows.BillingCommercialSubscriptionRow.org_id == org_id,
                rows.BillingCommercialSubscriptionRow.subscription_number == number,
            )
        )
        return from_row(CommercialSubscription, row) if row else None

    def all(self, org_id: str) -> tuple[CommercialSubscription, ...]:
        found = self._all(
            select(rows.BillingCommercialSubscriptionRow)
            .where(rows.BillingCommercialSubscriptionRow.org_id == org_id)
            .order_by(rows.BillingCommercialSubscriptionRow.subscription_number)
        )
        return tuple(from_row(CommercialSubscription, r) for r in found)

    def for_account(self, org_id: str, account_id: str) -> tuple[CommercialSubscription, ...]:
        """One indexed row selection. Which of them are live, impaired, or due for renewal is
        the Foundation's to decide."""
        found = self._all(
            select(rows.BillingCommercialSubscriptionRow)
            .where(
                rows.BillingCommercialSubscriptionRow.org_id == org_id,
                rows.BillingCommercialSubscriptionRow.account_id == account_id,
            )
            .order_by(rows.BillingCommercialSubscriptionRow.subscription_number)
        )
        return tuple(from_row(CommercialSubscription, r) for r in found)

    def replace(self, subscription: CommercialSubscription) -> CommercialSubscription:
        return self.add(subscription)


class SqlSubscriptionVersionRepository(_Repo, SubscriptionVersionRepository):
    """Append-only. There is no update and no delete method to call, and the table's unique
    `(org_id, subscription_id, version)` makes republishing a version a database-level error."""

    def publish(self, version: SubscriptionVersion) -> SubscriptionVersion:
        existing = self._one(
            select(rows.BillingSubscriptionVersionRow).where(
                rows.BillingSubscriptionVersionRow.org_id == version.org_id,
                rows.BillingSubscriptionVersionRow.subscription_id == version.subscription_id,
                rows.BillingSubscriptionVersionRow.version == version.version,
            )
        )
        if existing is not None:
            # Defence in depth above the constraint: a published version records what a
            # customer agreed to, and terms that could be replaced would not be a record.
            raise ImmutabilityViolation(
                f"subscription version {version.version} is already published and is immutable"
            )
        self._add(
            rows.BillingSubscriptionVersionRow(
                payload=encode(version), **_version_projection(version)
            )
        )
        return version

    def get(self, org_id: str, subscription_id: str, version: int) -> SubscriptionVersion | None:
        row = self._one(
            select(rows.BillingSubscriptionVersionRow).where(
                rows.BillingSubscriptionVersionRow.org_id == org_id,
                rows.BillingSubscriptionVersionRow.subscription_id == subscription_id,
                rows.BillingSubscriptionVersionRow.version == version,
            )
        )
        return from_row(SubscriptionVersion, row) if row else None

    def latest(self, org_id: str, subscription_id: str) -> SubscriptionVersion | None:
        row = self._one(
            select(rows.BillingSubscriptionVersionRow)
            .where(
                rows.BillingSubscriptionVersionRow.org_id == org_id,
                rows.BillingSubscriptionVersionRow.subscription_id == subscription_id,
            )
            .order_by(rows.BillingSubscriptionVersionRow.version.desc())  # type: ignore[attr-defined]
        )
        return from_row(SubscriptionVersion, row) if row else None

    def history(
        self, org_id: str, subscription_id: str, limit: int = 50
    ) -> tuple[SubscriptionVersion, ...]:
        found = self._all(
            select(rows.BillingSubscriptionVersionRow)
            .where(
                rows.BillingSubscriptionVersionRow.org_id == org_id,
                rows.BillingSubscriptionVersionRow.subscription_id == subscription_id,
            )
            .order_by(rows.BillingSubscriptionVersionRow.version.desc())  # type: ignore[attr-defined]
            .limit(limit)
        )
        return tuple(from_row(SubscriptionVersion, r) for r in found)

    def next_version(self, org_id: str, subscription_id: str) -> int:
        latest = self.latest(org_id, subscription_id)
        return 1 if latest is None else latest.version + 1


class SqlSubscriptionActivityRepository(_Repo, SubscriptionActivityRepository):
    """Append-only subscription history."""

    def append(self, activity: SubscriptionActivity) -> SubscriptionActivity:
        self._add(
            rows.BillingSubscriptionActivityRow(
                payload=encode(activity), **_activity_projection(activity)
            )
        )
        return activity

    def history(
        self, org_id: str, subscription_id: str = "", limit: int = 100
    ) -> tuple[SubscriptionActivity, ...]:
        statement = select(rows.BillingSubscriptionActivityRow).where(
            rows.BillingSubscriptionActivityRow.org_id == org_id
        )
        if subscription_id:
            statement = statement.where(
                rows.BillingSubscriptionActivityRow.subscription_id == subscription_id
            )
        found = self._all(
            statement.order_by(
                rows.BillingSubscriptionActivityRow.at.desc(),  # type: ignore[attr-defined]
                rows.BillingSubscriptionActivityRow.id.desc(),  # type: ignore[attr-defined]
            ).limit(limit)
        )
        return tuple(from_row(SubscriptionActivity, r) for r in found)


class SqlSubscriptionNumberAllocator(_Repo, SubscriptionNumberAllocator):
    """A per-organization monotonic sequence.

    A stored counter rather than `max(number) + 1`: a subscription number appears on an
    invoice, and handing a retired one to a different customer would make two agreements
    indistinguishable in correspondence."""

    def next_number(self, org_id: str) -> str:
        row = self._one(
            select(rows.BillingSubscriptionNumberRow).where(
                rows.BillingSubscriptionNumberRow.org_id == org_id
            )
        )
        if row is None:
            row = rows.BillingSubscriptionNumberRow(org_id=org_id, last_sequence=0)
            self._add(row)
        row.last_sequence += 1
        self._session.flush()
        return format_subscription_number(row.last_sequence)


SUBSCRIPTION_SQL_REPOSITORIES = (
    (CommercialSubscriptionRepository, SqlCommercialSubscriptionRepository),
    (SubscriptionVersionRepository, SqlSubscriptionVersionRepository),
    (SubscriptionActivityRepository, SqlSubscriptionActivityRepository),
    (SubscriptionNumberAllocator, SqlSubscriptionNumberAllocator),
)
