"""Subscriptions (M9.8 domain) — state, term, renewal, proration.

ADR-0023 §5.1. The subscription is the spine every other commercial fact resolves
against: entitlement, rating, and invoicing all attach to a subscription *and its
period*, never to "now" (Blueprint §6.2). The lifecycle is a deterministic, table-driven
FSM (`lifecycle.SUBSCRIPTION_LIFECYCLE`) driven by an injected clock — that combination is
what makes a billing run reproducible.

Enterprise is **not** a separate path: an ENTERPRISE subscription simply pins to a
contract-sourced plan and price book. Everything downstream is identical.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from .domain import BillingInterval, BillingPeriod
from .money import Currency


class SubscriptionState(StrEnum):
    """Blueprint §6.2. Terminal states are immutable (the `core.fsm` discipline)."""

    DRAFT = "draft"
    PENDING = "pending"
    TRIALING = "trialing"
    ACTIVE = "active"
    PAST_DUE = "past_due"
    SUSPENDED = "suspended"
    PAUSED = "paused"
    SCHEDULED_CHANGE = "scheduled_change"
    CANCELLED = "cancelled"
    EXPIRED = "expired"


class SubscriptionTrigger(StrEnum):
    """The only ways a subscription may move. Anything not in the table is rejected."""

    SUBMIT = "submit"
    START_TRIAL = "start_trial"
    ACTIVATE = "activate"
    CONVERT_TRIAL = "convert_trial"
    SCHEDULE_CHANGE = "schedule_change"
    APPLY_SCHEDULED_CHANGE = "apply_scheduled_change"
    PAYMENT_FAILED = "payment_failed"
    PAYMENT_RECOVERED = "payment_recovered"
    EXHAUST_DUNNING = "exhaust_dunning"
    REACTIVATE = "reactivate"
    PAUSE = "pause"
    RESUME = "resume"
    CANCEL = "cancel"
    EXPIRE = "expire"


class ChangeTiming(StrEnum):
    """When a plan/seat change takes effect. Downgrades default to period end so a
    customer never loses capability they have already paid for (Blueprint §6.3)."""

    IMMEDIATE = "immediate"
    PERIOD_END = "period_end"


class ProrationBehaviour(StrEnum):
    """How the financial side of a mid-period change is settled. An immediate downgrade
    issues a credit — never a silent refund (Blueprint §6.3)."""

    NONE = "none"
    CHARGE_REMAINDER = "charge_remainder"
    CREDIT_REMAINDER = "credit_remainder"


#: States in which a subscription confers entitlements at full strength.
FULL_ENTITLEMENT_STATES: frozenset[SubscriptionState] = frozenset(
    {SubscriptionState.TRIALING, SubscriptionState.ACTIVE, SubscriptionState.SCHEDULED_CHANGE}
)
#: States in which entitlements are contracted but customer data is untouched (RS/PY8).
CONTRACTED_ENTITLEMENT_STATES: frozenset[SubscriptionState] = frozenset(
    {SubscriptionState.PAST_DUE, SubscriptionState.PAUSED, SubscriptionState.SUSPENDED}
)
#: Terminal states — immutable once reached.
TERMINAL_SUBSCRIPTION_STATES: frozenset[SubscriptionState] = frozenset({SubscriptionState.EXPIRED})


@dataclass(frozen=True, slots=True)
class SubscriptionItem:
    """One priced component of a subscription. Several items on one subscription is how
    hybrid pricing (recurring + seats + usage + credits) is expressed — not multiple
    subscriptions per customer."""

    price_code: str
    price_version: int
    quantity: int = 1  # seats for PER_SEAT; 1 for flat/metered
    meter_code: str = ""


@dataclass(frozen=True, slots=True)
class ScheduledChange:
    """A change that has been accepted but takes effect at period end. Held explicitly so
    the customer, the invoice, and the audit trail all see the same pending future."""

    plan_code: str = ""
    plan_version: int = 0
    items: tuple[SubscriptionItem, ...] = ()
    effective_at: float = 0.0
    cancel_at_period_end: bool = False


@dataclass(frozen=True, slots=True)
class Subscription:
    """The commercial agreement between a billing account and a plan version.

    Pinning is deliberate and load-bearing: `plan_version` and `price_book_version` fix
    what this subscription is rated against, so a later catalog or price change cannot
    retroactively alter an issued invoice (ADR-0023 RS2, Blueprint §8.2)."""

    id: str
    org_id: str
    account_id: str
    plan_code: str
    plan_version: int
    price_book_id: str
    price_book_version: int
    currency: Currency
    interval: BillingInterval
    state: SubscriptionState = SubscriptionState.DRAFT
    items: tuple[SubscriptionItem, ...] = ()
    current_period: BillingPeriod | None = None
    trial_end: float | None = None
    contract_id: str = ""  # set ⇒ contract-sourced (enterprise); machinery unchanged
    scheduled_change: ScheduledChange | None = None
    started_at: float | None = None
    cancelled_at: float | None = None
    ended_at: float | None = None

    # ── derived, read-only ──────────────────────────────────────────────────────
    @property
    def is_trialing(self) -> bool:
        return self.state is SubscriptionState.TRIALING

    @property
    def is_terminal(self) -> bool:
        return self.state in TERMINAL_SUBSCRIPTION_STATES

    @property
    def confers_full_entitlement(self) -> bool:
        return self.state in FULL_ENTITLEMENT_STATES

    @property
    def is_contract_sourced(self) -> bool:
        return bool(self.contract_id)

    @property
    def seat_count(self) -> int:
        """Total seats across non-metered items — the input seat-based rating consumes."""
        return sum(i.quantity for i in self.items if not i.meter_code)

    def item(self, price_code: str) -> SubscriptionItem | None:
        return next((i for i in self.items if i.price_code == price_code), None)

    def metered_codes(self) -> tuple[str, ...]:
        return tuple(sorted({i.meter_code for i in self.items if i.meter_code}))


@dataclass(frozen=True, slots=True)
class ProrationOutcome:
    """The deterministic result of a mid-period change: what was decided, and why.

    Returned by the pure proration service so the decision is inspectable *before* any
    money is computed — a downgrade that would violate current consumption is rejected or
    explicitly acknowledged here, never silently accepted and enforced later by deleting
    customer resources (Blueprint §6.3)."""

    timing: ChangeTiming
    behaviour: ProrationBehaviour
    elapsed_seconds: float
    remaining_seconds: float
    numerator: int
    denominator: int
    reason: str = ""

    @property
    def is_prorated(self) -> bool:
        return self.behaviour is not ProrationBehaviour.NONE
