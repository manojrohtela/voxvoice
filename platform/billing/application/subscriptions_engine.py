"""Subscription Engine application layer (M9.9) — commands, queries, use cases, mapping.

Additive to M9.3/M9.6/M9.7/M9.8: the buses, Unit of Work, authorization orchestrator,
application-event vocabulary, and dispatch discipline are reused exactly as they are. This
module adds 19 commands, 6 queries, their use cases, and the view DTOs — and nothing else.

The four beats are unchanged: **authorize → open a transaction → coordinate the Foundation →
stage an event.** Every subscription rule — pin validity, item consistency, lifecycle legality,
cycle advancement, trial expiry, schedule conflicts, version freezing, search filtering — is
Foundation behaviour these methods *call*. There is no rule here, and a test greps for the ones
that would be tempting to inline (including, especially, any `Money` at all).

Names differ from the frozen M9.3 surface where they must: M9.3 owns `StartSubscription`,
`TransitionSubscription`, `ChangeSubscriptionPlan`, `GetSubscription`, and `ListSubscriptions`,
which drive the M9.2 `Subscription`. Everything here is named distinctly — the same call M9.6
made for `CreateCatalogPlan`, M9.7 for `PublishPricingBook`, and M9.8 for
`CloseCommercialAccount`.

As in the three milestones before it, **every command also appends a `SubscriptionActivity`**,
inside the same transaction as the change it describes; and a **rejected command emits no
business event**, because the Foundation fails fast before any emission.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace

from billing.foundation import (
    AgreementItem,
    AgreementTrigger,
    BillingInterval,
    CancellationPolicy,
    CancellationTiming,
    ChangeTiming,
    CommercialSubscription,
    Currency,
    DowngradePolicy,
    ItemKind,
    PausePolicy,
    RenewalPolicy,
    ScheduledChangeKind,
    SubscriptionAction,
    SubscriptionSearchQuery,
    SubscriptionVersion,
    TrialPolicy,
    UpgradePolicy,
)
from billing.foundation import BillingNotFound as _NotFound

from .authorization import AuthorizationOrchestrator
from .authorization import BillingPermission as P
from .commands import _Command
from .events import PREFIX_APP_EVENT, ApplicationEvent, ApplicationEventType
from .queries import _Query
from .unit_of_work import UnitOfWorkFactory

# ── commands ────────────────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class CreateSubscription(_Command):
    """The three references are fixed here and never change again."""

    account_id: str = ""
    catalog_version: int = 0
    pricing_version: int = 0
    currency: Currency = Currency.INR
    plan_code: str = ""
    subscription_number: str = ""
    interval: BillingInterval = BillingInterval.MONTHLY
    anchor_day: int = 1
    items: tuple[AgreementItem, ...] = ()
    trial: TrialPolicy = field(default_factory=TrialPolicy)
    renewal: RenewalPolicy = field(default_factory=RenewalPolicy)
    pause: PausePolicy = field(default_factory=PausePolicy)
    cancellation: CancellationPolicy = field(default_factory=CancellationPolicy)
    upgrade: UpgradePolicy = field(default_factory=UpgradePolicy)
    downgrade: DowngradePolicy = field(default_factory=DowngradePolicy)
    contract_id: str = ""


@dataclass(frozen=True, slots=True)
class ActivateSubscription(_Command):
    subscription_id: str = ""


@dataclass(frozen=True, slots=True)
class StartSubscriptionTrial(_Command):
    subscription_id: str = ""
    length_days: int = 0


@dataclass(frozen=True, slots=True)
class ConvertSubscriptionTrial(_Command):
    subscription_id: str = ""


@dataclass(frozen=True, slots=True)
class ExpireSubscriptionTrial(_Command):
    subscription_id: str = ""


@dataclass(frozen=True, slots=True)
class MarkSubscriptionPastDue(_Command):
    """Recorded here because it is a *state of the agreement*. Whether a payment failed is the
    Payment domain's fact; this command only reflects it onto the subscription."""

    subscription_id: str = ""
    reason: str = ""


@dataclass(frozen=True, slots=True)
class RecoverSubscription(_Command):
    subscription_id: str = ""


@dataclass(frozen=True, slots=True)
class SuspendSubscription(_Command):
    subscription_id: str = ""
    reason: str = ""


@dataclass(frozen=True, slots=True)
class ReactivateSubscription(_Command):
    subscription_id: str = ""


@dataclass(frozen=True, slots=True)
class PauseSubscription(_Command):
    subscription_id: str = ""
    reason: str = ""


@dataclass(frozen=True, slots=True)
class ResumeSubscription(_Command):
    subscription_id: str = ""


@dataclass(frozen=True, slots=True)
class CancelSubscription(_Command):
    subscription_id: str = ""
    timing: CancellationTiming = CancellationTiming.PERIOD_END
    reason: str = ""
    reason_code: str = ""


@dataclass(frozen=True, slots=True)
class ExpireSubscription(_Command):
    subscription_id: str = ""


@dataclass(frozen=True, slots=True)
class ArchiveSubscription(_Command):
    subscription_id: str = ""


@dataclass(frozen=True, slots=True)
class AddSubscriptionItem(_Command):
    subscription_id: str = ""
    price_code: str = ""
    price_version: int = 1
    kind: ItemKind = ItemKind.FLAT
    quantity: int = 1
    meter_code: str = ""
    included_units: int = 0
    label: str = ""


@dataclass(frozen=True, slots=True)
class UpdateSubscriptionItem(_Command):
    subscription_id: str = ""
    item_id: str = ""
    quantity: int | None = None
    included_units: int | None = None
    label: str | None = None


@dataclass(frozen=True, slots=True)
class RemoveSubscriptionItem(_Command):
    subscription_id: str = ""
    item_id: str = ""


