"""Subscription Engine domain (M9.9) — the commercial agreement, and nothing else.

Three milestones built the vocabulary this one binds together. M9.6 said **what is sold**,
M9.7 said **what it costs**, M9.8 said **who we sell to**. A subscription is the fact that
joins them: *this account, on this catalog version, at this pricing version, from this date.*

**Subscriptions own** the lifecycle, items, account/catalog/pricing references, billing
interval and cycle, renewal / trial / pause / cancellation / upgrade / downgrade policies,
scheduled changes, versions, history, and activity.

**They own no** catalog, pricing, rating, metering, usage, ledger, credit, invoice, payment,
refund, tax, revenue recognition, runtime, deployment, knowledge, or authorization. Not one
type here carries a `Money`, and a test asserts it — a subscription records *what was agreed*;
turning that into an amount is rating (M9.11).

Two properties are structural rather than conventional:

* **The three references are immutable pins.** `catalog_version` and `pricing_version` are
  integers naming published, frozen snapshots. Publishing a new catalog version never mutates
  an existing subscription; publishing new pricing never mutates one either. That is precisely
  what M9.6 and M9.7 made immutable *for*, and it is why an invoice raised two years ago can
  still be explained from the agreement that produced it.
* **A subscription is never deleted.** The lifecycle below has **no edge into any removal
  state at all** — stronger than the catalog, pricing, or account lifecycles, and deliberately
  so. A subscription is the commercial agreement itself; ADR-0023 RS4 makes its record
  permanent, and `archived` is as far out of the working set as one can go.

Renewal is deliberately **not** an FSM edge. Renewing advances the billing cycle of an active
subscription; it does not change what state the agreement is in. Modelling it as a self-loop
would have put a period computation inside a state machine, where nobody would look for it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum

from core.fsm import StateMachineDefinition, define

from .domain import BillingInterval, BillingPeriod
from .money import Currency
from .subscription import ChangeTiming, ProrationBehaviour

# ── lifecycle ───────────────────────────────────────────────────────────────────


class AgreementState(StrEnum):
    """The commercial lifecycle of a subscription.

    Named `AgreementState` rather than `SubscriptionState` because M9.2 owns the latter and is
    frozen — the same call M9.6 made for `Product` beside `Plan`."""

    DRAFT = "draft"
    TRIALING = "trialing"
    ACTIVE = "active"
    PAST_DUE = "past_due"
    SUSPENDED = "suspended"
    PAUSED = "paused"
    CANCELLED = "cancelled"
    EXPIRED = "expired"
    ARCHIVED = "archived"


class AgreementTrigger(StrEnum):
    ACTIVATE = "activate"
    START_TRIAL = "start_trial"
    CONVERT_TRIAL = "convert_trial"
    EXPIRE_TRIAL = "expire_trial"
    PAYMENT_FAILED = "payment_failed"
    PAYMENT_RECOVERED = "payment_recovered"
    SUSPEND = "suspend"
    REACTIVATE = "reactivate"
    PAUSE = "pause"
    RESUME = "resume"
    CANCEL = "cancel"
    EXPIRE = "expire"
    ARCHIVE = "archive"


# The subscription lifecycle. Five absences, each a decision:
#
#   * **no edge reaches a deletion state, because there is no deletion state.** A subscription
#     *is* the commercial agreement; ADR-0023 RS4 makes the record permanent. `archived` is the
#     end of the line, and it is still fully readable;
#   * there is no CANCELLED → ACTIVE edge. Winning a customer back is a *new* agreement, not a
#     resurrection of the one they ended — and conflating the two makes churn unmeasurable;
#   * there is no PAUSED → SUSPENDED edge. Pause is the customer's choice, suspension is ours,
#     and a path between them would blur which of the two actually happened;
#   * there is no edge out of ARCHIVED, which is what makes it terminal;
#   * **renewal is absent entirely** — it advances the billing cycle of an ACTIVE subscription
#     rather than changing its state. See `BillingCycleService.renew`.
AGREEMENT_LIFECYCLE: StateMachineDefinition = define(
    [
        (AgreementState.DRAFT, AgreementTrigger.ACTIVATE, AgreementState.ACTIVE),
        (AgreementState.DRAFT, AgreementTrigger.START_TRIAL, AgreementState.TRIALING),
        (AgreementState.DRAFT, AgreementTrigger.CANCEL, AgreementState.CANCELLED),
        (AgreementState.TRIALING, AgreementTrigger.CONVERT_TRIAL, AgreementState.ACTIVE),
        (AgreementState.TRIALING, AgreementTrigger.EXPIRE_TRIAL, AgreementState.EXPIRED),
        (AgreementState.TRIALING, AgreementTrigger.CANCEL, AgreementState.CANCELLED),
        (AgreementState.ACTIVE, AgreementTrigger.PAYMENT_FAILED, AgreementState.PAST_DUE),
        (AgreementState.ACTIVE, AgreementTrigger.SUSPEND, AgreementState.SUSPENDED),
        (AgreementState.ACTIVE, AgreementTrigger.PAUSE, AgreementState.PAUSED),
        (AgreementState.ACTIVE, AgreementTrigger.CANCEL, AgreementState.CANCELLED),
        (AgreementState.PAST_DUE, AgreementTrigger.PAYMENT_RECOVERED, AgreementState.ACTIVE),
        (AgreementState.PAST_DUE, AgreementTrigger.SUSPEND, AgreementState.SUSPENDED),
        (AgreementState.PAST_DUE, AgreementTrigger.CANCEL, AgreementState.CANCELLED),
        (AgreementState.SUSPENDED, AgreementTrigger.REACTIVATE, AgreementState.ACTIVE),
        (AgreementState.SUSPENDED, AgreementTrigger.CANCEL, AgreementState.CANCELLED),
        (AgreementState.PAUSED, AgreementTrigger.RESUME, AgreementState.ACTIVE),
        (AgreementState.PAUSED, AgreementTrigger.CANCEL, AgreementState.CANCELLED),
        (AgreementState.CANCELLED, AgreementTrigger.EXPIRE, AgreementState.EXPIRED),
        (AgreementState.EXPIRED, AgreementTrigger.ARCHIVE, AgreementState.ARCHIVED),
    ]
)

#: States in which the agreement is live — the customer is receiving what they bought.
LIVE_AGREEMENT_STATES: frozenset[AgreementState] = frozenset(
    {AgreementState.TRIALING, AgreementState.ACTIVE}
)
#: States in which the agreement exists but is impaired. Entitlement *decisions* about these
#: belong to the Entitlement domain; this set only records that the distinction exists.
IMPAIRED_AGREEMENT_STATES: frozenset[AgreementState] = frozenset(
    {AgreementState.PAST_DUE, AgreementState.SUSPENDED, AgreementState.PAUSED}
)
#: States after which nothing commercial happens again on this agreement.
CLOSED_AGREEMENT_STATES: frozenset[AgreementState] = frozenset(
    {AgreementState.CANCELLED, AgreementState.EXPIRED, AgreementState.ARCHIVED}
)


# ── items ───────────────────────────────────────────────────────────────────────


class ItemKind(StrEnum):
    """The commercial shape of one subscribed component.

    Deliberately parallel to — not identical with — M9.7's `PriceShape`. A `PriceShape` says
    how a price is *structured*; an `ItemKind` says what the customer *subscribed to*. They
    usually agree, and when they do not it is because a customer bought a hybrid line whose
    price shape resolves per component."""

    FLAT = "flat"
    SEAT = "seat"
    USAGE = "usage"
    TIERED = "tiered"
    VOLUME = "volume"
    HYBRID = "hybrid"


@dataclass(frozen=True, slots=True)
class AgreementItem:
    """One subscribed component, pinned to a price code **and version**.

    Carries no `Money`. It names the price and the quantity; what that costs is resolved
    against the pinned pricing version by the Pricing Engine, and what it *bills* is computed
    by rating. Several items on one subscription is how hybrid commerce is expressed —
    recurring plus seats plus usage — rather than several subscriptions per customer."""

    id: str
    price_code: str
    price_version: int = 1
    kind: ItemKind = ItemKind.FLAT
    quantity: int = 1
    meter_code: str = ""
    included_units: int = 0
    label: str = ""
    added_at: float = 0.0

    @property
    def is_metered(self) -> bool:
        return self.kind in (ItemKind.USAGE, ItemKind.TIERED, ItemKind.VOLUME) or bool(
            self.meter_code
        )

    @property
    def is_seat_based(self) -> bool:
        return self.kind is ItemKind.SEAT

    @property
    def pin(self) -> str:
        """The stable identity of what was subscribed to: `code@version`."""
        return f"{self.price_code}@{self.price_version}"


# ── policies ────────────────────────────────────────────────────────────────────


class RenewalMode(StrEnum):
    AUTO = "auto"
    MANUAL = "manual"
    NONE = "none"  # a fixed-term agreement that simply ends


@dataclass(frozen=True, slots=True)
class RenewalPolicy:
    """What *should* happen at the end of a term. Intent only — nothing here executes.

    There is no scheduler in this domain by design: a renewal engine that lived inside the
    subscription aggregate would make "what did we agree?" and "what did we run?" the same
    record, and they must be separable when one of them goes wrong."""

    mode: RenewalMode = RenewalMode.AUTO
    notice_days: int = 30
    terms: int | None = None  # None ⇒ perpetual; N ⇒ renews at most N times
    terms_used: int = 0
    next_renewal_at: float | None = None

    @property
    def renews(self) -> bool:
        return self.mode is not RenewalMode.NONE and (
            self.terms is None or self.terms_used < self.terms
        )

    @property
    def is_automatic(self) -> bool:
        return self.mode is RenewalMode.AUTO and self.renews


@dataclass(frozen=True, slots=True)
class TrialPolicy:
    """One deterministic trial policy: a length, an end, and what happens at it.

    `requires_payment_method` is a **declared** requirement that a consuming surface enforces
    at conversion. Nothing here talks to a payment provider — this domain has none."""

    enabled: bool = False
    length_days: int = 0
    ends_at: float | None = None
    converts_automatically: bool = True
    requires_payment_method: bool = False
    extension_allowed: bool = False

    def is_expired(self, at: float) -> bool:
        return self.ends_at is not None and at >= self.ends_at

    def end_from(self, start: float) -> float:
        return start + self.length_days * 86_400.0


@dataclass(frozen=True, slots=True)
class PausePolicy:
    """How long, and how often, a customer may pause.

    `entitlements_retained` defaults to **True** and is a statement, not a mechanism: pausing a
    subscription does not by itself alter what the customer is entitled to. The Entitlement
    domain decides entitlement; this flag records what was agreed so that decision has an
    input rather than an assumption."""

    max_pause_days: int = 90
    pauses_per_term: int = 2
    pauses_used: int = 0
    entitlements_retained: bool = True
    paused_at: float | None = None
    resume_at: float | None = None

    @property
    def pauses_remaining(self) -> int:
        return max(self.pauses_per_term - self.pauses_used, 0)

    def may_pause(self) -> bool:
        return self.pauses_remaining > 0


class CancellationTiming(StrEnum):
    IMMEDIATE = "immediate"
    PERIOD_END = "period_end"
    SCHEDULED = "scheduled"


@dataclass(frozen=True, slots=True)
class CancellationPolicy:
    """When a cancellation takes effect, and what was recorded about it.

    `refund_eligible` is a **declared flag** that the Refund domain (M9.16) reads. No refund is
    computed, authorized, or issued here."""

    default_timing: CancellationTiming = CancellationTiming.PERIOD_END
    notice_days: int = 0
    refund_eligible: bool = False
    reason: str = ""
    reason_code: str = ""
    requested_at: float | None = None
    effective_at: float | None = None

    @property
    def is_requested(self) -> bool:
        return self.requested_at is not None


@dataclass(frozen=True, slots=True)
class UpgradePolicy:
    """Upgrades default to immediate: a customer who has asked for more should get it now."""

    timing: ChangeTiming = ChangeTiming.IMMEDIATE
    proration: ProrationBehaviour = ProrationBehaviour.CHARGE_REMAINDER
    allow_mid_period: bool = True
    requires_approval: bool = False


@dataclass(frozen=True, slots=True)
class DowngradePolicy:
    """Downgrades default to period end, so a customer never loses capability they have
    already paid for (Blueprint §6.3)."""

    timing: ChangeTiming = ChangeTiming.PERIOD_END
    proration: ProrationBehaviour = ProrationBehaviour.CREDIT_REMAINDER
    allow_mid_period: bool = False
    requires_approval: bool = False


# ── billing cycle ───────────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class BillingCycle:
    """Where the subscription is in its recurring term.

    `cycle_number` counts terms elapsed, which is what makes "this is their third year" a fact
    rather than a date subtraction. `anchor_day` keeps the billing date stable across months
    and is capped at 28 for the same reason M9.8 caps it: 28 is the last day every month
    has."""

    interval: BillingInterval = BillingInterval.MONTHLY
    anchor_day: int = 1
    cycle_number: int = 1
    current_period: BillingPeriod | None = None

    @property
    def has_period(self) -> bool:
        return self.current_period is not None

    def covers(self, at: float) -> bool:
        period = self.current_period
        return period is not None and period.start <= at < period.end


# ── scheduled changes ───────────────────────────────────────────────────────────


class ScheduledChangeKind(StrEnum):
    UPGRADE = "upgrade"
    DOWNGRADE = "downgrade"
    CANCELLATION = "cancellation"
    RENEWAL = "renewal"
    PRICING_SWITCH = "pricing_switch"


class ScheduledChangeStatus(StrEnum):
    PENDING = "pending"
    APPLIED = "applied"
    CANCELLED = "cancelled"


@dataclass(frozen=True, slots=True)
class ScheduledSubscriptionChange:
    """An accepted intention to change the agreement at a future instant.

    **Intent only.** Nothing in this domain fires it: there is no scheduler, no timer, and no
    background worker. A change carries what was agreed and when it should take effect; the
    surface that later applies it dispatches an ordinary command and the FSM validates it then,
    exactly as if a human had asked. That separation is what keeps "what we agreed" auditable
    independently of "what the system managed to run"."""

    id: str
    kind: ScheduledChangeKind
    effective_at: float
    status: ScheduledChangeStatus = ScheduledChangeStatus.PENDING
    target_catalog_version: int | None = None
    target_pricing_version: int | None = None
    target_plan_code: str = ""
    items: tuple[AgreementItem, ...] = ()
    timing: ChangeTiming = ChangeTiming.PERIOD_END
    reason: str = ""
    requested_by: str = ""
    requested_at: float = 0.0
    applied_at: float | None = None

    @property
    def is_pending(self) -> bool:
        return self.status is ScheduledChangeStatus.PENDING

    @property
    def is_commercial(self) -> bool:
        """Whether applying this change would re-pin the agreement's commercial references."""
        return self.target_catalog_version is not None or self.target_pricing_version is not None

    def is_due(self, at: float) -> bool:
        return self.is_pending and at >= self.effective_at


