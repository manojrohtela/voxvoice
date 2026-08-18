"""Commands (M9.3) — immutable client intent, one per orchestrated operation.

Commands carry *intent and identity*, never behaviour. Every command names its `actor_id`
and `org_id` so the bus can audit uniformly and the authorization orchestrator can put a
well-formed question to the seam.

Coverage note (honest): commands exist for the operations the M9.2 Foundation actually
supports. Where a bounded context's domain milestone has not landed yet (rating M9.11,
payment execution M9.14, tax determination M9.15), the command orchestrates only what the
Foundation can do today — recording a validated aggregate — and gains richer behaviour when
that milestone extends the Foundation. The Application invents no rule in the meantime.
"""

from __future__ import annotations

from dataclasses import dataclass

from billing.foundation import (
    AccountKind,
    BillingAddress,
    BillingContact,
    BillingInterval,
    ChargeLine,
    CreditKind,
    Currency,
    Entitlement,
    EntitlementGate,
    LedgerDirection,
    LedgerEntryType,
    LicenseKind,
    MeterAggregation,
    MeterSource,
    Money,
    PaymentMethodKind,
    Plan,
    PlanFeature,
    Price,
    RefundReason,
    Rounding,
    Scope,
    SubscriptionItem,
    SubscriptionTrigger,
    TaxProfile,
    Tier,
)
from billing.foundation.domain import BillingPeriod


@dataclass(frozen=True, slots=True)
class _Command:
    """Common shape: who is acting, and in which organization."""

    actor_id: str
    org_id: str


# ── billing accounts (M9.5) ─────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class OpenBillingAccount(_Command):
    display_name: str
    currency: Currency
    kind: AccountKind = AccountKind.DIRECT
    parent_account_id: str = ""
    contact: BillingContact = BillingContact()
    address: BillingAddress = BillingAddress()


@dataclass(frozen=True, slots=True)
class UpdateBillingProfile(_Command):
    account_id: str
    contact: BillingContact = BillingContact()
    address: BillingAddress = BillingAddress()


@dataclass(frozen=True, slots=True)
class CloseBillingAccount(_Command):
    """Closing retains the account and all revenue records (ADR-0023 RS4)."""

    account_id: str
    reason: str = ""


# ── commercial catalog (M9.6) ───────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class DefineMeter(_Command):
    code: str
    display_name: str
    unit: str
    aggregation: MeterAggregation
    source: MeterSource
    #: `None` ⇒ the Foundation's declared default applies. The command forwards a choice;
    #: it never restates a policy default (that would be a rule living in two places).
    rounding: Rounding | None = None


@dataclass(frozen=True, slots=True)
class DefinePlan(_Command):
    code: str
    display_name: str
    tier: Tier
    features: tuple[PlanFeature, ...] = ()
    metered_codes: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class PublishPlan(_Command):
    plan: Plan


# ── pricing (M9.7) ──────────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class PublishPriceBook(_Command):
    code: str
    display_name: str
    currency: Currency
    prices: tuple[Price, ...] = ()


# ── subscriptions (M9.8) ────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class StartSubscription(_Command):
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
class TransitionSubscription(_Command):
    """Every subscription movement — activate, convert, cancel, pause, resume, past-due,
    recover, suspend, expire — is this one command carrying a Foundation trigger. The
    legality of the move is decided by the Foundation FSM, never here."""

    subscription_id: str
    trigger: SubscriptionTrigger


@dataclass(frozen=True, slots=True)
class ChangeSubscriptionPlan(_Command):
    subscription_id: str
    is_expansion: bool
    immediate_downgrade: bool = False
    price_delta: Money | None = None
    at: float | None = None


# ── entitlements (M9.9) ─────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class GrantEntitlement(_Command):
    account_id: str
    entitlement: Entitlement
    source_ref: str = ""


@dataclass(frozen=True, slots=True)
class RevokeEntitlement(_Command):
    grant_id: str


