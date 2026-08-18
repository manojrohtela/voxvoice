"""Subscription factories + repository contracts (M9.9).

`SubscriptionFactory` is the one place a subscription aggregate is constructed, following the
M9.2 factory discipline exactly: validate structurally, **fail fast** on error-severity findings
(`BillingValidationError`, §A2.6), compose an immutable aggregate from the injected id and clock
seams, and emit the corresponding `billing.*` fact through the injected emitter.

**Exactly one event per business fact.** A rejected command emits nothing at all — the fail-fast
check runs before any emission, so a validation failure leaves no trace on the fact stream. A
test asserts it.

No I/O, no persistence, no networking. Emission is the only effect and it is injected.

The repository interfaces below extend the M9.2 seam additively — same shape, same rules:
org-scoped reads by construction (§A11.2), and the two append-only stores (subscription versions,
subscription activity) deliberately declare **no update and no delete**. A version that could be
edited would not be a record of what was agreed.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Sequence
from dataclasses import replace

from .commercial_subscriptions import (
    AgreementItem,
    AgreementState,
    BillingCycle,
    CancellationPolicy,
    CommercialSubscription,
    DowngradePolicy,
    ItemKind,
    PausePolicy,
    RenewalPolicy,
    ScheduledChangeKind,
    ScheduledSubscriptionChange,
    SubscriptionAction,
    SubscriptionActivity,
    SubscriptionVersion,
    TrialPolicy,
    UpgradePolicy,
)
from .domain import BillingInterval
from .errors import BillingValidationError
from .events import BillingEvent, BillingEventType, EventEmitter, NullEventEmitter
from .money import Currency
from .seams import Clock, IdGenerator, SequentialIdGenerator, SystemClock
from .subscription import ChangeTiming
from .subscription_rules import SubscriptionAssembler, SubscriptionValidator

PREFIX_SUBSCRIPTION = "csb"
PREFIX_SUBSCRIPTION_ITEM = "sit"
PREFIX_SUBSCRIPTION_VERSION = "svr"
PREFIX_SUBSCRIPTION_ACTIVITY = "sac"
PREFIX_SCHEDULED_CHANGE = "sch"

#: The subscription-number shape: a stable prefix plus a zero-padded sequence. Like an account
#: number, it appears on invoices and in customer correspondence, so it is stable and typeable.
SUBSCRIPTION_NUMBER_PREFIX = "SUB"
SUBSCRIPTION_NUMBER_WIDTH = 6


def format_subscription_number(sequence: int, *, prefix: str = SUBSCRIPTION_NUMBER_PREFIX) -> str:
    """`SUB-000042`. Pure and deterministic."""
    return f"{prefix}-{sequence:0{SUBSCRIPTION_NUMBER_WIDTH}d}"


class SubscriptionNumberAllocator(ABC):
    """Allocates the next subscription number for an organization. Monotonic, never reused."""

    @abstractmethod
    def next_number(self, org_id: str) -> str: ...


class SubscriptionFactory:
    """Composes validated subscription aggregates. Stateless apart from its injected seams."""

    def __init__(
        self,
        *,
        ids: IdGenerator | None = None,
        clock: Clock | None = None,
        emitter: EventEmitter | None = None,
        validator: SubscriptionValidator | None = None,
        numbers: SubscriptionNumberAllocator | None = None,
    ) -> None:
        self._ids = ids or SequentialIdGenerator()
        self._clock = clock or SystemClock()
        self._emitter = emitter or NullEventEmitter()
        self._validator = validator or SubscriptionValidator()
        self._assembler = SubscriptionAssembler()
        self._numbers = numbers

    def _with_ids(self, items: Sequence[AgreementItem]) -> tuple[AgreementItem, ...]:
        """Mint an id for any item that arrived without one.

        Defect found at M9.9 and fixed here: items supplied alongside a subscription were stored
        with the caller's id, which for a transport-supplied item is empty — leaving the line
        un-addressable by every `/items/{item_id}` route. Construction is the factory's job, so
        the id is minted here rather than invented by a controller."""
        return tuple(
            item if item.id else replace(item, id=self._ids.new(PREFIX_SUBSCRIPTION_ITEM))
            for item in items
        )

    # ── the subscription ────────────────────────────────────────────────────────

    def create_subscription(
        self,
        *,
        org_id: str,
        account_id: str,
        catalog_version: int,
        pricing_version: int,
        currency: Currency,
        plan_code: str = "",
        subscription_number: str = "",
        interval: BillingInterval = BillingInterval.MONTHLY,
        anchor_day: int = 1,
        items: Sequence[AgreementItem] = (),
        trial: TrialPolicy | None = None,
        renewal: RenewalPolicy | None = None,
        pause: PausePolicy | None = None,
        cancellation: CancellationPolicy | None = None,
        upgrade: UpgradePolicy | None = None,
        downgrade: DowngradePolicy | None = None,
        contract_id: str = "",
        known_catalog_versions: frozenset[int] | None = None,
        known_pricing_versions: frozenset[int] | None = None,
        account_state: str | None = None,
        actor_id: str = "",
    ) -> CommercialSubscription:
        """Compose a **draft** subscription.

        Everything starts as a draft — an agreement nobody has signed cannot go live by
        accident. The three references are fixed here and never change again: moving onto newer
        commercial terms publishes a new *version*, it does not edit this one."""
        now = self._clock.now()
        subscription = CommercialSubscription(
            id=self._ids.new(PREFIX_SUBSCRIPTION),
            org_id=org_id,
            account_id=account_id,
            subscription_number=subscription_number or self._allocate(org_id),
            catalog_version=catalog_version,
            pricing_version=pricing_version,
            currency=currency,
            plan_code=plan_code,
            state=AgreementState.DRAFT,
            version=1,
            items=self._with_ids(items),
            cycle=BillingCycle(interval=interval, anchor_day=anchor_day),
            trial=trial or TrialPolicy(),
            renewal=renewal or RenewalPolicy(),
            pause=pause or PausePolicy(),
            cancellation=cancellation or CancellationPolicy(),
            upgrade=upgrade or UpgradePolicy(),
            downgrade=downgrade or DowngradePolicy(),
            contract_id=contract_id,
            created_at=now,
            updated_at=now,
        )
        self._fail_fast(
            self._validator.validate_subscription(
                subscription,
                known_catalog_versions=known_catalog_versions,
                known_pricing_versions=known_pricing_versions,
                account_state=account_state,
                at=now,
            )
        )
        self._emit(
            BillingEventType.SUBSCRIPTION_CREATED,
            org_id,
            subscription.id,
            actor_id,
            subscription_number=subscription.subscription_number,
            account_id=account_id,
            catalog_version=catalog_version,
            pricing_version=pricing_version,
        )
        return subscription

    # ── items ───────────────────────────────────────────────────────────────────

    def item(
        self,
        *,
        price_code: str,
        price_version: int = 1,
        kind: ItemKind = ItemKind.FLAT,
        quantity: int = 1,
        meter_code: str = "",
        included_units: int = 0,
        label: str = "",
    ) -> AgreementItem:
        """Compose one item. Carries no money — it names a price pin and a quantity."""
        return AgreementItem(
            id=self._ids.new(PREFIX_SUBSCRIPTION_ITEM),
            price_code=price_code,
            price_version=price_version,
            kind=kind,
            quantity=quantity,
            meter_code=meter_code,
            included_units=included_units,
            label=label,
            added_at=self._clock.now(),
        )

    def add_item(
        self, subscription: CommercialSubscription, item: AgreementItem, *, actor_id: str = ""
    ) -> CommercialSubscription:
        updated = replace(
            subscription, items=subscription.items + (item,), updated_at=self._clock.now()
        )
        self._fail_fast(self._validator.validate_items(updated.items))
        self._emit(
            BillingEventType.SUBSCRIPTION_ITEM_ADDED,
            subscription.org_id,
            subscription.id,
            actor_id,
            item=item.id,
            price=item.pin,
            kind=item.kind.value,
        )
        return updated

    def update_item(
        self,
        subscription: CommercialSubscription,
        item_id: str,
        *,
        actor_id: str = "",
        **changes,
    ) -> CommercialSubscription:
        existing = subscription.item(item_id)
        if existing is None:
            raise _missing_item(item_id)
        amended = replace(existing, **{k: v for k, v in changes.items() if v is not None})
        updated = replace(
            subscription,
            items=tuple(amended if i.id == item_id else i for i in subscription.items),
            updated_at=self._clock.now(),
        )
        self._fail_fast(self._validator.validate_items(updated.items))
        self._emit(
            BillingEventType.SUBSCRIPTION_ITEM_UPDATED,
            subscription.org_id,
            subscription.id,
            actor_id,
            item=item_id,
            fields=sorted(k for k, v in changes.items() if v is not None),
        )
        return updated

    def remove_item(
        self, subscription: CommercialSubscription, item_id: str, *, actor_id: str = ""
    ) -> CommercialSubscription:
        if subscription.item(item_id) is None:
            raise _missing_item(item_id)
        updated = replace(
            subscription,
            items=tuple(i for i in subscription.items if i.id != item_id),
            updated_at=self._clock.now(),
        )
        self._emit(
            BillingEventType.SUBSCRIPTION_ITEM_REMOVED,
            subscription.org_id,
            subscription.id,
            actor_id,
            item=item_id,
        )
        return updated

    # ── scheduled changes ───────────────────────────────────────────────────────

    def schedule_change(
        self,
        subscription: CommercialSubscription,
        *,
        kind: ScheduledChangeKind,
        effective_at: float,
        target_catalog_version: int | None = None,
        target_pricing_version: int | None = None,
        target_plan_code: str = "",
        items: Sequence[AgreementItem] = (),
        timing: ChangeTiming = ChangeTiming.PERIOD_END,
        reason: str = "",
        actor_id: str = "",
    ) -> CommercialSubscription:
        """Record an intention. **Nothing here fires it** — there is no scheduler in this
        domain, and the change takes effect only when a caller later dispatches the command."""
        change = ScheduledSubscriptionChange(
            id=self._ids.new(PREFIX_SCHEDULED_CHANGE),
            kind=kind,
            effective_at=effective_at,
            target_catalog_version=target_catalog_version,
            target_pricing_version=target_pricing_version,
            target_plan_code=target_plan_code,
            items=self._with_ids(items),
            timing=timing,
            reason=reason,
            requested_by=actor_id,
            requested_at=self._clock.now(),
        )
        updated = replace(
            subscription,
            scheduled_changes=subscription.scheduled_changes + (change,),
            updated_at=self._clock.now(),
        )
        self._fail_fast(
            self._validator.validate_schedule(updated.scheduled_changes, at=self._clock.now())
        )
        self._emit(
            BillingEventType.SUBSCRIPTION_CHANGE_SCHEDULED,
            subscription.org_id,
            subscription.id,
            actor_id,
            change=change.id,
            kind=kind.value,
            effective_at=effective_at,
        )
        return updated

    # ── policies ────────────────────────────────────────────────────────────────

    def set_renewal_policy(
        self, subscription: CommercialSubscription, renewal: RenewalPolicy, *, actor_id: str = ""
    ) -> CommercialSubscription:
        self._fail_fast(self._validator.validate_renewal(renewal))
        updated = replace(subscription, renewal=renewal, updated_at=self._clock.now())
        self._emit(
            BillingEventType.SUBSCRIPTION_RENEWAL_POLICY_CHANGED,
            subscription.org_id,
            subscription.id,
            actor_id,
            mode=renewal.mode.value,
            terms=renewal.terms,
        )
        return updated

    # ── versions ────────────────────────────────────────────────────────────────

    def publish_version(
        self,
        subscription: CommercialSubscription,
        *,
        version: int,
        reason: str = "",
        actor_id: str = "",
    ) -> SubscriptionVersion:
        """Freeze the agreement into an immutable version.

        Validation runs **before** the version exists: once published it can never be
        corrected, only superseded."""
        frozen = self._assembler.freeze(
            subscription,
            version_id=self._ids.new(PREFIX_SUBSCRIPTION_VERSION),
            version=version,
            published_at=self._clock.now(),
            published_by=actor_id,
            reason=reason,
        )
        self._fail_fast(self._validator.validate_version(frozen))
        self._emit(
            BillingEventType.SUBSCRIPTION_VERSION_PUBLISHED,
            subscription.org_id,
            frozen.id,
            actor_id,
            subscription_id=subscription.id,
            version=version,
            items=frozen.item_count,
        )
        return frozen

    def rollback_version(
        self,
        source: SubscriptionVersion,
        *,
        version: int,
        reason: str = "",
        actor_id: str = "",
    ) -> SubscriptionVersion:
        rolled = self._assembler.rollback(
            source,
            version_id=self._ids.new(PREFIX_SUBSCRIPTION_VERSION),
            version=version,
            published_at=self._clock.now(),
            published_by=actor_id,
            reason=reason,
        )
        self._fail_fast(self._validator.validate_version(rolled))
        self._emit(
            BillingEventType.SUBSCRIPTION_ROLLED_BACK,
            source.org_id,
            rolled.id,
            actor_id,
            subscription_id=source.subscription_id,
            version=version,
            rolled_back_from=source.version,
        )
        return rolled

    # ── facts + history ─────────────────────────────────────────────────────────

    def emit_transition(
        self,
        subscription: CommercialSubscription,
        *,
        event_type: BillingEventType,
        actor_id: str = "",
        **data,
    ) -> None:
        """Emit the `billing.*` fact for a lifecycle move the lifecycle service already applied.
        Kept here so every emission in this domain goes through one place — which is how
        "exactly one event per business fact" stays checkable."""
        self._emit(event_type, subscription.org_id, subscription.id, actor_id, **data)

    def activity(
        self,
        *,
        org_id: str,
        subscription_id: str,
        action: SubscriptionAction,
        actor_id: str = "",
        from_state: str = "",
        to_state: str = "",
        version: int = 1,
        detail: str = "",
    ) -> SubscriptionActivity:
        return SubscriptionActivity(
            id=self._ids.new(PREFIX_SUBSCRIPTION_ACTIVITY),
            org_id=org_id,
            subscription_id=subscription_id,
            action=action,
            actor_id=actor_id,
            at=self._clock.now(),
            from_state=from_state,
            to_state=to_state,
            version=version,
            detail=detail,
        )

    # ── internals ───────────────────────────────────────────────────────────────

    def _allocate(self, org_id: str) -> str:
        if self._numbers is not None:
            return self._numbers.next_number(org_id)
        minted = self._ids.new("seq").rsplit("_", 1)[-1]
        return format_subscription_number(int(minted) if minted.isdigit() else 1)

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


def _missing_item(item_id: str):
    from .errors import BillingNotFound

    return BillingNotFound(f"subscription item {item_id!r} not found")


# ── repository contracts (additive to the M9.2 seam) ────────────────────────────


class CommercialSubscriptionRepository(ABC):
    """The subscription book. Org-scoped by construction: every read filters on `org_id`, so a
    cross-tenant query is not expressible (§A11.2)."""

    @abstractmethod
    def add(self, subscription: CommercialSubscription) -> CommercialSubscription: ...
    @abstractmethod
    def get(self, org_id: str, subscription_id: str) -> CommercialSubscription | None: ...
    @abstractmethod
    def by_number(self, org_id: str, number: str) -> CommercialSubscription | None: ...
    @abstractmethod
    def all(self, org_id: str) -> tuple[CommercialSubscription, ...]: ...
    @abstractmethod
    def for_account(self, org_id: str, account_id: str) -> tuple[CommercialSubscription, ...]: ...
    @abstractmethod
    def replace(self, subscription: CommercialSubscription) -> CommercialSubscription:
        """Whole-aggregate replacement. An agreement is mutable while it is live; closing it is
        what makes the record read-only, and that is a lifecycle decision, not a storage one."""


class SubscriptionVersionRepository(ABC):
    """Append-only: published versions are immutable and remain readable forever. No update, no
    delete — there is no method to call."""

    @abstractmethod
    def publish(self, version: SubscriptionVersion) -> SubscriptionVersion: ...
    @abstractmethod
    def get(
        self, org_id: str, subscription_id: str, version: int
    ) -> SubscriptionVersion | None: ...
    @abstractmethod
    def latest(self, org_id: str, subscription_id: str) -> SubscriptionVersion | None: ...
    @abstractmethod
    def history(
        self, org_id: str, subscription_id: str, limit: int = 50
    ) -> tuple[SubscriptionVersion, ...]: ...
    @abstractmethod
    def next_version(self, org_id: str, subscription_id: str) -> int:
        """Monotonic and never reused — including across a rollback, which moves *forward* to
        restore earlier terms."""


class SubscriptionActivityRepository(ABC):
    """Append-only subscription history."""

    @abstractmethod
    def append(self, activity: SubscriptionActivity) -> SubscriptionActivity: ...
    @abstractmethod
    def history(
        self, org_id: str, subscription_id: str = "", limit: int = 100
    ) -> tuple[SubscriptionActivity, ...]:
        """Newest first. `subscription_id=""` spans the whole organization."""


__all__ = [
    "PREFIX_SCHEDULED_CHANGE",
    "PREFIX_SUBSCRIPTION",
    "PREFIX_SUBSCRIPTION_ACTIVITY",
    "PREFIX_SUBSCRIPTION_ITEM",
    "PREFIX_SUBSCRIPTION_VERSION",
    "SUBSCRIPTION_NUMBER_PREFIX",
    "CommercialSubscriptionRepository",
    "SubscriptionActivityRepository",
    "SubscriptionFactory",
    "SubscriptionNumberAllocator",
    "SubscriptionVersionRepository",
    "format_subscription_number",
]