@dataclass(frozen=True, slots=True)
class ScheduleSubscriptionChange(_Command):
    """Intent only. Nothing in this plane fires it — a caller dispatches the real command when
    the time comes, and the FSM validates it then."""

    subscription_id: str = ""
    kind: ScheduledChangeKind = ScheduledChangeKind.UPGRADE
    effective_at: float = 0.0
    target_catalog_version: int | None = None
    target_pricing_version: int | None = None
    target_plan_code: str = ""
    items: tuple[AgreementItem, ...] = ()
    timing: ChangeTiming = ChangeTiming.PERIOD_END
    reason: str = ""


@dataclass(frozen=True, slots=True)
class CancelScheduledChange(_Command):
    subscription_id: str = ""
    change_id: str = ""


@dataclass(frozen=True, slots=True)
class RenewSubscription(_Command):
    """Advances the billing cycle of an active subscription. Not a state change — and not a
    scheduler: a caller decides when this runs."""

    subscription_id: str = ""


@dataclass(frozen=True, slots=True)
class SetRenewalPolicy(_Command):
    subscription_id: str = ""
    renewal: RenewalPolicy = field(default_factory=RenewalPolicy)


@dataclass(frozen=True, slots=True)
class PublishSubscriptionVersion(_Command):
    """Freeze the current terms into an immutable version."""

    subscription_id: str = ""
    reason: str = ""


@dataclass(frozen=True, slots=True)
class RollbackSubscription(_Command):
    """Publish a **new** version carrying earlier terms. History is never rewritten."""

    subscription_id: str = ""
    to_version: int = 1
    reason: str = ""


ALL_SUBSCRIPTION_COMMANDS = (
    CreateSubscription,
    ActivateSubscription,
    StartSubscriptionTrial,
    ConvertSubscriptionTrial,
    ExpireSubscriptionTrial,
    MarkSubscriptionPastDue,
    RecoverSubscription,
    SuspendSubscription,
    ReactivateSubscription,
    PauseSubscription,
    ResumeSubscription,
    CancelSubscription,
    ExpireSubscription,
    ArchiveSubscription,
    AddSubscriptionItem,
    UpdateSubscriptionItem,
    RemoveSubscriptionItem,
    ScheduleSubscriptionChange,
    CancelScheduledChange,
    RenewSubscription,
    SetRenewalPolicy,
    PublishSubscriptionVersion,
    RollbackSubscription,
)


# ── queries ─────────────────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class GetCommercialSubscription(_Query):
    """Named distinctly from M9.3's `GetSubscription`, which reads the M9.2 aggregate."""

    subscription_id: str = ""
    subscription_number: str = ""


@dataclass(frozen=True, slots=True)
class SearchSubscriptions(_Query):
    query: SubscriptionSearchQuery = field(default_factory=SubscriptionSearchQuery)


@dataclass(frozen=True, slots=True)
class SubscriptionTimeline(_Query):
    """The agreement's ordered activity — what happened, in order."""

    subscription_id: str = ""
    limit: int = 100


@dataclass(frozen=True, slots=True)
class SubscriptionVersionHistory(_Query):
    """The agreement's version line — what the terms were, in order."""

    subscription_id: str = ""
    limit: int = 50


@dataclass(frozen=True, slots=True)
class GetSubscriptionSnapshot(_Query):
    """What agreement existed at an instant. `at=None` ⇒ now, as the caller sees it."""

    subscription_id: str = ""
    at: float | None = None
    version: int | None = None


@dataclass(frozen=True, slots=True)
class ListScheduledChanges(_Query):
    subscription_id: str = ""
    due_at: float | None = None  # set ⇒ only changes due by then


ALL_SUBSCRIPTION_QUERIES = (
    GetCommercialSubscription,
    SearchSubscriptions,
    SubscriptionTimeline,
    SubscriptionVersionHistory,
    GetSubscriptionSnapshot,
    ListScheduledChanges,
)


# ── view DTOs ───────────────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class PeriodView:
    start: float
    end: float


@dataclass(frozen=True, slots=True)
class CycleView:
    interval: str
    anchor_day: int
    cycle_number: int
    current_period: PeriodView | None


@dataclass(frozen=True, slots=True)
class ItemView:
    id: str
    price_code: str
    price_version: int
    pin: str
    kind: str
    quantity: int
    meter_code: str
    included_units: int
    label: str
    is_metered: bool
    is_seat_based: bool


@dataclass(frozen=True, slots=True)
class TrialView:
    enabled: bool
    length_days: int
    ends_at: float | None
    converts_automatically: bool
    requires_payment_method: bool
    extension_allowed: bool


@dataclass(frozen=True, slots=True)
class RenewalView:
    mode: str
    notice_days: int
    terms: int | None
    terms_used: int
    next_renewal_at: float | None
    renews: bool
    is_automatic: bool


@dataclass(frozen=True, slots=True)
class PauseView:
    max_pause_days: int
    pauses_per_term: int
    pauses_used: int
    pauses_remaining: int
    entitlements_retained: bool
    paused_at: float | None
    resume_at: float | None


@dataclass(frozen=True, slots=True)
class CancellationView:
    default_timing: str
    notice_days: int
    refund_eligible: bool
    reason: str
    reason_code: str
    requested_at: float | None
    effective_at: float | None
    is_requested: bool


@dataclass(frozen=True, slots=True)
class ChangePolicyView:
    timing: str
    proration: str
    allow_mid_period: bool
    requires_approval: bool


@dataclass(frozen=True, slots=True)
class ScheduledChangeView:
    id: str
    kind: str
    status: str
    effective_at: float
    target_catalog_version: int | None
    target_pricing_version: int | None
    target_plan_code: str
    item_count: int
    timing: str
    reason: str
    requested_by: str
    requested_at: float
    applied_at: float | None
    is_commercial: bool


