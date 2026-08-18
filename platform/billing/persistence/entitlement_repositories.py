"""Entitlement Engine repositories (M9.10) — additive to the M9.4 persistence layer.

Five SQL implementations of the M9.10 Foundation contracts, reusing the M9.4 machinery unchanged:
the same `_Repo` session handle, the same codec, the same projection-plus-payload mapping, the
same "never commit, never roll back" rule, and the same org-scoped-by-construction reads (§A11.2).

Two are append-only and expose no update or delete method at all: published entitlement versions
and entitlement activity. `next_version` is monotonic and never reuses a number — including across
a rollback, which moves *forward* to restore an earlier decision.

No repository here resolves precedence, expands a bundle, decides a lifecycle move, or composes a
snapshot. `for_account` is one indexed row selection; everything else is a Foundation decision,
and a repository that made one would be putting entitlement logic where nobody would look for it.
"""

from __future__ import annotations

from sqlmodel import select

from billing.foundation import (
    EntitlementActivity,
    EntitlementActivityRepository,
    EntitlementBundle,
    EntitlementBundleRepository,
    EntitlementDefinition,
    EntitlementDefinitionRepository,
    EntitlementProfile,
    EntitlementProfileRepository,
    EntitlementVersion,
    EntitlementVersionRepository,
    ImmutabilityViolation,
)

from . import entitlement_models as rows
from .codec import encode
from .mapping import from_row, update_row
from .repositories import _Repo


def _definition_projection(d: EntitlementDefinition) -> dict:
    return {
        "id": d.id,
        "org_id": d.org_id,
        "code": d.code,
        "display_name": d.display_name,
        "kind": d.kind.value,
        "classification": d.classification.value,
        "meter_code": d.meter_code,
        "max_staleness_seconds": d.max_staleness_seconds,
        "default_enabled": d.default.enabled,
        "default_allowance": d.default.allowance,
        "version": d.version,
        "created_at": d.created_at,
        "updated_at": d.updated_at,
        "retired_at": d.retired_at,
    }


def _bundle_projection(b: EntitlementBundle) -> dict:
    return {
        "id": b.id,
        "org_id": b.org_id,
        "code": b.code,
        "display_name": b.display_name,
        "entry_count": len(b.entries),
        "member_count": len(b.member_bundles),
        "version": b.version,
        "created_at": b.created_at,
        "updated_at": b.updated_at,
    }


def _profile_projection(p: EntitlementProfile) -> dict:
    return {
        "id": p.id,
        "org_id": p.org_id,
        "account_id": p.account_id,
        "subscription_id": p.subscription_id,
        "version": p.version,
        "max_staleness_seconds": p.max_staleness_seconds,
        "grant_count": len(p.grants),
        "live_grant_count": len(p.live_grants),
        "pending_change_count": len(p.pending_changes),
        "created_at": p.created_at,
        "updated_at": p.updated_at,
    }


def _version_projection(v: EntitlementVersion) -> dict:
    embedded = v.entitlements
    return {
        "id": v.id,
        "org_id": v.org_id,
        "account_id": v.account_id,
        "version": v.version,
        "published_at": v.published_at,
        "published_by": v.published_by,
        "reason": v.reason,
        "rolled_back_from": v.rolled_back_from,
        "entitlement_count": v.entitlement_count,
        "grant_count": v.grant_count,
        "subscription_id": embedded.subscription_id if embedded else "",
        "max_staleness_seconds": embedded.max_staleness_seconds if embedded else 3600.0,
    }


def _activity_projection(a: EntitlementActivity) -> dict:
    return {
        "id": a.id,
        "org_id": a.org_id,
        "account_id": a.account_id,
        "action": a.action.value,
        "actor_id": a.actor_id,
        "at": a.at,
        "entitlement_code": a.entitlement_code,
        "grant_id": a.grant_id,
        "from_state": a.from_state,
        "to_state": a.to_state,
        "version": a.version,
    }