@dataclass(frozen=True, slots=True)
class PublishEntitlementSnapshot(_Command):
    account_id: str
    gates: tuple[EntitlementGate, ...]
    subscription_id: str = ""
    max_staleness_seconds: float = 3600.0


# ── usage / metering (M9.10) ────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class RecordUsage(_Command):
    """Ingest one decoded `metric.*` observation. Idempotent by `event_id`: re-ingesting
    the same source event is a no-op (ADR-0023 M4)."""

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
class QuarantineUsage(_Command):
    account_id: str
    meter_code: str
    units: int
    unit: str
    scope: Scope
    period: BillingPeriod
    event_id: str
    event_type: str
    observed_at: float
    reason: str = "unattributable"


# ── credits + ledger (M9.12) ────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class GrantCredit(_Command):
    account_id: str
    kind: CreditKind
    amount: Money
    expires_at: float | None = None
    source_ref: str = ""


@dataclass(frozen=True, slots=True)
class PostLedgerEntry(_Command):
    account_id: str
    entry_type: LedgerEntryType
    direction: LedgerDirection
    amount: Money
    invoice_id: str = ""
    payment_id: str = ""
    subscription_id: str = ""
    description: str = ""


# ── invoicing (M9.13) ───────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class DraftInvoice(_Command):
    account_id: str
    currency: Currency
    period: BillingPeriod
    lines: tuple[ChargeLine, ...] = ()
    subscription_id: str = ""
    payment_terms_days: int = 0
    purchase_order_ref: str = ""


@dataclass(frozen=True, slots=True)
class IssueInvoice(_Command):
    invoice_id: str
    entity_code: str = "voxvoice"
    jurisdiction: str = ""


# ── payments (M9.14 domain) ─────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class RequestPayment(_Command):
    """Records the *intent* to collect. **No payment is executed at M9.3** — adapters and
    execution land at M9.14 (M9.0C: providers are rented edges behind contracts)."""

    account_id: str
    invoice_id: str
    amount: Money
    method: PaymentMethodKind = PaymentMethodKind.CARD
    instrument_id: str = ""


# ── refunds (M9.16 domain) ──────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class RequestRefund(_Command):
    account_id: str
    payment_id: str
    invoice_id: str
    amount: Money
    reason: RefundReason
    request_key: str
    captured: Money | None = None


@dataclass(frozen=True, slots=True)
class ApproveRefund(_Command):
    """Approval requires a different principal than the requester — separation of duties
    is a Foundation rule; this command only carries the approver's identity to it."""

    refund_id: str
    note: str = ""


# ── tax (M9.15 domain) ──────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class UpsertTaxProfile(_Command):
    profile: TaxProfile


# ── contracts + licensing (M9.21) ───────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class RecordContract(_Command):
    contract: object  # billing.foundation.Contract — kept structural to avoid a cycle


@dataclass(frozen=True, slots=True)
class IssueLicense(_Command):
    account_id: str
    kind: LicenseKind
    valid_from: float
    valid_until: float | None = None
    entitlement_codes: tuple[str, ...] = ()
    contract_id: str = ""


@dataclass(frozen=True, slots=True)
class RevokeLicense(_Command):
    license_id: str
    reason: str = ""


#: Every command type, in registration order — the bus wiring and the coverage test both
#: read this, so a new command cannot be added without a handler.
ALL_COMMANDS: tuple[type, ...] = (
    OpenBillingAccount,
    UpdateBillingProfile,
    CloseBillingAccount,
    DefineMeter,
    DefinePlan,
    PublishPlan,
    PublishPriceBook,
    StartSubscription,
    TransitionSubscription,
    ChangeSubscriptionPlan,
    GrantEntitlement,
    RevokeEntitlement,
    PublishEntitlementSnapshot,
    RecordUsage,
    QuarantineUsage,
    GrantCredit,
    PostLedgerEntry,
    DraftInvoice,
    IssueInvoice,
    RequestPayment,
    RequestRefund,
    ApproveRefund,
    UpsertTaxProfile,
    RecordContract,
    IssueLicense,
    RevokeLicense,
)