# ── the subscription ────────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class CommercialSubscription:
    """The commercial agreement between a billing account and VoxVoice.

    Three references, all immutable once set, all naming *frozen* things:

    * `account_id` — an M9.8 `CommercialAccount`;
    * `catalog_version` — a published, self-contained M9.6 catalog snapshot;
    * `pricing_version` — a published, immutable M9.7 pricing snapshot.

    Because all three are versioned snapshots rather than live objects, changing the catalog
    or the pricing cannot reach backwards into an existing subscription. Moving a subscription
    onto newer commercial terms is an explicit act that publishes a new subscription *version*
    — never an edit of the one an invoice was raised against.

    Carries no `Money`. What this agreement costs is resolved against `pricing_version`; what
    it bills is rating's."""

    id: str
    org_id: str
    account_id: str
    subscription_number: str
    catalog_version: int
    pricing_version: int
    currency: Currency
    plan_code: str = ""
    state: AgreementState = AgreementState.DRAFT
    version: int = 1
    items: tuple[AgreementItem, ...] = ()
    cycle: BillingCycle = field(default_factory=BillingCycle)
    trial: TrialPolicy = field(default_factory=TrialPolicy)
    renewal: RenewalPolicy = field(default_factory=RenewalPolicy)
    pause: PausePolicy = field(default_factory=PausePolicy)
    cancellation: CancellationPolicy = field(default_factory=CancellationPolicy)
    upgrade: UpgradePolicy = field(default_factory=UpgradePolicy)
    downgrade: DowngradePolicy = field(default_factory=DowngradePolicy)
    scheduled_changes: tuple[ScheduledSubscriptionChange, ...] = ()
    contract_id: str = ""  # set ⇒ contract-sourced; the machinery is identical
    created_at: float = 0.0
    updated_at: float = 0.0
    started_at: float | None = None
    cancelled_at: float | None = None
    ended_at: float | None = None
    archived_at: float | None = None

    # ── state ───────────────────────────────────────────────────────────────────

    @property
    def is_live(self) -> bool:
        return self.state in LIVE_AGREEMENT_STATES

    @property
    def is_impaired(self) -> bool:
        return self.state in IMPAIRED_AGREEMENT_STATES

    @property
    def is_closed(self) -> bool:
        return self.state in CLOSED_AGREEMENT_STATES

    @property
    def is_trialing(self) -> bool:
        return self.state is AgreementState.TRIALING

    @property
    def is_editable(self) -> bool:
        """A closed agreement is a historical record. Its terms are what was agreed, and
        editing them after it ended would change the record of a finished relationship."""
        return not self.is_closed

    @property
    def is_terminal(self) -> bool:
        return self.state is AgreementState.ARCHIVED

    # ── commercial identity ─────────────────────────────────────────────────────

    @property
    def commercial_pin(self) -> tuple[int, int]:
        """The pair that makes this agreement reproducible: `(catalog, pricing)`."""
        return (self.catalog_version, self.pricing_version)

    @property
    def seat_count(self) -> int:
        """Total seats across seat-based items — an input other domains consume, never a
        charge this one computes."""
        return sum(i.quantity for i in self.items if i.is_seat_based)

    @property
    def metered_codes(self) -> tuple[str, ...]:
        return tuple(sorted({i.meter_code for i in self.items if i.meter_code}))

    @property
    def price_pins(self) -> tuple[str, ...]:
        return tuple(sorted(i.pin for i in self.items))

    @property
    def item_kinds(self) -> tuple[ItemKind, ...]:
        return tuple(sorted({i.kind for i in self.items}))

    @property
    def is_hybrid(self) -> bool:
        """More than one commercial shape on one agreement — the case that exists because
        real customers buy a platform fee *and* seats *and* usage together."""
        return len(self.item_kinds) > 1

    def item(self, item_id: str) -> AgreementItem | None:
        return next((i for i in self.items if i.id == item_id), None)

    def item_for_price(self, price_code: str) -> AgreementItem | None:
        return next((i for i in self.items if i.price_code == price_code), None)

    # ── scheduling ──────────────────────────────────────────────────────────────

    @property
    def pending_changes(self) -> tuple[ScheduledSubscriptionChange, ...]:
        return tuple(
            sorted(
                (c for c in self.scheduled_changes if c.is_pending),
                key=lambda c: (c.effective_at, c.id),
            )
        )

    def due_changes(self, at: float) -> tuple[ScheduledSubscriptionChange, ...]:
        """What *would* be applied at this instant. A read — this domain applies nothing on
        its own, so calling it has no effect."""
        return tuple(c for c in self.pending_changes if c.is_due(at))

    def scheduled_change(self, change_id: str) -> ScheduledSubscriptionChange | None:
        return next((c for c in self.scheduled_changes if c.id == change_id), None)