@dataclass(frozen=True, slots=True)
class CommercialSubscriptionView:
    id: str
    subscription_number: str
    account_id: str
    catalog_version: int
    pricing_version: int
    currency: str
    plan_code: str
    state: str
    version: int
    items: tuple[ItemView, ...]
    cycle: CycleView
    trial: TrialView
    renewal: RenewalView
    pause: PauseView
    cancellation: CancellationView
    upgrade: ChangePolicyView
    downgrade: ChangePolicyView
    scheduled_changes: tuple[ScheduledChangeView, ...]
    contract_id: str
    seat_count: int
    metered_codes: tuple[str, ...]
    price_pins: tuple[str, ...]
    is_live: bool
    is_impaired: bool
    is_closed: bool
    is_hybrid: bool
    is_editable: bool
    allowed_triggers: tuple[str, ...]
    created_at: float
    updated_at: float
    started_at: float | None
    cancelled_at: float | None
    ended_at: float | None
    archived_at: float | None


@dataclass(frozen=True, slots=True)
class SubscriptionVersionView:
    id: str
    subscription_id: str
    version: int
    published_at: float
    published_by: str
    reason: str
    rolled_back_from: int | None
    item_count: int
    catalog_version: int | None
    pricing_version: int | None
    state: str


@dataclass(frozen=True, slots=True)
class SubscriptionActivityView:
    id: str
    subscription_id: str
    action: str
    actor_id: str
    at: float
    from_state: str
    to_state: str
    version: int
    detail: str


@dataclass(frozen=True, slots=True)
class SubscriptionSnapshotView:
    subscription_id: str
    at: float
    version: int
    catalog_version: int
    pricing_version: int
    account_id: str
    state: str
    items: tuple[ItemView, ...]
    seat_count: int
    metered_codes: tuple[str, ...]
    pending_change_count: int
    in_period: bool
    is_reproducible: bool


@dataclass(frozen=True, slots=True)
class SubscriptionSearchView:
    subscriptions: tuple[CommercialSubscriptionView, ...]
    total: int


def _item_view(item: AgreementItem) -> ItemView:
    return ItemView(
        id=item.id,
        price_code=item.price_code,
        price_version=item.price_version,
        pin=item.pin,
        kind=item.kind.value,
        quantity=item.quantity,
        meter_code=item.meter_code,
        included_units=item.included_units,
        label=item.label,
        is_metered=item.is_metered,
        is_seat_based=item.is_seat_based,
    )


def _cycle_view(cycle) -> CycleView:
    period = cycle.current_period
    return CycleView(
        interval=cycle.interval.value,
        anchor_day=cycle.anchor_day,
        cycle_number=cycle.cycle_number,
        current_period=None if period is None else PeriodView(start=period.start, end=period.end),
    )


def _change_view(change) -> ScheduledChangeView:
    return ScheduledChangeView(
        id=change.id,
        kind=change.kind.value,
        status=change.status.value,
        effective_at=change.effective_at,
        target_catalog_version=change.target_catalog_version,
        target_pricing_version=change.target_pricing_version,
        target_plan_code=change.target_plan_code,
        item_count=len(change.items),
        timing=change.timing.value,
        reason=change.reason,
        requested_by=change.requested_by,
        requested_at=change.requested_at,
        applied_at=change.applied_at,
        is_commercial=change.is_commercial,
    )


def subscription_view(
    subscription: CommercialSubscription, allowed: tuple[AgreementTrigger, ...] = ()
) -> CommercialSubscriptionView:
    return CommercialSubscriptionView(
        id=subscription.id,
        subscription_number=subscription.subscription_number,
        account_id=subscription.account_id,
        catalog_version=subscription.catalog_version,
        pricing_version=subscription.pricing_version,
        currency=subscription.currency.value,
        plan_code=subscription.plan_code,
        state=subscription.state.value,
        version=subscription.version,
        items=tuple(_item_view(i) for i in subscription.items),
        cycle=_cycle_view(subscription.cycle),
        trial=TrialView(
            enabled=subscription.trial.enabled,
            length_days=subscription.trial.length_days,
            ends_at=subscription.trial.ends_at,
            converts_automatically=subscription.trial.converts_automatically,
            requires_payment_method=subscription.trial.requires_payment_method,
            extension_allowed=subscription.trial.extension_allowed,
        ),
        renewal=RenewalView(
            mode=subscription.renewal.mode.value,
            notice_days=subscription.renewal.notice_days,
            terms=subscription.renewal.terms,
            terms_used=subscription.renewal.terms_used,
            next_renewal_at=subscription.renewal.next_renewal_at,
            renews=subscription.renewal.renews,
            is_automatic=subscription.renewal.is_automatic,
        ),
        pause=PauseView(
            max_pause_days=subscription.pause.max_pause_days,
            pauses_per_term=subscription.pause.pauses_per_term,
            pauses_used=subscription.pause.pauses_used,
            pauses_remaining=subscription.pause.pauses_remaining,
            entitlements_retained=subscription.pause.entitlements_retained,
            paused_at=subscription.pause.paused_at,
            resume_at=subscription.pause.resume_at,
        ),
        cancellation=CancellationView(
            default_timing=subscription.cancellation.default_timing.value,
            notice_days=subscription.cancellation.notice_days,
            refund_eligible=subscription.cancellation.refund_eligible,
            reason=subscription.cancellation.reason,
            reason_code=subscription.cancellation.reason_code,
            requested_at=subscription.cancellation.requested_at,
            effective_at=subscription.cancellation.effective_at,
            is_requested=subscription.cancellation.is_requested,
        ),
        upgrade=ChangePolicyView(
            timing=subscription.upgrade.timing.value,
            proration=subscription.upgrade.proration.value,
            allow_mid_period=subscription.upgrade.allow_mid_period,
            requires_approval=subscription.upgrade.requires_approval,
        ),
        downgrade=ChangePolicyView(
            timing=subscription.downgrade.timing.value,
            proration=subscription.downgrade.proration.value,
            allow_mid_period=subscription.downgrade.allow_mid_period,
            requires_approval=subscription.downgrade.requires_approval,
        ),
        scheduled_changes=tuple(_change_view(c) for c in subscription.scheduled_changes),
        contract_id=subscription.contract_id,
        seat_count=subscription.seat_count,
        metered_codes=subscription.metered_codes,
        price_pins=subscription.price_pins,
        is_live=subscription.is_live,
        is_impaired=subscription.is_impaired,
        is_closed=subscription.is_closed,
        is_hybrid=subscription.is_hybrid,
        is_editable=subscription.is_editable,
        allowed_triggers=tuple(t.value for t in allowed),
        created_at=subscription.created_at,
        updated_at=subscription.updated_at,
        started_at=subscription.started_at,
        cancelled_at=subscription.cancelled_at,
        ended_at=subscription.ended_at,
        archived_at=subscription.archived_at,
    )