class SqlEntitlementDefinitionRepository(_Repo, EntitlementDefinitionRepository):
    """The gate registry. Retirement is a field, not a deletion — a gate an enforcing plane may
    still be asking about must remain readable."""

    def add(self, definition: EntitlementDefinition) -> EntitlementDefinition:
        self._add(
            rows.BillingEntitlementDefinitionRow(
                payload=encode(definition), **_definition_projection(definition)
            )
        )
        return definition

    def get(self, org_id: str, definition_id: str) -> EntitlementDefinition | None:
        row = self._one(
            select(rows.BillingEntitlementDefinitionRow).where(
                rows.BillingEntitlementDefinitionRow.org_id == org_id,
                rows.BillingEntitlementDefinitionRow.id == definition_id,
            )
        )
        return from_row(EntitlementDefinition, row) if row else None

    def by_code(self, org_id: str, code: str) -> EntitlementDefinition | None:
        row = self._one(
            select(rows.BillingEntitlementDefinitionRow).where(
                rows.BillingEntitlementDefinitionRow.org_id == org_id,
                rows.BillingEntitlementDefinitionRow.code == code,
            )
        )
        return from_row(EntitlementDefinition, row) if row else None

    def all(self, org_id: str) -> tuple[EntitlementDefinition, ...]:
        found = self._all(
            select(rows.BillingEntitlementDefinitionRow)
            .where(rows.BillingEntitlementDefinitionRow.org_id == org_id)
            .order_by(rows.BillingEntitlementDefinitionRow.code)
        )
        return tuple(from_row(EntitlementDefinition, r) for r in found)

    def replace(self, definition: EntitlementDefinition) -> EntitlementDefinition:
        existing = self._one(
            select(rows.BillingEntitlementDefinitionRow).where(
                rows.BillingEntitlementDefinitionRow.org_id == definition.org_id,
                rows.BillingEntitlementDefinitionRow.id == definition.id,
            )
        )
        if existing is None:
            return self.add(definition)
        update_row(existing, definition, _definition_projection(definition))
        self._session.flush()
        return definition


class SqlEntitlementBundleRepository(_Repo, EntitlementBundleRepository):
    def add(self, bundle: EntitlementBundle) -> EntitlementBundle:
        self._add(
            rows.BillingEntitlementBundleRow(payload=encode(bundle), **_bundle_projection(bundle))
        )
        return bundle

    def get(self, org_id: str, bundle_id: str) -> EntitlementBundle | None:
        row = self._one(
            select(rows.BillingEntitlementBundleRow).where(
                rows.BillingEntitlementBundleRow.org_id == org_id,
                rows.BillingEntitlementBundleRow.id == bundle_id,
            )
        )
        return from_row(EntitlementBundle, row) if row else None

    def by_code(self, org_id: str, code: str) -> EntitlementBundle | None:
        row = self._one(
            select(rows.BillingEntitlementBundleRow).where(
                rows.BillingEntitlementBundleRow.org_id == org_id,
                rows.BillingEntitlementBundleRow.code == code,
            )
        )
        return from_row(EntitlementBundle, row) if row else None

    def all(self, org_id: str) -> tuple[EntitlementBundle, ...]:
        found = self._all(
            select(rows.BillingEntitlementBundleRow)
            .where(rows.BillingEntitlementBundleRow.org_id == org_id)
            .order_by(rows.BillingEntitlementBundleRow.code)
        )
        return tuple(from_row(EntitlementBundle, r) for r in found)

    def replace(self, bundle: EntitlementBundle) -> EntitlementBundle:
        existing = self._one(
            select(rows.BillingEntitlementBundleRow).where(
                rows.BillingEntitlementBundleRow.org_id == bundle.org_id,
                rows.BillingEntitlementBundleRow.id == bundle.id,
            )
        )
        if existing is None:
            return self.add(bundle)
        update_row(existing, bundle, _bundle_projection(bundle))
        self._session.flush()
        return bundle


class SqlEntitlementProfileRepository(_Repo, EntitlementProfileRepository):
    """Upsert on the primary key — `add` means *store this position*, which is what makes
    `replace` and `add` the same operation for a whole aggregate. Grants travel with it, so a
    resolution never sees half an account's entitlements."""

    def add(self, profile: EntitlementProfile) -> EntitlementProfile:
        existing = self._one(
            select(rows.BillingEntitlementProfileRow).where(
                rows.BillingEntitlementProfileRow.org_id == profile.org_id,
                rows.BillingEntitlementProfileRow.id == profile.id,
            )
        )
        if existing is not None:
            update_row(existing, profile, _profile_projection(profile))
            self._session.flush()
            return profile
        self._add(
            rows.BillingEntitlementProfileRow(
                payload=encode(profile), **_profile_projection(profile)
            )
        )
        return profile

    def get(self, org_id: str, profile_id: str) -> EntitlementProfile | None:
        row = self._one(
            select(rows.BillingEntitlementProfileRow).where(
                rows.BillingEntitlementProfileRow.org_id == org_id,
                rows.BillingEntitlementProfileRow.id == profile_id,
            )
        )
        return from_row(EntitlementProfile, row) if row else None

    def for_account(self, org_id: str, account_id: str) -> EntitlementProfile | None:
        row = self._one(
            select(rows.BillingEntitlementProfileRow).where(
                rows.BillingEntitlementProfileRow.org_id == org_id,
                rows.BillingEntitlementProfileRow.account_id == account_id,
            )
        )
        return from_row(EntitlementProfile, row) if row else None

    def all(self, org_id: str) -> tuple[EntitlementProfile, ...]:
        found = self._all(
            select(rows.BillingEntitlementProfileRow)
            .where(rows.BillingEntitlementProfileRow.org_id == org_id)
            .order_by(rows.BillingEntitlementProfileRow.account_id)
        )
        return tuple(from_row(EntitlementProfile, r) for r in found)

    def replace(self, profile: EntitlementProfile) -> EntitlementProfile:
        return self.add(profile)


