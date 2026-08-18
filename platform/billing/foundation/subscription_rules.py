"""Subscription rules (M9.9) — validation, lifecycle, cycles, trials, scheduling, versioning.

Every subscription *decision* lives here: what makes an agreement valid, which lifecycle moves
are legal, how a billing period advances, when a trial ends, which scheduled changes conflict,
how a version is frozen, and how a snapshot is composed. Layers above coordinate these; they
never re-decide.

Validation follows the Foundation discipline exactly — it **returns diagnostics and never
raises**, reports *every* finding rather than the first, and orders them deterministically.

Three disciplines are worth stating before the code:

* **Nothing here computes an amount.** A subscription records what was agreed; turning that
  into a charge is rating (M9.11). `Money` does not appear in this module, and a test asserts
  it — including the arithmetic that would produce one.
* **Nothing here executes a schedule.** `ScheduleService` validates and *reads* intent; there
  is no timer, no worker, and no auto-apply. A due change is a question the caller asks, not an
  action this module takes.
* **Nothing here reads a clock.** Every temporal decision takes `at` as an argument, which is
  what makes a renewal computation reproducible in a test and in a billing run alike (§A2.4).

Pure: no I/O, no clock read, no persistence.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import replace

from core.fsm import StateMachineDefinition

from .commercial_subscriptions import (
    AGREEMENT_LIFECYCLE,
    AgreementItem,
    AgreementState,
    AgreementTrigger,
    BillingCycle,
    CancellationPolicy,
    CancellationTiming,
    CommercialSubscription,
    ItemKind,
    PausePolicy,
    RenewalPolicy,
    ScheduledChangeKind,
    ScheduledChangeStatus,
    ScheduledSubscriptionChange,
    SubscriptionActivity,
    SubscriptionHistory,
    SubscriptionSearchQuery,
    SubscriptionSearchResult,
    SubscriptionSnapshot,
    SubscriptionVersion,
    TrialPolicy,
)
from .diagnostics import BillingDiagnosticCode as C
from .diagnostics import BillingDiagnostics, Diagnostic, Severity
from .domain import BillingInterval, BillingPeriod
from .errors import BillingConflict, InvalidBillingTransition
from .services import PeriodService

#: Period arithmetic is **M9.2's**, not a second copy of it. `PeriodService` already owns "how
#: long is a month", and duplicating that table here would have created two answers to one
#: question — the exact drift §A2.5 exists to prevent. It covers the two members the frozen
#: `BillingInterval` declares; weekly, quarterly, and daily terms would need a new enum member,
#: and adding one to an enum pricing, invoicing, and proration all switch on is a change this
#: milestone has no mandate to make. Recorded as a limitation instead of quietly widened.

#: How many pending changes one agreement may carry. Beyond this the customer cannot
#: meaningfully know what they have agreed to, which is the failure this bound prevents.
MAX_PENDING_CHANGES = 10


def _err(path: str, code: C, message: str, remediation: str = "") -> Diagnostic:
    return Diagnostic(path, code, Severity.ERROR, message, remediation)


def _warn(path: str, code: C, message: str, remediation: str = "") -> Diagnostic:
    return Diagnostic(path, code, Severity.WARNING, message, remediation)


def _duplicates(values: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    dupes: set[str] = set()
    for value in values:
        if value in seen:
            dupes.add(value)
        seen.add(value)
    return sorted(dupes)


# ── validation ──────────────────────────────────────────────────────────────────


class SubscriptionValidator:
    """Structural, referential, and temporal validation for subscriptions. Never raises."""

    def validate_subscription(
        self,
        subscription: CommercialSubscription,
        *,
        known_catalog_versions: frozenset[int] | None = None,
        known_pricing_versions: frozenset[int] | None = None,
        account_state: str | None = None,
        at: float | None = None,
    ) -> BillingDiagnostics:
        """`known_*_versions=None` means "not checked here"; an **empty set** means "nothing is
        published", which must reject the pin rather than skip the check — the distinction an
        empty-collection falsiness test silently loses, and the defect M9.6 caught the hard
        way."""
        out: list[Diagnostic] = []
        if not subscription.org_id:
            out.append(_err("subscription.org_id", C.EMPTY_IDENTITY, "org_id is required"))
        if not subscription.account_id:
            out.append(
                _err(
                    "subscription.account_id",
                    C.EMPTY_IDENTITY,
                    "a subscription belongs to a billing account",
                )
            )
        if not subscription.subscription_number:
            out.append(
                _err(
                    "subscription.subscription_number",
                    C.EMPTY_IDENTITY,
                    "a subscription number is required",
                )
            )
        if subscription.version < 1:
            out.append(
                _err("subscription.version", C.INVALID_CODE, "versions start at 1 and increase")
            )

        out.extend(
            self._commercial_pins(subscription, known_catalog_versions, known_pricing_versions)
        )
        out.extend(self._account(subscription, account_state))
        out.extend(self.validate_items(subscription.items).findings)
        out.extend(self.validate_cycle(subscription.cycle).findings)
        out.extend(self.validate_trial(subscription.trial).findings)
        out.extend(self.validate_renewal(subscription.renewal).findings)
        out.extend(self.validate_pause(subscription.pause).findings)
        out.extend(self.validate_cancellation(subscription.cancellation).findings)
        out.extend(self.validate_schedule(subscription.scheduled_changes, at=at).findings)

        # A live agreement must actually agree to something.
        if subscription.state in (AgreementState.ACTIVE, AgreementState.TRIALING) and not (
            subscription.items
        ):
            out.append(
                _err(
                    "subscription.items",
                    C.EMPTY_SUBSCRIPTION,
                    "a live subscription with no items is an agreement to nothing",
                )
            )
        if subscription.state is AgreementState.TRIALING and not subscription.trial.enabled:
            out.append(
                _err(
                    "subscription.trial",
                    C.INVALID_TRIAL_POLICY,
                    "a trialing subscription must carry an enabled trial policy",
                )
            )
        return BillingDiagnostics.of(out)

    def _commercial_pins(
        self,
        subscription: CommercialSubscription,
        catalog_versions: frozenset[int] | None,
        pricing_versions: frozenset[int] | None,
    ) -> list[Diagnostic]:
        """The three references are the whole point of this aggregate; a dangling one makes the
        agreement unreproducible."""
        out: list[Diagnostic] = []
        if subscription.catalog_version < 1:
            out.append(
                _err(
                    "subscription.catalog_version",
                    C.UNKNOWN_CATALOG_VERSION,
                    "a subscription must pin a published catalog version",
                    "pins name frozen snapshots, never mutable catalog objects",
                )
            )
        elif catalog_versions is not None and subscription.catalog_version not in catalog_versions:
            out.append(
                _err(
                    "subscription.catalog_version",
                    C.UNKNOWN_CATALOG_VERSION,
                    f"no published catalog version {subscription.catalog_version}",
                )
            )
        if subscription.pricing_version < 1:
            out.append(
                _err(
                    "subscription.pricing_version",
                    C.UNKNOWN_PRICING_VERSION,
                    "a subscription must pin a published pricing version",
                )
            )
        elif pricing_versions is not None and subscription.pricing_version not in pricing_versions:
            out.append(
                _err(
                    "subscription.pricing_version",
                    C.UNKNOWN_PRICING_VERSION,
                    f"no published pricing version {subscription.pricing_version}",
                )
            )
        return out

    @staticmethod
    def _account(
        subscription: CommercialSubscription, account_state: str | None
    ) -> list[Diagnostic]:
        """`account_state` is passed in as a *string* deliberately: this domain refers to an
        account, and refusing to import its enum keeps the dependency one-way and shallow."""
        out: list[Diagnostic] = []
        if account_state is None:
            return out
        if account_state == "":
            out.append(
                _err(
                    "subscription.account_id",
                    C.UNKNOWN_BILLING_ACCOUNT,
                    f"no billing account {subscription.account_id!r} in this organization",
                )
            )
        elif account_state != "active" and subscription.state in (
            AgreementState.ACTIVE,
            AgreementState.TRIALING,
        ):
            out.append(
                _err(
                    "subscription.account_id",
                    C.INACTIVE_BILLING_ACCOUNT,
                    f"the billing account is {account_state}; a live subscription needs an "
                    "active one",
                )
            )
        return out

    def validate_items(self, items: Sequence[AgreementItem]) -> BillingDiagnostics:
        out: list[Diagnostic] = []
        for dupe in _duplicates(i.id for i in items):
            out.append(
                _err(
                    "subscription.items",
                    C.DUPLICATE_SUBSCRIPTION_ITEM,
                    f"item id {dupe!r} appears more than once",
                )
            )
        for dupe in _duplicates(i.pin for i in items):
            out.append(
                _err(
                    "subscription.items",
                    C.DUPLICATE_SUBSCRIPTION_ITEM,
                    f"price {dupe!r} is subscribed twice",
                    "one line per price version; use quantity for more of the same thing",
                )
            )
        for i, item in enumerate(items):
            path = f"subscription.items[{i}]"
            if not item.price_code:
                out.append(
                    _err(f"{path}.price_code", C.EMPTY_IDENTITY, "an item must name a price")
                )
            if item.price_version < 1:
                out.append(
                    _err(
                        f"{path}.price_version",
                        C.INVALID_SUBSCRIPTION_ITEM,
                        "an item pins a price *version*; versions start at 1",
                        "the pin is what makes an old invoice re-derivable",
                    )
                )
            if item.quantity < 0:
                out.append(
                    _err(f"{path}.quantity", C.INVALID_QUANTITY, "quantity cannot be negative")
                )
            if item.kind is ItemKind.SEAT and item.quantity < 1:
                out.append(
                    _err(
                        f"{path}.quantity",
                        C.INVALID_SUBSCRIPTION_ITEM,
                        "a seat item must carry at least one seat",
                    )
                )
            if item.kind in (ItemKind.USAGE, ItemKind.TIERED, ItemKind.VOLUME) and not (
                item.meter_code
            ):
                out.append(
                    _err(
                        f"{path}.meter_code",
                        C.INVALID_SUBSCRIPTION_ITEM,
                        f"a {item.kind.value} item must name the meter it is measured by",
                        "usage nobody can attribute to a meter cannot be rated",
                    )
                )
            if item.kind is ItemKind.FLAT and item.meter_code:
                out.append(
                    _warn(
                        f"{path}.meter_code",
                        C.INVALID_SUBSCRIPTION_ITEM,
                        "a flat item names a meter, which nothing will read",
                    )
                )
            if item.included_units < 0:
                out.append(
                    _err(
                        f"{path}.included_units", C.INVALID_QUANTITY, "included_units must be >= 0"
                    )
                )
        return BillingDiagnostics.of(out)

    def validate_cycle(self, cycle: BillingCycle) -> BillingDiagnostics:
        out: list[Diagnostic] = []
        if not 1 <= cycle.anchor_day <= 28:
            out.append(
                _err(
                    "cycle.anchor_day",
                    C.INVALID_BILLING_CYCLE,
                    f"anchor day {cycle.anchor_day} is outside 1..28",
                    "28 is the last day every month has; beyond it the date silently drifts",
                )
            )
        if cycle.cycle_number < 1:
            out.append(
                _err("cycle.cycle_number", C.INVALID_BILLING_CYCLE, "cycles are numbered from 1")
            )
        period = cycle.current_period
        if period is not None and period.end <= period.start:
            out.append(
                _err(
                    "cycle.current_period",
                    C.INVALID_PERIOD,
                    "a billing period must end after it starts",
                )
            )
        return BillingDiagnostics.of(out)

    def validate_trial(self, trial: TrialPolicy) -> BillingDiagnostics:
        out: list[Diagnostic] = []
        if trial.enabled and trial.length_days <= 0 and trial.ends_at is None:
            out.append(
                _err(
                    "trial.length_days",
                    C.INVALID_TRIAL_POLICY,
                    "an enabled trial needs a length or an explicit end",
                )
            )
        if trial.length_days < 0:
            out.append(
                _err(
                    "trial.length_days", C.INVALID_TRIAL_POLICY, "a trial length cannot be negative"
                )
            )
        if not trial.enabled and trial.ends_at is not None:
            out.append(
                _warn(
                    "trial.ends_at",
                    C.INVALID_TRIAL_POLICY,
                    "a disabled trial carries an end date, which nothing will act on",
                )
            )
        return BillingDiagnostics.of(out)

    def validate_renewal(self, renewal: RenewalPolicy) -> BillingDiagnostics:
        out: list[Diagnostic] = []
        if renewal.notice_days < 0:
            out.append(
                _err("renewal.notice_days", C.INVALID_RENEWAL_POLICY, "notice cannot be negative")
            )
        if renewal.terms is not None and renewal.terms < 1:
            out.append(
                _err(
                    "renewal.terms",
                    C.INVALID_RENEWAL_POLICY,
                    "a fixed-term policy renews at least once; use mode=none for no renewal",
                )
            )
        if renewal.terms_used < 0:
            out.append(
                _err(
                    "renewal.terms_used", C.INVALID_RENEWAL_POLICY, "terms_used cannot be negative"
                )
            )
        if renewal.terms is not None and renewal.terms_used > renewal.terms:
            out.append(
                _err(
                    "renewal.terms_used",
                    C.INVALID_RENEWAL_POLICY,
                    f"{renewal.terms_used} terms used exceeds the {renewal.terms} agreed",
                )
            )
        return BillingDiagnostics.of(out)

    def validate_pause(self, pause: PausePolicy) -> BillingDiagnostics:
        out: list[Diagnostic] = []
        if pause.max_pause_days < 0:
            out.append(_err("pause.max_pause_days", C.INVALID_PAUSE_POLICY, "cannot be negative"))
        if pause.pauses_per_term < 0:
            out.append(_err("pause.pauses_per_term", C.INVALID_PAUSE_POLICY, "cannot be negative"))
        if pause.pauses_used < 0:
            out.append(_err("pause.pauses_used", C.INVALID_PAUSE_POLICY, "cannot be negative"))
        if (
            pause.paused_at is not None
            and pause.resume_at is not None
            and pause.resume_at <= pause.paused_at
        ):
            out.append(
                _err(
                    "pause.resume_at",
                    C.INVALID_PAUSE_POLICY,
                    "a resume date must follow the pause it ends",
                )
            )
        return BillingDiagnostics.of(out)

    def validate_cancellation(self, cancellation: CancellationPolicy) -> BillingDiagnostics:
        out: list[Diagnostic] = []
        if cancellation.notice_days < 0:
            out.append(
                _err("cancellation.notice_days", C.INVALID_CANCELLATION, "cannot be negative")
            )
        if (
            cancellation.default_timing is CancellationTiming.SCHEDULED
            and cancellation.effective_at is None
            and cancellation.is_requested
        ):
            out.append(
                _err(
                    "cancellation.effective_at",
                    C.INVALID_CANCELLATION,
                    "a scheduled cancellation must say when it takes effect",
                )
            )
        if (
            cancellation.is_requested
            and cancellation.effective_at is not None
            and cancellation.requested_at is not None
            and cancellation.effective_at < cancellation.requested_at
        ):
            out.append(
                _err(
                    "cancellation.effective_at",
                    C.INVALID_CANCELLATION,
                    "a cancellation cannot take effect before it was requested",
                )
            )
        return BillingDiagnostics.of(out)

    def validate_schedule(
        self, changes: Sequence[ScheduledSubscriptionChange], *, at: float | None = None
    ) -> BillingDiagnostics:
        """Scheduled changes are *intent*. Validation therefore checks that the intent is
        coherent and unambiguous, never that it is executable — nothing executes it here."""
        out: list[Diagnostic] = []
        pending = [c for c in changes if c.is_pending]

        for dupe in _duplicates(c.id for c in changes):
            out.append(
                _err(
                    "subscription.scheduled_changes",
                    C.INVALID_SCHEDULED_CHANGE,
                    f"change id {dupe!r} appears more than once",
                )
            )
        if len(pending) > MAX_PENDING_CHANGES:
            out.append(
                _err(
                    "subscription.scheduled_changes",
                    C.CONFLICTING_SCHEDULED_CHANGE,
                    f"{len(pending)} pending changes; the maximum is {MAX_PENDING_CHANGES}",
                    "beyond this a customer cannot know what they have agreed to",
                )
            )

        for i, change in enumerate(changes):
            path = f"subscription.scheduled_changes[{i}]"
            if change.effective_at <= 0:
                out.append(
                    _err(
                        f"{path}.effective_at",
                        C.INVALID_SCHEDULED_CHANGE,
                        "a scheduled change must say when it takes effect",
                    )
                )
            elif at is not None and change.is_pending and change.effective_at < at:
                out.append(
                    _warn(
                        f"{path}.effective_at",
                        C.SCHEDULED_CHANGE_IN_PAST,
                        "this change was due in the past and is still pending",
                        "nothing in this domain applies it; a caller must dispatch the command",
                    )
                )
            if change.kind is ScheduledChangeKind.PRICING_SWITCH and (
                change.target_pricing_version is None
            ):
                out.append(
                    _err(
                        f"{path}.target_pricing_version",
                        C.INVALID_SCHEDULED_CHANGE,
                        "a pricing switch must name the pricing version to move to",
                    )
                )
            if change.kind in (ScheduledChangeKind.UPGRADE, ScheduledChangeKind.DOWNGRADE) and not (
                change.items
                or change.target_plan_code
                or change.target_catalog_version
                or change.target_pricing_version
            ):
                out.append(
                    _err(
                        f"{path}",
                        C.INVALID_SCHEDULED_CHANGE,
                        f"a scheduled {change.kind.value} must say what changes",
                    )
                )
            if change.target_catalog_version is not None and change.target_catalog_version < 1:
                out.append(
                    _err(
                        f"{path}.target_catalog_version",
                        C.UNKNOWN_CATALOG_VERSION,
                        "catalog versions start at 1",
                    )
                )
            if change.target_pricing_version is not None and change.target_pricing_version < 1:
                out.append(
                    _err(
                        f"{path}.target_pricing_version",
                        C.UNKNOWN_PRICING_VERSION,
                        "pricing versions start at 1",
                    )
                )

        # Two pending cancellations, or a change scheduled after one, is an ambiguous future.
        cancellations = [c for c in pending if c.kind is ScheduledChangeKind.CANCELLATION]
        if len(cancellations) > 1:
            out.append(
                _err(
                    "subscription.scheduled_changes",
                    C.CONFLICTING_SCHEDULED_CHANGE,
                    f"{len(cancellations)} pending cancellations; there may be at most one",
                )
            )
        elif cancellations:
            ends_at = cancellations[0].effective_at
            for change in pending:
                if change.kind is not ScheduledChangeKind.CANCELLATION and (
                    change.effective_at >= ends_at
                ):
                    out.append(
                        _err(
                            "subscription.scheduled_changes",
                            C.CONFLICTING_SCHEDULED_CHANGE,
                            f"a {change.kind.value} is scheduled at or after the cancellation",
                            "the subscription will not exist to receive it",
                        )
                    )
        return BillingDiagnostics.of(out)

    def validate_version(self, version: SubscriptionVersion) -> BillingDiagnostics:
        """Version integrity — a published version must be internally complete, because nothing
        may ever be added to it."""
        out: list[Diagnostic] = []
        if version.version < 1:
            out.append(
                _err(
                    "subscription_version.version",
                    C.SUBSCRIPTION_SNAPSHOT_INTEGRITY,
                    "versions start at 1 and increase",
                )
            )
        if not version.org_id:
            out.append(_err("subscription_version.org_id", C.EMPTY_IDENTITY, "org_id is required"))
        if not version.subscription_id:
            out.append(
                _err(
                    "subscription_version.subscription_id",
                    C.EMPTY_IDENTITY,
                    "a version belongs to a subscription",
                )
            )
        if version.subscription is None:
            out.append(
                _err(
                    "subscription_version.subscription",
                    C.SUBSCRIPTION_SNAPSHOT_INTEGRITY,
                    "a version must embed the agreement it froze",
                    "a version that pointed at live data would not be a snapshot",
                )
            )
        elif version.subscription.version != version.version:
            out.append(
                _err(
                    "subscription_version.subscription",
                    C.SUBSCRIPTION_SNAPSHOT_INTEGRITY,
                    f"version {version.version} embeds an agreement at v"
                    f"{version.subscription.version}",
                )
            )
        if version.rolled_back_from is not None and version.rolled_back_from >= version.version:
            out.append(
                _err(
                    "subscription_version.rolled_back_from",
                    C.SUBSCRIPTION_SNAPSHOT_INTEGRITY,
                    "a rollback source must be an earlier version",
                    "rollback moves forward by copying backward — history is never rewritten",
                )
            )
        return BillingDiagnostics.of(out)


# ── lifecycle ───────────────────────────────────────────────────────────────────


class AgreementLifecycleService:
    """Applies subscription lifecycle triggers through the shared FSM. Illegal edges are
    rejected with a typed error, never coerced into an adjacent state.

    Named distinctly from M9.2's `SubscriptionLifecycleService`, which drives the frozen M9.2
    `Subscription` over its own table. Both exist and mean different things."""

    def __init__(self, definition: StateMachineDefinition = AGREEMENT_LIFECYCLE) -> None:
        self._definition = definition

    def can(self, current: AgreementState, trigger: AgreementTrigger) -> bool:
        return self._definition.get(current, trigger) is not None

    def next_state(self, current: AgreementState, trigger: AgreementTrigger) -> AgreementState:
        edge = self._definition.get(current, trigger)
        if edge is None:
            legal = sorted(t.value for t in self._definition.triggers_from(current))
            raise InvalidBillingTransition(
                f"subscription cannot {trigger.value} from {current.value}; legal triggers: {legal}"
            )
        return edge.target

    def allowed(self, current: AgreementState) -> tuple[AgreementTrigger, ...]:
        """Which moves are legal from here — what a UI needs to render buttons that cannot
        produce a 409."""
        return tuple(t for t in AgreementTrigger if self.can(current, t))

    def apply(
        self,
        subscription: CommercialSubscription,
        trigger: AgreementTrigger,
        *,
        at: float,
        reason: str = "",
        reason_code: str = "",
        timing: CancellationTiming | None = None,
    ) -> CommercialSubscription:
        state = self.next_state(subscription.state, trigger)
        changes: dict = {"state": state, "updated_at": at}

        if state is AgreementState.ACTIVE and subscription.started_at is None:
            changes["started_at"] = at
        if trigger is AgreementTrigger.START_TRIAL:
            changes["started_at"] = subscription.started_at or at
            if subscription.trial.ends_at is None and subscription.trial.length_days:
                changes["trial"] = replace(
                    subscription.trial, ends_at=subscription.trial.end_from(at)
                )
        if trigger is AgreementTrigger.PAUSE:
            changes["pause"] = replace(
                subscription.pause,
                paused_at=at,
                pauses_used=subscription.pause.pauses_used + 1,
            )
        if trigger is AgreementTrigger.RESUME:
            changes["pause"] = replace(subscription.pause, paused_at=None, resume_at=at)
        if trigger is AgreementTrigger.CANCEL:
            changes["cancelled_at"] = at
            changes["cancellation"] = replace(
                subscription.cancellation,
                default_timing=timing or subscription.cancellation.default_timing,
                reason=reason or subscription.cancellation.reason,
                reason_code=reason_code or subscription.cancellation.reason_code,
                requested_at=at,
                effective_at=self._cancellation_effective_at(subscription, at, timing),
            )
        if state in (AgreementState.EXPIRED,):
            changes["ended_at"] = at
        if state is AgreementState.ARCHIVED:
            changes["archived_at"] = at
        return replace(subscription, **changes)

    @staticmethod
    def _cancellation_effective_at(
        subscription: CommercialSubscription, at: float, timing: CancellationTiming | None
    ) -> float:
        """When the cancellation bites. Period-end cancellation is the default because a
        customer who has paid for a period should keep it — and if no period is known, the
        honest answer is *now* rather than a date this domain invented."""
        effective = timing or subscription.cancellation.default_timing
        if effective is CancellationTiming.IMMEDIATE:
            return at
        period = subscription.cycle.current_period
        if effective is CancellationTiming.PERIOD_END and period is not None:
            return period.end
        if effective is CancellationTiming.SCHEDULED:
            return subscription.cancellation.effective_at or at
        return at

    def guard_editable(self, subscription: CommercialSubscription) -> None:
        """A closed agreement is a historical record. Its terms are what was agreed, and
        editing them after it ended would change the record of a finished relationship."""
        if not subscription.is_editable:
            raise BillingConflict(
                f"subscription {subscription.subscription_number!r} is "
                f"{subscription.state.value}; its terms are a retained record and are no "
                "longer editable"
            )

    def guard_pausable(self, subscription: CommercialSubscription) -> None:
        """Pause allowance is a *commercial* limit, distinct from the FSM's structural one:
        the transition is legal, but the customer has used up what they agreed to."""
        if not subscription.pause.may_pause():
            raise BillingConflict(
                f"subscription {subscription.subscription_number!r} has used all "
                f"{subscription.pause.pauses_per_term} pauses agreed for this term"
            )


# ── billing cycle ───────────────────────────────────────────────────────────────


class BillingCycleService:
    """Advances a subscription's billing period. Deterministic and clock-free — `at` is always
    an argument, which is what makes a renewal reproducible in a test and in a billing run.

    Renewal lives here rather than in the FSM because it does not change what state the
    agreement is in: an active subscription that renews is still active, with a later period
    and a higher cycle number."""

    def __init__(self, periods: PeriodService | None = None) -> None:
        self._periods = periods or PeriodService()

    def open_period(self, cycle: BillingCycle, *, at: float) -> BillingCycle:
        """Start the first period. Idempotent: a cycle that already has one is returned
        unchanged, so opening twice cannot silently reset a customer's billing date."""
        if cycle.has_period:
            return cycle
        return replace(cycle, current_period=self.period_from(cycle.interval, at))

    def period_from(self, interval: BillingInterval, start: float) -> BillingPeriod:
        """Delegated to M9.2's `PeriodService` — one definition of a period length."""
        return self._periods.period_for(interval, start=start)

    def next_period(self, cycle: BillingCycle, *, at: float) -> BillingPeriod:
        """The period that follows. Anchored to the current period's end rather than to `at`,
        so a renewal processed late does not shift the customer's billing date forward."""
        period = cycle.current_period
        start = period.end if period is not None else at
        return self.period_from(cycle.interval, start)

    def renew(self, subscription: CommercialSubscription, *, at: float) -> CommercialSubscription:
        """Advance one term. Raises rather than silently no-ops when the agreed terms are
        exhausted — a renewal beyond what was agreed is a commercial decision, not a
        computation."""
        if not subscription.renewal.renews:
            raise BillingConflict(
                f"subscription {subscription.subscription_number!r} has exhausted its agreed "
                "renewal terms"
            )
        if subscription.state is not AgreementState.ACTIVE:
            raise BillingConflict(
                f"only an active subscription renews; this one is {subscription.state.value}"
            )
        cycle = subscription.cycle
        return replace(
            subscription,
            cycle=replace(
                cycle,
                current_period=self.next_period(cycle, at=at),
                cycle_number=cycle.cycle_number + 1,
            ),
            renewal=replace(
                subscription.renewal,
                terms_used=subscription.renewal.terms_used + 1,
                next_renewal_at=self.next_period(cycle, at=at).end,
            ),
            updated_at=at,
        )

    def is_due_for_renewal(self, subscription: CommercialSubscription, *, at: float) -> bool:
        """A read. Nothing in this domain renews on its own — there is no scheduler."""
        period = subscription.cycle.current_period
        return (
            subscription.state is AgreementState.ACTIVE
            and subscription.renewal.renews
            and period is not None
            and at >= period.end
        )