def subscription_version_view(version: SubscriptionVersion) -> SubscriptionVersionView:
    embedded = version.subscription
    return SubscriptionVersionView(
        id=version.id,
        subscription_id=version.subscription_id,
        version=version.version,
        published_at=version.published_at,
        published_by=version.published_by,
        reason=version.reason,
        rolled_back_from=version.rolled_back_from,
        item_count=version.item_count,
        catalog_version=embedded.catalog_version if embedded else None,
        pricing_version=embedded.pricing_version if embedded else None,
        state=embedded.state.value if embedded else "",
    )


def subscription_activity_view(activity) -> SubscriptionActivityView:
    return SubscriptionActivityView(
        id=activity.id,
        subscription_id=activity.subscription_id,
        action=activity.action.value,
        actor_id=activity.actor_id,
        at=activity.at,
        from_state=activity.from_state,
        to_state=activity.to_state,
        version=activity.version,
        detail=activity.detail,
    )


def snapshot_view(snapshot) -> SubscriptionSnapshotView:
    return SubscriptionSnapshotView(
        subscription_id=snapshot.subscription.id,
        at=snapshot.at,
        version=snapshot.version,
        catalog_version=snapshot.catalog_version,
        pricing_version=snapshot.pricing_version,
        account_id=snapshot.account_id,
        state=snapshot.state.value,
        items=tuple(_item_view(i) for i in snapshot.items),
        seat_count=snapshot.seat_count,
        metered_codes=snapshot.metered_codes,
        pending_change_count=snapshot.pending_change_count,
        in_period=snapshot.in_period,
        is_reproducible=snapshot.is_reproducible,
    )


# ── use cases ───────────────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class SubscriptionRepositories:
    """The subscription seams, injected. Interfaces only — implementations in persistence."""

    subscriptions: object
    versions: object
    activity: object
    numbers: object


#: Which `billing.*` fact each lifecycle action publishes. One entry per business fact — the
#: mapping is a table so "exactly one event per fact" is checkable rather than asserted in prose.
def _transition_events() -> dict:
    from billing.foundation import BillingEventType as E

    return {
        SubscriptionAction.ACTIVATED: E.SUBSCRIPTION_ACTIVATED,
        SubscriptionAction.TRIAL_STARTED: E.SUBSCRIPTION_TRIAL_STARTED,
        SubscriptionAction.TRIAL_CONVERTED: E.SUBSCRIPTION_TRIAL_CONVERTED,
        SubscriptionAction.TRIAL_EXPIRED: E.SUBSCRIPTION_TRIAL_EXPIRED,
        SubscriptionAction.PAYMENT_FAILED: E.SUBSCRIPTION_PAST_DUE,
        SubscriptionAction.PAYMENT_RECOVERED: E.SUBSCRIPTION_ACTIVATED,
        SubscriptionAction.SUSPENDED: E.SUBSCRIPTION_SUSPENDED,
        SubscriptionAction.REACTIVATED: E.SUBSCRIPTION_REACTIVATED,
        SubscriptionAction.PAUSED: E.SUBSCRIPTION_PAUSED,
        SubscriptionAction.RESUMED: E.SUBSCRIPTION_RESUMED,
        SubscriptionAction.CANCELLED: E.SUBSCRIPTION_CANCELLED,
        SubscriptionAction.EXPIRED: E.SUBSCRIPTION_EXPIRED,
        SubscriptionAction.ARCHIVED: E.SUBSCRIPTION_ARCHIVED,
        SubscriptionAction.RENEWED: E.SUBSCRIPTION_RENEWED,
    }


_TRANSITION_EVENTS = _transition_events()