# ── versions, snapshots, history ────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class SubscriptionVersion:
    """An immutable snapshot of the agreement at a commercial change.

    Self-contained, like a catalog or pricing version: it embeds the whole subscription as it
    was, so reading version 3 never depends on what version 7 did. That is what makes "what
    exactly had this customer agreed to when we raised that invoice?" answerable from one row.

    `rolled_back_from` records provenance when this version restored an earlier one. History
    is appended to; nothing is rewritten (§A5)."""

    id: str
    org_id: str
    subscription_id: str
    version: int
    published_at: float = 0.0
    published_by: str = ""
    reason: str = ""
    rolled_back_from: int | None = None
    subscription: CommercialSubscription | None = None

    @property
    def is_rollback(self) -> bool:
        return self.rolled_back_from is not None

    @property
    def commercial_pin(self) -> tuple[int, int] | None:
        return self.subscription.commercial_pin if self.subscription else None

    @property
    def item_count(self) -> int:
        return len(self.subscription.items) if self.subscription else 0


@dataclass(frozen=True, slots=True)
class SubscriptionSnapshot:
    """What agreement existed at one instant — the composed read model.

    Derived, never stored: everything in it comes from the subscription and the version line,
    so there is no second copy of a commercial fact to fall out of date (§A2.5)."""

    subscription: CommercialSubscription
    at: float = 0.0
    version: int = 1
    catalog_version: int = 0
    pricing_version: int = 0
    account_id: str = ""
    state: AgreementState = AgreementState.DRAFT
    items: tuple[AgreementItem, ...] = ()
    seat_count: int = 0
    metered_codes: tuple[str, ...] = ()
    pending_change_count: int = 0
    in_period: bool = False

    @property
    def is_reproducible(self) -> bool:
        """A snapshot can be re-priced exactly when both of its pins name real versions."""
        return self.catalog_version > 0 and self.pricing_version > 0


