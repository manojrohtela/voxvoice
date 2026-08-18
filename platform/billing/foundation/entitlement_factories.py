"""Entitlement factories + repository contracts (M9.10).

`EntitlementFactory` is the one place an entitlement definition, bundle, grant, profile, or
version is constructed, following the M9.2 factory discipline exactly: validate structurally,
**fail fast** on error-severity findings (`BillingValidationError`, §A2.6), compose an immutable
aggregate from the injected id and clock seams, and emit the corresponding `billing.*` fact
through the injected emitter.

**Exactly one event per business fact.** A rejected command emits nothing at all — the fail-fast
check runs before any emission, so a validation failure leaves no trace on the fact stream. A
test asserts it.

No I/O, no persistence, no networking. Emission is the only effect and it is injected.

The repository interfaces below extend the M9.2 seam additively — same shape, same rules:
org-scoped reads by construction (§A11.2), and the two append-only stores (entitlement versions,
entitlement activity) deliberately declare **no update and no delete**. A published entitlement
snapshot is what an enforcing plane acted on; a version that could be edited would erase the
reason a past request was allowed or refused.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Sequence
from dataclasses import replace

from .catalog import FeatureKind
from .commercial_entitlements import (
    BundleEntry,
    CommercialGrant,
    EntitlementAction,
    EntitlementActivity,
    EntitlementBundle,
    EntitlementChangeKind,
    EntitlementDefinition,
    EntitlementProfile,
    EntitlementSet,
    EntitlementValue,
    EntitlementVersion,
    EntitlementWindow,
    GrantState,
    ScheduledEntitlementChange,
)
from .entitlement import GateClassification, GrantSource
from .entitlement_rules import (
    EntitlementAssembler,
    EntitlementBundleExpander,
    EntitlementValidator,
)
from .errors import BillingNotFound, BillingValidationError
from .events import BillingEvent, BillingEventType, EventEmitter, NullEventEmitter
from .seams import Clock, IdGenerator, SequentialIdGenerator, SystemClock

PREFIX_ENTITLEMENT_DEFINITION = "edf"
PREFIX_ENTITLEMENT_BUNDLE = "ebn"
PREFIX_ENTITLEMENT_GRANT = "egr"
PREFIX_ENTITLEMENT_PROFILE = "epf"
PREFIX_ENTITLEMENT_SET = "est"
PREFIX_ENTITLEMENT_VERSION = "evr"
PREFIX_ENTITLEMENT_ACTIVITY = "eac"
PREFIX_ENTITLEMENT_CHANGE = "ech"


class EntitlementFactory:
    """Composes validated entitlement aggregates. Stateless apart from its injected seams."""

    def __init__(
        self,
        *,
        ids: IdGenerator | None = None,
        clock: Clock | None = None,
        emitter: EventEmitter | None = None,
        validator: EntitlementValidator | None = None,
        assembler: EntitlementAssembler | None = None,
        expander: EntitlementBundleExpander | None = None,
    ) -> None:
        self._ids = ids or SequentialIdGenerator()
        self._clock = clock or SystemClock()
        self._emitter = emitter or NullEventEmitter()
        self._validator = validator or EntitlementValidator()
        self._assembler = assembler or EntitlementAssembler()
        self._expander = expander or EntitlementBundleExpander()

    # ── definitions ─────────────────────────────────────────────────────────────

    def define_entitlement(
        self,
        *,
        org_id: str,
        code: str,
        display_name: str = "",
        kind: FeatureKind = FeatureKind.FLAG,
        classification: GateClassification = GateClassification.FAIL_OPEN,
        default: EntitlementValue | None = None,
        meter_code: str = "",
        max_staleness_seconds: float | None = None,
        description: str = "",
        actor_id: str = "",
    ) -> EntitlementDefinition:
        """Declare a capability and, with it, **how an enforcing plane must behave when it
        cannot see fresh data**. The classification is not optional and has no silent default
        beyond the safest one: ADR-0023 E3 makes an unclassified gate an incomplete design."""
        now = self._clock.now()
        definition = EntitlementDefinition(
            id=self._ids.new(PREFIX_ENTITLEMENT_DEFINITION),
            org_id=org_id,
            code=code,
            display_name=display_name or code,
            kind=kind,
            classification=classification,
            default=default if default is not None else EntitlementValue(enabled=False),
            meter_code=meter_code,
            max_staleness_seconds=(
                max_staleness_seconds
                if max_staleness_seconds is not None
                else EntitlementDefinition.__dataclass_fields__["max_staleness_seconds"].default
            ),
            description=description,
            version=1,
            created_at=now,
            updated_at=now,
        )
        self._fail_fast(self._validator.validate_definition(definition))
        self._emit(
            BillingEventType.ENTITLEMENT_DEFINED,
            org_id,
            definition.id,
            actor_id,
            code=code,
            kind=kind.value,
            classification=classification.value,
        )
        return definition

    def update_definition(
        self, definition: EntitlementDefinition, *, actor_id: str = "", **changes
    ) -> EntitlementDefinition:
        """Amend a declaration. The version increments because a changed classification changes
        how every enforcing plane behaves during an outage — that is a new declaration, not an
        edit of the old one."""
        amended = replace(
            definition,
            **{k: v for k, v in changes.items() if v is not None},
            version=definition.version + 1,
            updated_at=self._clock.now(),
        )
        self._fail_fast(self._validator.validate_definition(amended))
        self._emit(
            BillingEventType.ENTITLEMENT_DEFINITION_UPDATED,
            definition.org_id,
            definition.id,
            actor_id,
            code=definition.code,
            version=amended.version,
            fields=sorted(k for k, v in changes.items() if v is not None),
        )
        return amended

    def retire_definition(
        self, definition: EntitlementDefinition, *, reason: str = "", actor_id: str = ""
    ) -> EntitlementDefinition:
        """Retire a declaration without removing it. Existing grants keep working — withdrawing
        a capability from customers who hold it is a *revocation*, and conflating the two would
        let a catalog cleanup silently cancel a contract."""
        retired = replace(definition, retired_at=self._clock.now(), updated_at=self._clock.now())
        self._emit(
            BillingEventType.ENTITLEMENT_DEFINITION_RETIRED,
            definition.org_id,
            definition.id,
            actor_id,
            code=definition.code,
            reason=reason,
        )
        return retired

    # ── bundles ─────────────────────────────────────────────────────────────────

    def create_bundle(
        self,
        *,
        org_id: str,
        code: str,
        display_name: str = "",
        entries: Sequence[BundleEntry] = (),
        member_bundles: Sequence[str] = (),
        description: str = "",
        known_codes: frozenset[str] | None = None,
        known_bundles: frozenset[str] | None = None,
        actor_id: str = "",
    ) -> EntitlementBundle:
        now = self._clock.now()
        bundle = EntitlementBundle(
            id=self._ids.new(PREFIX_ENTITLEMENT_BUNDLE),
            org_id=org_id,
            code=code,
            display_name=display_name or code,
            entries=tuple(entries),
            member_bundles=tuple(member_bundles),
            description=description,
            version=1,
            created_at=now,
            updated_at=now,
        )
        self._fail_fast(
            self._validator.validate_bundle(
                bundle, known_codes=known_codes, known_bundles=known_bundles
            )
        )
        self._emit(
            BillingEventType.ENTITLEMENT_BUNDLE_CREATED,
            org_id,
            bundle.id,
            actor_id,
            code=code,
            entries=len(bundle.entries),
            members=len(bundle.member_bundles),
        )
        return bundle

    def update_bundle(
        self,
        bundle: EntitlementBundle,
        *,
        entries: Sequence[BundleEntry] | None = None,
        member_bundles: Sequence[str] | None = None,
        display_name: str | None = None,
        description: str | None = None,
        known_codes: frozenset[str] | None = None,
        known_bundles: frozenset[str] | None = None,
        actor_id: str = "",
    ) -> EntitlementBundle:
        amended = replace(
            bundle,
            entries=tuple(entries) if entries is not None else bundle.entries,
            member_bundles=(
                tuple(member_bundles) if member_bundles is not None else bundle.member_bundles
            ),
            display_name=display_name if display_name is not None else bundle.display_name,
            description=description if description is not None else bundle.description,
            version=bundle.version + 1,
            updated_at=self._clock.now(),
        )
        self._fail_fast(
            self._validator.validate_bundle(
                amended, known_codes=known_codes, known_bundles=known_bundles
            )
        )
        self._emit(
            BillingEventType.ENTITLEMENT_BUNDLE_UPDATED,
            bundle.org_id,
            bundle.id,
            actor_id,
            code=bundle.code,
            version=amended.version,
        )
        return amended

    def entry(self, code: str, value: EntitlementValue | None = None) -> BundleEntry:
        return BundleEntry(code=code, value=value if value is not None else EntitlementValue())

    # ── profiles ────────────────────────────────────────────────────────────────

    def open_profile(
        self,
        *,
        org_id: str,
        account_id: str,
        subscription_id: str = "",
        max_staleness_seconds: float | None = None,
        actor_id: str = "",
    ) -> EntitlementProfile:
        """Open an account's entitlement position. A profile starts empty and holds no
        capability — nothing is entitled until something is granted."""
        now = self._clock.now()
        profile = EntitlementProfile(
            id=self._ids.new(PREFIX_ENTITLEMENT_PROFILE),
            org_id=org_id,
            account_id=account_id,
            subscription_id=subscription_id,
            version=0,
            max_staleness_seconds=(
                max_staleness_seconds
                if max_staleness_seconds is not None
                else EntitlementProfile.__dataclass_fields__["max_staleness_seconds"].default
            ),
            created_at=now,
            updated_at=now,
        )
        self._fail_fast(self._validator.validate_profile(profile, at=now))
        self._emit(
            BillingEventType.ENTITLEMENT_PROFILE_OPENED,
            org_id,
            profile.id,
            actor_id,
            account_id=account_id,
            subscription_id=subscription_id,
        )
        return profile

    # ── grants ──────────────────────────────────────────────────────────────────

    def grant(
        self,
        profile: EntitlementProfile,
        *,
        entitlement_code: str,
        value: EntitlementValue | None = None,
        source: GrantSource = GrantSource.PLAN,
        source_ref: str = "",
        bundle_code: str = "",
        window: EntitlementWindow | None = None,
        precedence_override: int | None = None,
        reason: str = "",
        definition: EntitlementDefinition | None = None,
        known_codes: frozenset[str] | None = None,
        subscription_state: str | None = None,
        actor_id: str = "",
    ) -> tuple[EntitlementProfile, CommercialGrant]:
        """Confer a capability. The grant starts as a **draft** — a capability nobody has
        approved should not become effective by accident."""
        now = self._clock.now()
        grant = CommercialGrant(
            id=self._ids.new(PREFIX_ENTITLEMENT_GRANT),
            org_id=profile.org_id,
            account_id=profile.account_id,
            entitlement_code=entitlement_code,
            value=value if value is not None else EntitlementValue(),
            source=source,
            source_ref=source_ref,
            bundle_code=bundle_code,
            window=window if window is not None else EntitlementWindow(),
            state=GrantState.DRAFT,
            precedence_override=precedence_override,
            reason=reason,
            created_at=now,
            updated_at=now,
        )
        self._fail_fast(
            self._validator.validate_grant(
                grant,
                definition=definition,
                known_codes=known_codes,
                subscription_state=subscription_state,
            )
        )
        updated = replace(profile, grants=profile.grants + (grant,), updated_at=now)
        self._fail_fast(self._validator.validate_grants(updated.grants))
        self._emit(
            BillingEventType.ENTITLEMENT_GRANTED,
            profile.org_id,
            grant.id,
            actor_id,
            account_id=profile.account_id,
            code=entitlement_code,
            source=source.value,
        )
        return updated, grant

    def grant_bundle(
        self,
        profile: EntitlementProfile,
        bundles: Sequence[EntitlementBundle],
        *,
        bundle_code: str,
        source: GrantSource = GrantSource.PLAN,
        source_ref: str = "",
        window: EntitlementWindow | None = None,
        reason: str = "",
        known_codes: frozenset[str] | None = None,
        actor_id: str = "",
    ) -> tuple[EntitlementProfile, tuple[CommercialGrant, ...]]:
        """Confer a whole bundle: one grant per expanded entry, each carrying the bundle code.

        Expanded into individual grants rather than stored as a bundle reference, deliberately.
        A grant is the unit that gets revoked, suspended, and explained; a bundle reference
        would make "revoke just the SSO part" impossible without inventing an exception, and it
        would make the resolution path say *bundle* where a support conversation needs
        *capability*."""
        now = self._clock.now()
        entries = self._expander.expand(bundles, bundle_code)
        if not entries:
            raise BillingNotFound(f"entitlement bundle {bundle_code!r} confers nothing")
        grants: list[CommercialGrant] = []
        for entry in entries:
            grant = CommercialGrant(
                id=self._ids.new(PREFIX_ENTITLEMENT_GRANT),
                org_id=profile.org_id,
                account_id=profile.account_id,
                entitlement_code=entry.code,
                value=entry.value,
                source=source,
                source_ref=source_ref,
                bundle_code=bundle_code,
                window=window if window is not None else EntitlementWindow(),
                state=GrantState.DRAFT,
                reason=reason or f"bundle {bundle_code}",
                created_at=now,
                updated_at=now,
            )
            self._fail_fast(self._validator.validate_grant(grant, known_codes=known_codes))
            grants.append(grant)
        updated = replace(profile, grants=profile.grants + tuple(grants), updated_at=now)
        self._fail_fast(self._validator.validate_grants(updated.grants))
        self._emit(
            BillingEventType.ENTITLEMENT_BUNDLE_GRANTED,
            profile.org_id,
            profile.id,
            actor_id,
            account_id=profile.account_id,
            bundle=bundle_code,
            grants=len(grants),
        )
        return updated, tuple(grants)

    def adjust_grant(
        self,
        profile: EntitlementProfile,
        grant_id: str,
        *,
        value: EntitlementValue | None = None,
        window: EntitlementWindow | None = None,
        reason: str | None = None,
        actor_id: str = "",
    ) -> EntitlementProfile:
        existing = profile.grant(grant_id)
        if existing is None:
            raise BillingNotFound(f"entitlement grant {grant_id!r} not found")
        amended = replace(
            existing,
            value=value if value is not None else existing.value,
            window=window if window is not None else existing.window,
            reason=reason if reason is not None else existing.reason,
            updated_at=self._clock.now(),
        )
        self._fail_fast(self._validator.validate_grant(amended))
        updated = self.replace_grant(profile, amended)
        self._emit(
            BillingEventType.ENTITLEMENT_GRANT_ADJUSTED,
            profile.org_id,
            grant_id,
            actor_id,
            code=existing.entitlement_code,
        )
        return updated

    def replace_grant(
        self, profile: EntitlementProfile, grant: CommercialGrant
    ) -> EntitlementProfile:
        """Put an amended grant back on the profile. Composition only — the amendment itself was
        a lifecycle or factory decision made above."""
        return replace(
            profile,
            grants=tuple(grant if g.id == grant.id else g for g in profile.grants),
            updated_at=self._clock.now(),
        )

    # ── scheduled changes ───────────────────────────────────────────────────────

    def schedule_change(
        self,
        profile: EntitlementProfile,
        *,
        kind: EntitlementChangeKind,
        effective_at: float,
        entitlement_code: str = "",
        bundle_code: str = "",
        grant_id: str = "",
        value: EntitlementValue | None = None,
        source: GrantSource = GrantSource.PLAN,
        source_ref: str = "",
        reason: str = "",
        actor_id: str = "",
    ) -> EntitlementProfile:
        """Record an intention. **Nothing here fires it** — there is no scheduler in this
        domain, and the change takes effect only when a caller later dispatches the command."""
        change = ScheduledEntitlementChange(
            id=self._ids.new(PREFIX_ENTITLEMENT_CHANGE),
            kind=kind,
            effective_at=effective_at,
            entitlement_code=entitlement_code,
            bundle_code=bundle_code,
            grant_id=grant_id,
            value=value if value is not None else EntitlementValue(),
            source=source,
            source_ref=source_ref,
            reason=reason,
            requested_by=actor_id,
            requested_at=self._clock.now(),
        )
        updated = replace(
            profile,
            scheduled_changes=profile.scheduled_changes + (change,),
            updated_at=self._clock.now(),
        )
        self._fail_fast(
            self._validator.validate_schedule(updated.scheduled_changes, at=self._clock.now())
        )
        self._emit(
            BillingEventType.ENTITLEMENT_CHANGE_SCHEDULED,
            profile.org_id,
            profile.id,
            actor_id,
            change=change.id,
            kind=kind.value,
            effective_at=effective_at,
        )
        return updated

    # ── versions ────────────────────────────────────────────────────────────────

    def compose_set(
        self,
        profile: EntitlementProfile,
        definitions: Sequence[EntitlementDefinition],
        *,
        version: int,
        at: float | None = None,
        include_defaults: bool = False,
    ) -> EntitlementSet:
        """Resolve grants into the statement enforcing planes read. Deterministic: the same
        profile, definitions, and instant always produce an identical set."""
        return self._assembler.compose(
            profile,
            definitions,
            snapshot_id=self._ids.new(PREFIX_ENTITLEMENT_SET),
            version=version,
            at=at if at is not None else self._clock.now(),
            include_defaults=include_defaults,
        )

    def publish_version(
        self,
        profile: EntitlementProfile,
        entitlements: EntitlementSet,
        *,
        version: int,
        reason: str = "",
        actor_id: str = "",
    ) -> EntitlementVersion:
        """Freeze the decision into an immutable version.

        Validation runs **before** the version exists: once published it can never be corrected,
        only superseded. That is not strictness for its own sake — an enforcing plane may
        already have acted on it."""
        frozen = self._assembler.freeze(
            profile,
            entitlements,
            version_id=self._ids.new(PREFIX_ENTITLEMENT_VERSION),
            version=version,
            published_at=self._clock.now(),
            published_by=actor_id,
            reason=reason,
        )
        self._fail_fast(self._validator.validate_version(frozen))
        self._emit(
            BillingEventType.ENTITLEMENT_SNAPSHOT_PUBLISHED,
            profile.org_id,
            frozen.id,
            actor_id,
            account_id=profile.account_id,
            version=version,
            entitlements=frozen.entitlement_count,
        )
        return frozen

    def rollback_version(
        self,
        source: EntitlementVersion,
        *,
        version: int,
        reason: str = "",
        actor_id: str = "",
    ) -> EntitlementVersion:
        rolled = self._assembler.rollback(
            source,
            version_id=self._ids.new(PREFIX_ENTITLEMENT_VERSION),
            version=version,
            published_at=self._clock.now(),
            published_by=actor_id,
            reason=reason,
        )
        self._fail_fast(self._validator.validate_version(rolled))
        self._emit(
            BillingEventType.ENTITLEMENT_ROLLED_BACK,
            source.org_id,
            rolled.id,
            actor_id,
            account_id=source.account_id,
            version=version,
            rolled_back_from=source.version,
        )
        return rolled

    # ── facts + history ─────────────────────────────────────────────────────────

    def emit_profile_fact(
        self,
        profile: EntitlementProfile,
        *,
        event_type: BillingEventType,
        actor_id: str = "",
        **data,
    ) -> None:
        """Emit a `billing.*` fact about a profile-level change the caller already applied
        through a Foundation service (cancelling a scheduled change, for instance). Exists so
        every emission in this domain still goes through one place."""
        self._emit(event_type, profile.org_id, profile.id, actor_id, **data)

    def emit_transition(
        self,
        grant: CommercialGrant,
        *,
        event_type: BillingEventType,
        actor_id: str = "",
        **data,
    ) -> None:
        """Emit the `billing.*` fact for a lifecycle move the lifecycle service already applied.
        Kept here so every emission in this domain goes through one place — which is how
        "exactly one event per business fact" stays checkable."""
        self._emit(event_type, grant.org_id, grant.id, actor_id, **data)

    def activity(
        self,
        *,
        org_id: str,
        account_id: str,
        action: EntitlementAction,
        actor_id: str = "",
        entitlement_code: str = "",
        grant_id: str = "",
        from_state: str = "",
        to_state: str = "",
        version: int = 0,
        detail: str = "",
    ) -> EntitlementActivity:
        return EntitlementActivity(
            id=self._ids.new(PREFIX_ENTITLEMENT_ACTIVITY),
            org_id=org_id,
            account_id=account_id,
            action=action,
            actor_id=actor_id,
            at=self._clock.now(),
            entitlement_code=entitlement_code,
            grant_id=grant_id,
            from_state=from_state,
            to_state=to_state,
            version=version,
            detail=detail,
        )

    # ── internals ───────────────────────────────────────────────────────────────

    def _fail_fast(self, diagnostics) -> None:
        if diagnostics.has_errors:
            raise BillingValidationError(diagnostics)

    def _emit(
        self,
        event_type: BillingEventType,
        org_id: str,
        subject_id: str,
        actor_id: str,
        **data,
    ) -> None:
        self._emitter.emit(
            BillingEvent(
                id=self._ids.new("bev"),
                type=event_type,
                ts=self._clock.now(),
                org_id=org_id,
                subject_id=subject_id,
                actor_id=actor_id,
                data=dict(data),
            )
        )


# ── repository contracts (additive to the M9.2 seam) ────────────────────────────


class EntitlementDefinitionRepository(ABC):
    """The gate registry. Org-scoped by construction: every read filters on `org_id`, so a
    cross-tenant query is not expressible (§A11.2)."""

    @abstractmethod
    def add(self, definition: EntitlementDefinition) -> EntitlementDefinition: ...
    @abstractmethod
    def get(self, org_id: str, definition_id: str) -> EntitlementDefinition | None: ...
    @abstractmethod
    def by_code(self, org_id: str, code: str) -> EntitlementDefinition | None: ...
    @abstractmethod
    def all(self, org_id: str) -> tuple[EntitlementDefinition, ...]: ...
    @abstractmethod
    def replace(self, definition: EntitlementDefinition) -> EntitlementDefinition:
        """Whole-record replacement. Retirement is a field, not a deletion — a gate an enforcing
        plane may still be asking about must remain readable."""


class EntitlementBundleRepository(ABC):
    """Entitlement bundles."""

    @abstractmethod
    def add(self, bundle: EntitlementBundle) -> EntitlementBundle: ...
    @abstractmethod
    def get(self, org_id: str, bundle_id: str) -> EntitlementBundle | None: ...
    @abstractmethod
    def by_code(self, org_id: str, code: str) -> EntitlementBundle | None: ...
    @abstractmethod
    def all(self, org_id: str) -> tuple[EntitlementBundle, ...]: ...
    @abstractmethod
    def replace(self, bundle: EntitlementBundle) -> EntitlementBundle: ...


class EntitlementProfileRepository(ABC):
    """The per-account entitlement position, stored as one aggregate with its grants."""

    @abstractmethod
    def add(self, profile: EntitlementProfile) -> EntitlementProfile: ...
    @abstractmethod
    def get(self, org_id: str, profile_id: str) -> EntitlementProfile | None: ...
    @abstractmethod
    def for_account(self, org_id: str, account_id: str) -> EntitlementProfile | None: ...
    @abstractmethod
    def all(self, org_id: str) -> tuple[EntitlementProfile, ...]: ...
    @abstractmethod
    def replace(self, profile: EntitlementProfile) -> EntitlementProfile: ...


class EntitlementVersionRepository(ABC):
    """Append-only: published entitlement snapshots are immutable and remain readable forever.
    No update, no delete — there is no method to call."""

    @abstractmethod
    def publish(self, version: EntitlementVersion) -> EntitlementVersion: ...
    @abstractmethod
    def get(self, org_id: str, account_id: str, version: int) -> EntitlementVersion | None: ...
    @abstractmethod
    def latest(self, org_id: str, account_id: str) -> EntitlementVersion | None: ...
    @abstractmethod
    def history(
        self, org_id: str, account_id: str, limit: int = 50
    ) -> tuple[EntitlementVersion, ...]: ...
    @abstractmethod
    def next_version(self, org_id: str, account_id: str) -> int:
        """Monotonic and never reused — including across a rollback, which moves *forward* to
        restore an earlier decision."""


class EntitlementActivityRepository(ABC):
    """Append-only entitlement history."""

    @abstractmethod
    def append(self, activity: EntitlementActivity) -> EntitlementActivity: ...
    @abstractmethod
    def history(
        self, org_id: str, account_id: str = "", limit: int = 100
    ) -> tuple[EntitlementActivity, ...]:
        """Newest first. `account_id=""` spans the whole organization."""


__all__ = [
    "PREFIX_ENTITLEMENT_ACTIVITY",
    "PREFIX_ENTITLEMENT_BUNDLE",
    "PREFIX_ENTITLEMENT_CHANGE",
    "PREFIX_ENTITLEMENT_DEFINITION",
    "PREFIX_ENTITLEMENT_GRANT",
    "PREFIX_ENTITLEMENT_PROFILE",
    "PREFIX_ENTITLEMENT_SET",
    "PREFIX_ENTITLEMENT_VERSION",
    "EntitlementActivityRepository",
    "EntitlementBundleRepository",
    "EntitlementDefinitionRepository",
    "EntitlementFactory",
    "EntitlementProfileRepository",
    "EntitlementVersionRepository",
]