class SubscriptionCommandUseCases:
    """Orchestration for the Subscription Engine. Coordinates; decides nothing.

    Not one method here computes an amount, advances a period by hand, or decides a lifecycle
    edge. Cycles come from `BillingCycleService`, legality from the FSM, and money from nowhere
    at all — a subscription records what was agreed, not what it costs."""

    def __init__(
        self,
        *,
        context,
        subscription_repos: SubscriptionRepositories,
        factory,
        lifecycle,
        cycles,
        trials,
        schedules,
        assembler,
        authz: AuthorizationOrchestrator,
        uow_factory: UnitOfWorkFactory,
        ids,
        clock,
    ) -> None:
        self._ctx = context
        self._repos = subscription_repos
        self._factory = factory
        self._lifecycle = lifecycle
        self._cycles = cycles
        self._trials = trials
        self._schedules = schedules
        self._assembler = assembler
        self._authz = authz
        self._uow = uow_factory
        self._ids = ids
        self._clock = clock

    # ── creation ────────────────────────────────────────────────────────────────

    def create(self, command: CreateSubscription) -> CommercialSubscription:
        self._authz.require(command.actor_id, command.org_id, P.BILLING_MANAGE)
        with self._uow.new() as uow:
            subscription = self._factory.create_subscription(
                org_id=command.org_id,
                account_id=command.account_id,
                catalog_version=command.catalog_version,
                pricing_version=command.pricing_version,
                currency=command.currency,
                plan_code=command.plan_code,
                subscription_number=command.subscription_number,
                interval=command.interval,
                anchor_day=command.anchor_day,
                items=command.items,
                trial=command.trial,
                renewal=command.renewal,
                pause=command.pause,
                cancellation=command.cancellation,
                upgrade=command.upgrade,
                downgrade=command.downgrade,
                contract_id=command.contract_id,
                known_catalog_versions=self._known_catalog_versions(command.org_id),
                known_pricing_versions=self._known_pricing_versions(command.org_id),
                account_state=self._account_state(command.org_id, command.account_id),
                actor_id=command.actor_id,
            )
            stored = self._repos.subscriptions.add(subscription)
            self._record(
                command,
                stored.id,
                SubscriptionAction.CREATED,
                to_state=stored.state.value,
                version=stored.version,
            )
            uow.stage(
                self._event(
                    command,
                    stored.id,
                    subscription_number=stored.subscription_number,
                    catalog_version=stored.catalog_version,
                    pricing_version=stored.pricing_version,
                )
            )
            return stored

    # ── lifecycle ───────────────────────────────────────────────────────────────

    def activate(self, command: ActivateSubscription) -> CommercialSubscription:
        """Activation opens the first billing period. Idempotent in the cycle service, so an
        agreement that already has one keeps its billing date."""
        return self._transition(
            command,
            AgreementTrigger.ACTIVATE,
            SubscriptionAction.ACTIVATED,
            open_period=True,
        )

    def start_trial(self, command: StartSubscriptionTrial) -> CommercialSubscription:
        self._authz.require(command.actor_id, command.org_id, P.BILLING_MANAGE)
        with self._uow.new() as uow:
            subscription = self._subscription(command.org_id, command.subscription_id)
            now = self._clock.now()
            trial = self._trials.start(
                subscription, at=now, length_days=command.length_days or None
            )
            moved = self._lifecycle.apply(
                replace(subscription, trial=trial), AgreementTrigger.START_TRIAL, at=now
            )
            return self._commit_transition(
                command, uow, subscription, moved, SubscriptionAction.TRIAL_STARTED
            )

    def convert_trial(self, command: ConvertSubscriptionTrial) -> CommercialSubscription:
        self._authz.require(command.actor_id, command.org_id, P.BILLING_MANAGE)
        with self._uow.new() as uow:
            subscription = self._subscription(command.org_id, command.subscription_id)
            blocked = self._trials.conversion_blocked_reason(subscription)
            if blocked:
                from billing.foundation import BillingConflict

                raise BillingConflict(f"cannot convert this trial: {blocked}")
            now = self._clock.now()
            moved = self._lifecycle.apply(subscription, AgreementTrigger.CONVERT_TRIAL, at=now)
            moved = replace(moved, cycle=self._cycles.open_period(moved.cycle, at=now))
            return self._commit_transition(
                command, uow, subscription, moved, SubscriptionAction.TRIAL_CONVERTED
            )

    def expire_trial(self, command: ExpireSubscriptionTrial) -> CommercialSubscription:
        return self._transition(
            command, AgreementTrigger.EXPIRE_TRIAL, SubscriptionAction.TRIAL_EXPIRED
        )

    def mark_past_due(self, command: MarkSubscriptionPastDue) -> CommercialSubscription:
        return self._transition(
            command,
            AgreementTrigger.PAYMENT_FAILED,
            SubscriptionAction.PAYMENT_FAILED,
            detail=command.reason,
        )

    def recover(self, command: RecoverSubscription) -> CommercialSubscription:
        return self._transition(
            command, AgreementTrigger.PAYMENT_RECOVERED, SubscriptionAction.PAYMENT_RECOVERED
        )

    def suspend(self, command: SuspendSubscription) -> CommercialSubscription:
        return self._transition(
            command,
            AgreementTrigger.SUSPEND,
            SubscriptionAction.SUSPENDED,
            detail=command.reason,
        )

    def reactivate(self, command: ReactivateSubscription) -> CommercialSubscription:
        return self._transition(
            command, AgreementTrigger.REACTIVATE, SubscriptionAction.REACTIVATED
        )

    def pause(self, command: PauseSubscription) -> CommercialSubscription:
        """The FSM decides whether pausing is *structurally* legal; the pause policy decides
        whether the customer has any pauses left. Both are Foundation calls."""
        return self._transition(
            command,
            AgreementTrigger.PAUSE,
            SubscriptionAction.PAUSED,
            detail=command.reason,
            guard_pause=True,
        )

    def resume(self, command: ResumeSubscription) -> CommercialSubscription:
        return self._transition(command, AgreementTrigger.RESUME, SubscriptionAction.RESUMED)

    def cancel(self, command: CancelSubscription) -> CommercialSubscription:
        return self._transition(
            command,
            AgreementTrigger.CANCEL,
            SubscriptionAction.CANCELLED,
            reason=command.reason,
            reason_code=command.reason_code,
            timing=command.timing,
            detail=command.reason,
        )

    def expire(self, command: ExpireSubscription) -> CommercialSubscription:
        return self._transition(command, AgreementTrigger.EXPIRE, SubscriptionAction.EXPIRED)

    def archive(self, command: ArchiveSubscription) -> CommercialSubscription:
        return self._transition(command, AgreementTrigger.ARCHIVE, SubscriptionAction.ARCHIVED)

    # ── items ───────────────────────────────────────────────────────────────────

    def add_item(self, command: AddSubscriptionItem) -> CommercialSubscription:
        self._authz.require(command.actor_id, command.org_id, P.BILLING_MANAGE)
        with self._uow.new() as uow:
            subscription = self._subscription(command.org_id, command.subscription_id)
            self._lifecycle.guard_editable(subscription)
            item = self._factory.item(
                price_code=command.price_code,
                price_version=command.price_version,
                kind=command.kind,
                quantity=command.quantity,
                meter_code=command.meter_code,
                included_units=command.included_units,
                label=command.label,
            )
            stored = self._repos.subscriptions.replace(
                self._factory.add_item(subscription, item, actor_id=command.actor_id)
            )
            self._record(
                command,
                stored.id,
                SubscriptionAction.ITEM_ADDED,
                detail=item.pin,
                version=stored.version,
            )
            uow.stage(self._event(command, stored.id, item=item.id, price=item.pin))
            return stored

    def update_item(self, command: UpdateSubscriptionItem) -> CommercialSubscription:
        self._authz.require(command.actor_id, command.org_id, P.BILLING_MANAGE)
        with self._uow.new() as uow:
            subscription = self._subscription(command.org_id, command.subscription_id)
            self._lifecycle.guard_editable(subscription)
            stored = self._repos.subscriptions.replace(
                self._factory.update_item(
                    subscription,
                    command.item_id,
                    actor_id=command.actor_id,
                    quantity=command.quantity,
                    included_units=command.included_units,
                    label=command.label,
                )
            )
            self._record(
                command,
                stored.id,
                SubscriptionAction.ITEM_UPDATED,
                detail=command.item_id,
                version=stored.version,
            )
            uow.stage(self._event(command, stored.id, item=command.item_id))
            return stored

    def remove_item(self, command: RemoveSubscriptionItem) -> CommercialSubscription:
        self._authz.require(command.actor_id, command.org_id, P.BILLING_MANAGE)
        with self._uow.new() as uow:
            subscription = self._subscription(command.org_id, command.subscription_id)
            self._lifecycle.guard_editable(subscription)
            stored = self._repos.subscriptions.replace(
                self._factory.remove_item(subscription, command.item_id, actor_id=command.actor_id)
            )
            self._record(
                command,
                stored.id,
                SubscriptionAction.ITEM_REMOVED,
                detail=command.item_id,
                version=stored.version,
            )
            uow.stage(self._event(command, stored.id, item=command.item_id))
            return stored

    # ── scheduled changes ───────────────────────────────────────────────────────

    def schedule_change(self, command: ScheduleSubscriptionChange) -> CommercialSubscription:
        """Records intent. Nothing here applies it, now or later."""
        self._authz.require(command.actor_id, command.org_id, P.BILLING_MANAGE)
        with self._uow.new() as uow:
            subscription = self._subscription(command.org_id, command.subscription_id)
            self._lifecycle.guard_editable(subscription)
            stored = self._repos.subscriptions.replace(
                self._factory.schedule_change(
                    subscription,
                    kind=command.kind,
                    effective_at=command.effective_at,
                    target_catalog_version=command.target_catalog_version,
                    target_pricing_version=command.target_pricing_version,
                    target_plan_code=command.target_plan_code,
                    items=command.items,
                    timing=command.timing,
                    reason=command.reason,
                    actor_id=command.actor_id,
                )
            )
            action = {
                ScheduledChangeKind.UPGRADE: SubscriptionAction.UPGRADED,
                ScheduledChangeKind.DOWNGRADE: SubscriptionAction.DOWNGRADED,
            }.get(command.kind, SubscriptionAction.CHANGE_SCHEDULED)
            self._record(
                command,
                stored.id,
                action,
                detail=f"{command.kind.value} at {command.effective_at}",
                version=stored.version,
            )
            uow.stage(
                self._event(
                    command, stored.id, kind=command.kind.value, effective_at=command.effective_at
                )
            )
            return stored

    def cancel_scheduled_change(self, command: CancelScheduledChange) -> CommercialSubscription:
        self._authz.require(command.actor_id, command.org_id, P.BILLING_MANAGE)
        with self._uow.new() as uow:
            subscription = self._subscription(command.org_id, command.subscription_id)
            stored = self._repos.subscriptions.replace(
                self._schedules.cancel_change(subscription, command.change_id, at=self._clock.now())
            )
            self._factory.emit_transition(
                stored,
                event_type=_change_cancelled_event(),
                actor_id=command.actor_id,
                change=command.change_id,
            )
            self._record(
                command,
                stored.id,
                SubscriptionAction.CHANGE_CANCELLED,
                detail=command.change_id,
                version=stored.version,
            )
            uow.stage(self._event(command, stored.id, change=command.change_id))
            return stored

    # ── renewal ─────────────────────────────────────────────────────────────────

    def renew(self, command: RenewSubscription) -> CommercialSubscription:
        """Advances the cycle. Not a state change, and not a scheduler — this runs because a
        caller decided it should."""
        self._authz.require(command.actor_id, command.org_id, P.BILLING_MANAGE)
        with self._uow.new() as uow:
            subscription = self._subscription(command.org_id, command.subscription_id)
            renewed = self._cycles.renew(subscription, at=self._clock.now())
            stored = self._repos.subscriptions.replace(renewed)
            self._factory.emit_transition(
                stored,
                event_type=_TRANSITION_EVENTS[SubscriptionAction.RENEWED],
                actor_id=command.actor_id,
                cycle=stored.cycle.cycle_number,
            )
            self._record(
                command,
                stored.id,
                SubscriptionAction.RENEWED,
                detail=f"cycle {stored.cycle.cycle_number}",
                version=stored.version,
            )
            uow.stage(self._event(command, stored.id, cycle=stored.cycle.cycle_number))
            return stored

    def set_renewal_policy(self, command: SetRenewalPolicy) -> CommercialSubscription:
        self._authz.require(command.actor_id, command.org_id, P.BILLING_MANAGE)
        with self._uow.new() as uow:
            subscription = self._subscription(command.org_id, command.subscription_id)
            self._lifecycle.guard_editable(subscription)
            stored = self._repos.subscriptions.replace(
                self._factory.set_renewal_policy(
                    subscription, command.renewal, actor_id=command.actor_id
                )
            )
            self._record(
                command,
                stored.id,
                SubscriptionAction.RENEWAL_POLICY_CHANGED,
                detail=command.renewal.mode.value,
                version=stored.version,
            )
            uow.stage(self._event(command, stored.id, mode=command.renewal.mode.value))
            return stored

    # ── versioning ──────────────────────────────────────────────────────────────

    def publish_version(self, command: PublishSubscriptionVersion) -> SubscriptionVersion:
        self._authz.require(command.actor_id, command.org_id, P.BILLING_MANAGE)
        with self._uow.new() as uow:
            subscription = self._subscription(command.org_id, command.subscription_id)
            version = self._repos.versions.next_version(command.org_id, subscription.id)
            frozen = self._factory.publish_version(
                subscription, version=version, reason=command.reason, actor_id=command.actor_id
            )
            stored = self._repos.versions.publish(frozen)
            self._repos.subscriptions.replace(
                replace(subscription, version=version, updated_at=self._clock.now())
            )
            self._record(
                command,
                subscription.id,
                SubscriptionAction.VERSION_PUBLISHED,
                detail=command.reason,
                version=version,
            )
            uow.stage(self._event(command, stored.id, version=version))
            return stored

    def rollback(self, command: RollbackSubscription) -> SubscriptionVersion:
        """Publish a **new** version carrying earlier terms. The versions in between remain
        readable forever."""
        self._authz.require(command.actor_id, command.org_id, P.BILLING_MANAGE)
        with self._uow.new() as uow:
            subscription = self._subscription(command.org_id, command.subscription_id)
            source = self._repos.versions.get(command.org_id, subscription.id, command.to_version)
            if source is None:
                raise _NotFound(
                    f"subscription version {command.to_version} not found for "
                    f"{subscription.subscription_number!r}"
                )
            next_version = self._repos.versions.next_version(command.org_id, subscription.id)
            rolled = self._factory.rollback_version(
                source, version=next_version, reason=command.reason, actor_id=command.actor_id
            )
            stored = self._repos.versions.publish(rolled)
            if rolled.subscription is not None:
                self._repos.subscriptions.replace(rolled.subscription)
            self._record(
                command,
                subscription.id,
                SubscriptionAction.ROLLED_BACK,
                detail=f"terms restored from v{source.version}",
                version=next_version,
            )
            uow.stage(
                self._event(
                    command, stored.id, version=next_version, rolled_back_from=source.version
                )
            )
            return stored

    # ── internals ───────────────────────────────────────────────────────────────

    def _transition(
        self,
        command,
        trigger: AgreementTrigger,
        action: SubscriptionAction,
        *,
        reason: str = "",
        reason_code: str = "",
        timing: CancellationTiming | None = None,
        detail: str = "",
        open_period: bool = False,
        guard_pause: bool = False,
    ) -> CommercialSubscription:
        self._authz.require(command.actor_id, command.org_id, P.BILLING_MANAGE)
        with self._uow.new() as uow:
            subscription = self._subscription(command.org_id, command.subscription_id)
            if guard_pause:
                self._lifecycle.guard_pausable(subscription)
            now = self._clock.now()
            # Legality is the Foundation's FSM decision, not this method's.
            moved = self._lifecycle.apply(
                subscription,
                trigger,
                at=now,
                reason=reason,
                reason_code=reason_code,
                timing=timing,
            )
            if open_period:
                moved = replace(moved, cycle=self._cycles.open_period(moved.cycle, at=now))
            return self._commit_transition(command, uow, subscription, moved, action, detail=detail)

    def _commit_transition(
        self, command, uow, before, after, action: SubscriptionAction, *, detail: str = ""
    ) -> CommercialSubscription:
        stored = self._repos.subscriptions.replace(after)
        self._factory.emit_transition(
            stored,
            event_type=_TRANSITION_EVENTS[action],
            actor_id=command.actor_id,
            state=stored.state.value,
        )
        self._record(
            command,
            stored.id,
            action,
            from_state=before.state.value,
            to_state=stored.state.value,
            detail=detail,
            version=stored.version,
        )
        uow.stage(self._event(command, stored.id, state=stored.state.value))
        return stored

    def _subscription(self, org_id: str, identifier: str) -> CommercialSubscription:
        found = self._repos.subscriptions.get(
            org_id, identifier
        ) or self._repos.subscriptions.by_number(org_id, identifier)
        if found is None:
            raise _NotFound(f"subscription {identifier!r} not found")
        return found

    def _known_catalog_versions(self, org_id: str) -> frozenset[int] | None:
        catalog = getattr(self._ctx, "catalog", None)
        if catalog is None:
            return None
        return frozenset(v.version for v in catalog.versions.history(org_id, limit=1000))

    def _known_pricing_versions(self, org_id: str) -> frozenset[int] | None:
        pricing = getattr(self._ctx, "pricing", None)
        if pricing is None:
            return None
        return frozenset(v.version for v in pricing.versions.history(org_id, limit=1000))

    def _account_state(self, org_id: str, account_id: str) -> str | None:
        """The account's lifecycle state as a **string**, or `None` when accounts are not
        wired. Passed as a string deliberately: this domain refers to an account and refuses to
        import its enum, which keeps the dependency one-way and shallow."""
        accounts = getattr(self._ctx, "commercial_accounts", None)
        if accounts is None:
            return None
        found = accounts.accounts.get(org_id, account_id)
        return "" if found is None else found.state.value

    def _record(
        self,
        command,
        subscription_id: str,
        action: SubscriptionAction,
        *,
        from_state: str = "",
        to_state: str = "",
        version: int = 1,
        detail: str = "",
    ) -> None:
        """Append subscription history **inside the caller's transaction**, so the history and
        the change it describes commit or vanish together."""
        self._repos.activity.append(
            self._factory.activity(
                org_id=command.org_id,
                subscription_id=subscription_id,
                action=action,
                actor_id=command.actor_id,
                from_state=from_state,
                to_state=to_state,
                version=version,
                detail=detail,
            )
        )

    def _event(self, command, subject_id: str, **data) -> ApplicationEvent:
        return ApplicationEvent(
            id=self._ids.new(PREFIX_APP_EVENT),
            type=ApplicationEventType.SUBSCRIPTION_TRANSITIONED,
            ts=self._clock.now(),
            use_case=type(command).__name__,
            outcome="completed",
            org_id=command.org_id,
            actor=command.actor_id,
            subject_id=subject_id,
            data=dict(data),
        )


