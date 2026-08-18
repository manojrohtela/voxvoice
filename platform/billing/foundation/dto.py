"""Foundation DTOs (M9.2) — immutable request/result shapes.

The inputs factories and services take, and the results they return. Deliberately *not*
API models: no HTTP, no serialization, no framework. The control-plane layers (M9.3+)
translate their own transport models into these, exactly as Epic 8 does.

Every DTO is frozen; every result carries its diagnostics so a caller can inspect
warnings even on success (§A2.6 — accepted configuration must act, and rejected
configuration must say why).
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .accounts import BillingAccount, BillingAddress, BillingContact
from .catalog import MeterAggregation, MeterSource, PlanFeature
from .diagnostics import BillingDiagnostics
from .domain import AccountKind, BillingInterval, BillingPeriod, Scope, Tier
from .entitlement import EntitlementSnapshot
from .invoicing import ChargeLine, Invoice, InvoiceTotals
from .money import Currency, Money, Rounding
from .subscription import ProrationOutcome, Subscription, SubscriptionItem

# ── requests ────────────────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class OpenAccountRequest:
    """Open a billing account for an organization the Customer Platform already owns.
    `org_id` is a reference — Billing writes no organization fact (ADR-0023 §5.2)."""

    org_id: str
    display_name: str
    currency: Currency
    kind: AccountKind = AccountKind.DIRECT
    parent_account_id: str = ""
    contact: BillingContact = BillingContact()
    address: BillingAddress = BillingAddress()


@dataclass(frozen=True, slots=True)
class DefineMeterRequest:
    code: str
    display_name: str
    unit: str
    aggregation: MeterAggregation
    source: MeterSource
    rounding: Rounding = Rounding.HALF_UP
    org_id: str = ""  # "" = platform-wide meter
    description: str = ""


@dataclass(frozen=True, slots=True)
class DefinePlanRequest:
    code: str
    display_name: str
    tier: Tier
    features: tuple[PlanFeature, ...] = ()
    metered_codes: tuple[str, ...] = ()
    org_id: str = ""  # set ⇒ contract-scoped (enterprise) plan
    description: str = ""


@dataclass(frozen=True, slots=True)
class StartSubscriptionRequest:
    org_id: str
    account_id: str
    plan_code: str
    plan_version: int
    price_book_id: str
    price_book_version: int
    currency: Currency
    interval: BillingInterval
    items: tuple[SubscriptionItem, ...] = ()
    trial_seconds: int = 0
    contract_id: str = ""


@dataclass(frozen=True, slots=True)
class RecordUsageRequest:
    """One decoded `metric.*` observation. `event_id` is the idempotency key — Billing
    derives a usage record from it and **never measures anything itself** (M1/M4)."""

    org_id: str
    account_id: str
    meter_code: str
    units: int
    unit: str
    scope: Scope
    period: BillingPeriod
    event_id: str
    event_type: str
    observed_at: float
    subscription_id: str = ""


@dataclass(frozen=True, slots=True)
class IssueInvoiceRequest:
    org_id: str
    account_id: str
    currency: Currency
    period: BillingPeriod
    lines: tuple[ChargeLine, ...] = ()
    subscription_id: str = ""
    entity_code: str = "voxvoice"
    jurisdiction: str = ""
    payment_terms_days: int = 0
    purchase_order_ref: str = ""


@dataclass(frozen=True, slots=True)
class ChangePlanRequest:
    """A plan or seat change. The *timing* and the *financial behaviour* are decided by
    the proration service and reported back — never assumed by the caller."""

    org_id: str
    subscription_id: str
    new_plan_code: str = ""
    new_plan_version: int = 0
    new_items: tuple[SubscriptionItem, ...] = ()
    at: float = 0.0
    acknowledge_reduction: bool = False  # explicit consent for a contracting change


# ── results ─────────────────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class AccountResult:
    account: BillingAccount
    diagnostics: BillingDiagnostics = field(default_factory=BillingDiagnostics.empty)


@dataclass(frozen=True, slots=True)
class SubscriptionResult:
    subscription: Subscription
    diagnostics: BillingDiagnostics = field(default_factory=BillingDiagnostics.empty)


@dataclass(frozen=True, slots=True)
class SnapshotResult:
    snapshot: EntitlementSnapshot
    diagnostics: BillingDiagnostics = field(default_factory=BillingDiagnostics.empty)


@dataclass(frozen=True, slots=True)
class InvoiceResult:
    invoice: Invoice
    totals: InvoiceTotals
    diagnostics: BillingDiagnostics = field(default_factory=BillingDiagnostics.empty)


@dataclass(frozen=True, slots=True)
class PlanChangeResult:
    """The full, inspectable outcome of a plan/seat change: what will happen, when, what
    it costs, and — when a change would contract capability the customer is using —
    whether it was accepted at all (Blueprint §6.3)."""

    accepted: bool
    outcome: ProrationOutcome
    adjustment: Money | None = None
    diagnostics: BillingDiagnostics = field(default_factory=BillingDiagnostics.empty)