# ── trials ──────────────────────────────────────────────────────────────────────


class TrialService:
    """One deterministic trial policy: start, expiry, conversion. No payment provider — this
    domain has none, and `requires_payment_method` is a declared requirement a consuming
    surface enforces at conversion."""

    def start(
        self, subscription: CommercialSubscription, *, at: float, length_days: int | None = None
    ) -> TrialPolicy:
        length = length_days if length_days is not None else subscription.trial.length_days
        return replace(
            subscription.trial,
            enabled=True,
            length_days=length,
            ends_at=at + length * 86_400.0,
        )

    def is_expired(self, subscription: CommercialSubscription, *, at: float) -> bool:
        return subscription.is_trialing and subscription.trial.is_expired(at)

    def remaining_days(self, subscription: CommercialSubscription, *, at: float) -> int:
        ends_at = subscription.trial.ends_at
        if ends_at is None or not subscription.is_trialing:
            return 0
        return max(int((ends_at - at) // 86_400), 0)

    def conversion_blocked_reason(self, subscription: CommercialSubscription) -> str:
        """Why a conversion would be refused, as a sentence, or `""`. Returned rather than
        raised because a UI needs to *show* the reason before the customer clicks."""
        if not subscription.is_trialing:
            return f"the subscription is {subscription.state.value}, not trialing"
        if not subscription.items:
            return "the subscription has no items to convert to"
        return ""


# ── scheduled changes ───────────────────────────────────────────────────────────


class ScheduleService:
    """Reads and reasons about scheduled changes. **Applies nothing.**

    There is no timer here, no worker, and no auto-apply. `due` answers a question; turning
    that answer into a change is an explicit command a caller dispatches, validated by the FSM
    at that moment exactly as if a human had asked. Keeping intent and execution separable is
    what lets "what did we agree?" survive a failure in "what did we run?"."""

    def due(
        self, subscription: CommercialSubscription, *, at: float
    ) -> tuple[ScheduledSubscriptionChange, ...]:
        return subscription.due_changes(at)

    def next_change(
        self, subscription: CommercialSubscription
    ) -> ScheduledSubscriptionChange | None:
        pending = subscription.pending_changes
        return pending[0] if pending else None

    def conflicts_with(
        self,
        subscription: CommercialSubscription,
        candidate: ScheduledSubscriptionChange,
    ) -> str:
        """Why this change cannot be added, as a sentence, or `""`."""
        pending = subscription.pending_changes
        if len(pending) >= MAX_PENDING_CHANGES:
            return f"there are already {len(pending)} pending changes"
        if candidate.kind is ScheduledChangeKind.CANCELLATION and any(
            c.kind is ScheduledChangeKind.CANCELLATION for c in pending
        ):
            return "a cancellation is already scheduled"
        cancellation = next(
            (c for c in pending if c.kind is ScheduledChangeKind.CANCELLATION), None
        )
        if (
            cancellation is not None
            and candidate.kind is not ScheduledChangeKind.CANCELLATION
            and candidate.effective_at >= cancellation.effective_at
        ):
            return "the subscription is scheduled to end at or before that date"
        return ""

    def mark_applied(
        self, subscription: CommercialSubscription, change_id: str, *, at: float
    ) -> CommercialSubscription:
        """Record that a change was applied. The *applying* is the caller's command; this only
        moves the intent out of the pending set, so the record and the reality agree."""
        return self._restatus(subscription, change_id, ScheduledChangeStatus.APPLIED, at=at)

    def cancel_change(
        self, subscription: CommercialSubscription, change_id: str, *, at: float
    ) -> CommercialSubscription:
        return self._restatus(subscription, change_id, ScheduledChangeStatus.CANCELLED, at=at)

    @staticmethod
    def _restatus(
        subscription: CommercialSubscription,
        change_id: str,
        status: ScheduledChangeStatus,
        *,
        at: float,
    ) -> CommercialSubscription:
        found = subscription.scheduled_change(change_id)
        if found is None:
            raise BillingConflict(f"scheduled change {change_id!r} is not on this subscription")
        if not found.is_pending:
            raise BillingConflict(f"scheduled change {change_id!r} is already {found.status.value}")
        moved = replace(
            found,
            status=status,
            applied_at=at if status is ScheduledChangeStatus.APPLIED else found.applied_at,
        )
        return replace(
            subscription,
            scheduled_changes=tuple(
                moved if c.id == change_id else c for c in subscription.scheduled_changes
            ),
            updated_at=at,
        )


# ── versioning ──────────────────────────────────────────────────────────────────


class SubscriptionAssembler:
    """Freezes agreements into immutable versions and composes snapshots. Pure: the same
    subscription and the same version number always produce an identical version."""

    def freeze(
        self,
        subscription: CommercialSubscription,
        *,
        version_id: str,
        version: int,
        published_at: float,
        published_by: str = "",
        reason: str = "",
        rolled_back_from: int | None = None,
    ) -> SubscriptionVersion:
        """Embed the agreement as it is. Items and changes are sorted on the way in, so two
        freezes of an unchanged agreement are byte-identical — which makes "did anything
        actually change between v3 and v4?" a diff rather than an investigation."""
        frozen = replace(
            subscription,
            version=version,
            items=tuple(sorted(subscription.items, key=lambda i: (i.price_code, i.id))),
            scheduled_changes=tuple(
                sorted(subscription.scheduled_changes, key=lambda c: (c.effective_at, c.id))
            ),
        )
        return SubscriptionVersion(
            id=version_id,
            org_id=subscription.org_id,
            subscription_id=subscription.id,
            version=version,
            published_at=published_at,
            published_by=published_by,
            reason=reason,
            rolled_back_from=rolled_back_from,
            subscription=frozen,
        )

    def rollback(
        self,
        source: SubscriptionVersion,
        *,
        version_id: str,
        version: int,
        published_at: float,
        published_by: str = "",
        reason: str = "",
    ) -> SubscriptionVersion:
        """A rollback is a **new version** whose terms are copied from an older one.

        Nothing is rewritten and nothing is deleted: the versions in between remain readable
        forever, and the new version records where its terms came from. With a commercial
        agreement, "never rewrite history" is not an academic preference — it is the difference
        between a contract and a wiki page."""
        if version <= source.version:
            raise BillingConflict(
                f"rollback must publish a *new* version; {version} does not follow {source.version}"
            )
        if source.subscription is None:
            raise BillingConflict("cannot roll back to a version that embeds no agreement")
        return SubscriptionVersion(
            id=version_id,
            org_id=source.org_id,
            subscription_id=source.subscription_id,
            version=version,
            published_at=published_at,
            published_by=published_by,
            reason=reason or f"rollback to v{source.version}",
            rolled_back_from=source.version,
            subscription=replace(source.subscription, version=version, updated_at=published_at),
        )

    def snapshot(self, subscription: CommercialSubscription, *, at: float) -> SubscriptionSnapshot:
        """The composed answer to "what agreement existed at this instant?"."""
        return SubscriptionSnapshot(
            subscription=subscription,
            at=at,
            version=subscription.version,
            catalog_version=subscription.catalog_version,
            pricing_version=subscription.pricing_version,
            account_id=subscription.account_id,
            state=subscription.state,
            items=subscription.items,
            seat_count=subscription.seat_count,
            metered_codes=subscription.metered_codes,
            pending_change_count=len(subscription.pending_changes),
            in_period=subscription.cycle.covers(at),
        )

    def history(
        self,
        subscription_id: str,
        *,
        activity: Sequence[SubscriptionActivity] = (),
        versions: Sequence[SubscriptionVersion] = (),
    ) -> SubscriptionHistory:
        return SubscriptionHistory(
            subscription_id=subscription_id,
            activity=tuple(sorted(activity, key=lambda a: (a.at, a.id))),
            versions=tuple(sorted(versions, key=lambda v: v.version)),
        )


# ── search ──────────────────────────────────────────────────────────────────────


class SubscriptionSearchService:
    """Deterministic subscription filtering. No scoring, no ranking, no index — the same
    subscriptions and the same query always produce the same rows in the same order.

    Ordering is by subscription number, always. Arbitrary but *stable*, which is the property a
    paged admin screen and a reproducible test both actually need."""

    def search(
        self, subscriptions: Sequence[CommercialSubscription], query: SubscriptionSearchQuery
    ) -> SubscriptionSearchResult:
        matched = [s for s in subscriptions if self._matches(s, query)]
        matched.sort(key=lambda s: s.subscription_number)
        start = max(query.offset, 0)
        page = tuple(matched[start : start + max(query.limit, 0)])
        return SubscriptionSearchResult(subscriptions=page, total=len(matched))

    def _matches(
        self, subscription: CommercialSubscription, query: SubscriptionSearchQuery
    ) -> bool:
        return (
            self._text_matches(
                query.text,
                subscription.subscription_number,
                subscription.plan_code,
                *subscription.price_pins,
            )
            and (not query.states or subscription.state in query.states)
            and (not query.account_id or subscription.account_id == query.account_id)
            and (not query.plan_code or subscription.plan_code == query.plan_code)
            and (not query.currencies or subscription.currency in query.currencies)
            and (not query.intervals or subscription.cycle.interval in query.intervals)
            and (
                query.catalog_version is None
                or subscription.catalog_version == query.catalog_version
            )
            and (
                query.pricing_version is None
                or subscription.pricing_version == query.pricing_version
            )
            and (not query.meter_code or query.meter_code in subscription.metered_codes)
            and (not query.live_only or subscription.is_live)
            and (not query.trialing_only or subscription.is_trialing)
            and (not query.with_pending_changes or bool(subscription.pending_changes))
            and (
                query.renews_automatically is None
                or subscription.renewal.is_automatic is query.renews_automatically
            )
            and (query.active_at is None or subscription.cycle.covers(query.active_at))
        )

    @staticmethod
    def _text_matches(text: str, *fields: str) -> bool:
        if not text:
            return True
        needle = text.casefold()
        return any(needle in (value or "").casefold() for value in fields)