def _change_cancelled_event():
    from billing.foundation import BillingEventType as E

    return E.SUBSCRIPTION_CHANGE_CANCELLED


class SubscriptionQueryUseCases:
    """Read-only. No transaction, no audit, no mutation."""

    def __init__(
        self,
        *,
        context,
        subscription_repos: SubscriptionRepositories,
        search,
        schedules,
        assembler,
        authz,
    ) -> None:
        self._ctx = context
        self._repos = subscription_repos
        self._search = search
        self._schedules = schedules
        self._assembler = assembler
        self._authz = authz
        from billing.foundation import AgreementLifecycleService

        self._lifecycle = AgreementLifecycleService()

    def get(self, query: GetCommercialSubscription) -> CommercialSubscriptionView | None:
        self._authz.require(query.actor_id, query.org_id, P.BILLING_READ)
        found = self._resolve(query.org_id, query.subscription_id or query.subscription_number)
        if found is None:
            return None
        return subscription_view(found, self._lifecycle.allowed(found.state))

    def search(self, query: SearchSubscriptions) -> SubscriptionSearchView:
        self._authz.require(query.actor_id, query.org_id, P.BILLING_READ)
        result = self._search.search(self._repos.subscriptions.all(query.org_id), query.query)
        return SubscriptionSearchView(
            subscriptions=tuple(
                subscription_view(s, self._lifecycle.allowed(s.state)) for s in result.subscriptions
            ),
            total=result.total,
        )

    def timeline(self, query: SubscriptionTimeline) -> tuple[SubscriptionActivityView, ...]:
        self._authz.require(query.actor_id, query.org_id, P.BILLING_READ)
        subscription_id = query.subscription_id
        if subscription_id:
            found = self._resolve(query.org_id, subscription_id)
            if found is None:
                return ()
            subscription_id = found.id
        return tuple(
            subscription_activity_view(a)
            for a in self._repos.activity.history(query.org_id, subscription_id, query.limit)
        )

    def versions(self, query: SubscriptionVersionHistory) -> tuple[SubscriptionVersionView, ...]:
        self._authz.require(query.actor_id, query.org_id, P.BILLING_READ)
        found = self._resolve(query.org_id, query.subscription_id)
        if found is None:
            return ()
        return tuple(
            subscription_version_view(v)
            for v in self._repos.versions.history(query.org_id, found.id, query.limit)
        )

    def snapshot(self, query: GetSubscriptionSnapshot) -> SubscriptionSnapshotView | None:
        """What agreement existed at an instant. With `version` set the answer comes from the
        frozen version; otherwise from the live aggregate. Same assembler, different input."""
        self._authz.require(query.actor_id, query.org_id, P.BILLING_READ)
        found = self._resolve(query.org_id, query.subscription_id)
        if found is None:
            return None
        at = query.at if query.at is not None else found.updated_at
        if query.version is not None:
            version = self._repos.versions.get(query.org_id, found.id, query.version)
            if version is None or version.subscription is None:
                raise _NotFound(f"subscription version {query.version} not found")
            return snapshot_view(self._assembler.snapshot(version.subscription, at=at))
        return snapshot_view(self._assembler.snapshot(found, at=at))

    def scheduled_changes(self, query: ListScheduledChanges) -> tuple[ScheduledChangeView, ...]:
        self._authz.require(query.actor_id, query.org_id, P.BILLING_READ)
        found = self._resolve(query.org_id, query.subscription_id)
        if found is None:
            return ()
        changes = (
            self._schedules.due(found, at=query.due_at)
            if query.due_at is not None
            else found.pending_changes
        )
        return tuple(_change_view(c) for c in changes)

    def _resolve(self, org_id: str, identifier: str) -> CommercialSubscription | None:
        if not identifier:
            return None
        return self._repos.subscriptions.get(
            org_id, identifier
        ) or self._repos.subscriptions.by_number(org_id, identifier)