class SqlEntitlementVersionRepository(_Repo, EntitlementVersionRepository):
    """Append-only. There is no update and no delete method to call, and the table's unique
    `(org_id, account_id, version)` makes republishing a version a database-level error."""

    def publish(self, version: EntitlementVersion) -> EntitlementVersion:
        existing = self._one(
            select(rows.BillingEntitlementVersionRow).where(
                rows.BillingEntitlementVersionRow.org_id == version.org_id,
                rows.BillingEntitlementVersionRow.account_id == version.account_id,
                rows.BillingEntitlementVersionRow.version == version.version,
            )
        )
        if existing is not None:
            # Defence in depth above the constraint: a published snapshot is what an enforcing
            # plane acted on, and a decision that could be replaced would erase the reason a past
            # request was allowed or refused.
            raise ImmutabilityViolation(
                f"entitlement version {version.version} is already published and is immutable"
            )
        self._add(
            rows.BillingEntitlementVersionRow(
                payload=encode(version), **_version_projection(version)
            )
        )
        return version

    def get(self, org_id: str, account_id: str, version: int) -> EntitlementVersion | None:
        row = self._one(
            select(rows.BillingEntitlementVersionRow).where(
                rows.BillingEntitlementVersionRow.org_id == org_id,
                rows.BillingEntitlementVersionRow.account_id == account_id,
                rows.BillingEntitlementVersionRow.version == version,
            )
        )
        return from_row(EntitlementVersion, row) if row else None

    def latest(self, org_id: str, account_id: str) -> EntitlementVersion | None:
        row = self._one(
            select(rows.BillingEntitlementVersionRow)
            .where(
                rows.BillingEntitlementVersionRow.org_id == org_id,
                rows.BillingEntitlementVersionRow.account_id == account_id,
            )
            .order_by(rows.BillingEntitlementVersionRow.version.desc())  # type: ignore[attr-defined]
        )
        return from_row(EntitlementVersion, row) if row else None

    def history(
        self, org_id: str, account_id: str, limit: int = 50
    ) -> tuple[EntitlementVersion, ...]:
        found = self._all(
            select(rows.BillingEntitlementVersionRow)
            .where(
                rows.BillingEntitlementVersionRow.org_id == org_id,
                rows.BillingEntitlementVersionRow.account_id == account_id,
            )
            .order_by(rows.BillingEntitlementVersionRow.version.desc())  # type: ignore[attr-defined]
            .limit(limit)
        )
        return tuple(from_row(EntitlementVersion, r) for r in found)

    def next_version(self, org_id: str, account_id: str) -> int:
        latest = self.latest(org_id, account_id)
        return 1 if latest is None else latest.version + 1


class SqlEntitlementActivityRepository(_Repo, EntitlementActivityRepository):
    """Append-only entitlement history."""

    def append(self, activity: EntitlementActivity) -> EntitlementActivity:
        self._add(
            rows.BillingEntitlementActivityRow(
                payload=encode(activity), **_activity_projection(activity)
            )
        )
        return activity

    def history(
        self, org_id: str, account_id: str = "", limit: int = 100
    ) -> tuple[EntitlementActivity, ...]:
        statement = select(rows.BillingEntitlementActivityRow).where(
            rows.BillingEntitlementActivityRow.org_id == org_id
        )
        if account_id:
            statement = statement.where(rows.BillingEntitlementActivityRow.account_id == account_id)
        found = self._all(
            statement.order_by(
                rows.BillingEntitlementActivityRow.at.desc(),  # type: ignore[attr-defined]
                rows.BillingEntitlementActivityRow.id.desc(),  # type: ignore[attr-defined]
            ).limit(limit)
        )
        return tuple(from_row(EntitlementActivity, r) for r in found)


ENTITLEMENT_SQL_REPOSITORIES = (
    (EntitlementDefinitionRepository, SqlEntitlementDefinitionRepository),
    (EntitlementBundleRepository, SqlEntitlementBundleRepository),
    (EntitlementProfileRepository, SqlEntitlementProfileRepository),
    (EntitlementVersionRepository, SqlEntitlementVersionRepository),
    (EntitlementActivityRepository, SqlEntitlementActivityRepository),
)