class SubscriptionAction(StrEnum):
    """What happened to a subscription. The activity log's closed vocabulary."""

    CREATED = "subscription.created"
    ACTIVATED = "subscription.activated"
    TRIAL_STARTED = "subscription.trial_started"
    TRIAL_CONVERTED = "subscription.trial_converted"
    TRIAL_EXPIRED = "subscription.trial_expired"
    PAYMENT_FAILED = "subscription.payment_failed"
    PAYMENT_RECOVERED = "subscription.payment_recovered"
    SUSPENDED = "subscription.suspended"
    REACTIVATED = "subscription.reactivated"
    PAUSED = "subscription.paused"
    RESUMED = "subscription.resumed"
    CANCELLED = "subscription.cancelled"
    EXPIRED = "subscription.expired"
    ARCHIVED = "subscription.archived"
    RENEWED = "subscription.renewed"
    ITEM_ADDED = "subscription.item_added"
    ITEM_UPDATED = "subscription.item_updated"
    ITEM_REMOVED = "subscription.item_removed"
    CHANGE_SCHEDULED = "subscription.change_scheduled"
    CHANGE_CANCELLED = "subscription.change_cancelled"
    UPGRADED = "subscription.upgraded"
    DOWNGRADED = "subscription.downgraded"
    RENEWAL_POLICY_CHANGED = "subscription.renewal_policy_changed"
    VERSION_PUBLISHED = "subscription.version_published"
    ROLLED_BACK = "subscription.rolled_back"