# ── wiring table ────────────────────────────────────────────────────────────────


def register_subscription_command_handlers(bus, uc: SubscriptionCommandUseCases) -> None:
    bus.register(CreateSubscription, uc.create)
    bus.register(ActivateSubscription, uc.activate)
    bus.register(StartSubscriptionTrial, uc.start_trial)
    bus.register(ConvertSubscriptionTrial, uc.convert_trial)
    bus.register(ExpireSubscriptionTrial, uc.expire_trial)
    bus.register(MarkSubscriptionPastDue, uc.mark_past_due)
    bus.register(RecoverSubscription, uc.recover)
    bus.register(SuspendSubscription, uc.suspend)
    bus.register(ReactivateSubscription, uc.reactivate)
    bus.register(PauseSubscription, uc.pause)
    bus.register(ResumeSubscription, uc.resume)
    bus.register(CancelSubscription, uc.cancel)
    bus.register(ExpireSubscription, uc.expire)
    bus.register(ArchiveSubscription, uc.archive)
    bus.register(AddSubscriptionItem, uc.add_item)
    bus.register(UpdateSubscriptionItem, uc.update_item)
    bus.register(RemoveSubscriptionItem, uc.remove_item)
    bus.register(ScheduleSubscriptionChange, uc.schedule_change)
    bus.register(CancelScheduledChange, uc.cancel_scheduled_change)
    bus.register(RenewSubscription, uc.renew)
    bus.register(SetRenewalPolicy, uc.set_renewal_policy)
    bus.register(PublishSubscriptionVersion, uc.publish_version)
    bus.register(RollbackSubscription, uc.rollback)


def register_subscription_query_handlers(bus, uc: SubscriptionQueryUseCases) -> None:
    bus.register(GetCommercialSubscription, uc.get)
    bus.register(SearchSubscriptions, uc.search)
    bus.register(SubscriptionTimeline, uc.timeline)
    bus.register(SubscriptionVersionHistory, uc.versions)
    bus.register(GetSubscriptionSnapshot, uc.snapshot)
    bus.register(ListScheduledChanges, uc.scheduled_changes)