@dataclass(frozen=True, slots=True)
class SubscriptionActivity:
    """One append-only entry in a subscription's own history: who changed what, when.

    Kept alongside the `billing.*` fact stream rather than instead of it — the stream is the
    platform-wide log; this is the agreement's ordered, queryable timeline, and a subscription
    screen should not have to filter the whole revenue stream to render it."""

    id: str
    org_id: str
    subscription_id: str
    action: SubscriptionAction
    actor_id: str = ""
    at: float = 0.0
    from_state: str = ""
    to_state: str = ""
    version: int = 1
    detail: str = ""


@dataclass(frozen=True, slots=True)
class SubscriptionHistory:
    """The whole story of one agreement: its ordered activity and its version line.

    Two sequences rather than one because they answer different questions — the activity says
    *what happened*, the versions say *what the terms were*. A support conversation needs the
    first; a disputed invoice needs the second."""

    subscription_id: str
    activity: tuple[SubscriptionActivity, ...] = ()
    versions: tuple[SubscriptionVersion, ...] = ()

    @property
    def event_count(self) -> int:
        return len(self.activity)

    @property
    def version_count(self) -> int:
        return len(self.versions)

    @property
    def current_version(self) -> int:
        return max((v.version for v in self.versions), default=1)

    def version_at(self, at: float) -> SubscriptionVersion | None:
        """The terms in force at an instant — the question a re-priced invoice actually
        asks."""
        covering = [v for v in self.versions if v.published_at <= at]
        return max(covering, key=lambda v: v.version) if covering else None


# ── search ──────────────────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class SubscriptionSearchQuery:
    """A deterministic filter. No relevance scoring and no index — the same subscriptions and
    the same query always come back in the same order, which is what a paged admin screen and
    a reproducible test both need."""

    text: str = ""  # case-insensitive substring over number, plan code, price pins
    states: tuple[AgreementState, ...] = ()
    account_id: str = ""
    plan_code: str = ""
    currencies: tuple[Currency, ...] = ()
    intervals: tuple[BillingInterval, ...] = ()
    catalog_version: int | None = None
    pricing_version: int | None = None
    meter_code: str = ""
    live_only: bool = False
    trialing_only: bool = False
    with_pending_changes: bool = False
    renews_automatically: bool | None = None
    active_at: float | None = None
    limit: int = 100
    offset: int = 0


@dataclass(frozen=True, slots=True)
class SubscriptionSearchResult:
    """A page of matches plus the unpaged total, so a caller can render "20 of 137"."""

    subscriptions: tuple[CommercialSubscription, ...] = ()
    total: int = 0
