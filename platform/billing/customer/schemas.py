"""HTTP request/response models (M9.5) — Pydantic v2.

The wire contract, and deliberately a *third* representation: distinct from Application
commands/queries, from Application view DTOs, and from Foundation aggregates. **No Foundation
aggregate, view DTO, or persistence row is ever serialized directly.** That separation is what
lets the domain evolve without breaking a published API, and stops a storage detail from
becoming a public field by accident.

Foundation enums *are* reused for typed fields (currency, tier, meter aggregation, …), so
validation and OpenAPI enums come from the domain vocabulary rather than a hand-copied list
that drifts.

Pure data. Each body carries a `to_*()` that builds the Foundation value objects a command
needs. Each response carries **two** constructors, because the Application has two return
shapes and pretending otherwise is how a field silently goes missing: `from_view()` for the
DTOs a *query* returns, and `from_aggregate()` for the immutable Foundation aggregate a
*command* returns. Both are field mapping — the arithmetic that
would make them business logic (rating, proration, tax, balance) happens below, in the
Foundation.

**Money crosses the wire as integer minor units plus an explicit currency**, never as a float
and never as a formatted string: the Foundation's discipline reaches all the way to the
boundary, because a rounding decision made in a JSON serializer is a rounding decision nobody
reviewed (ADR-0023 RS5).
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from billing.foundation import (
    AccountKind,
    BillingAddress,
    BillingContact,
    BillingInterval,
    BillingPeriod,
    ChargeLine,
    ChargeLineKind,
    CreditKind,
    Currency,
    Entitlement,
    EntitlementGate,
    FeatureKind,
    GateClassification,
    LedgerDirection,
    LedgerEntryType,
    LicenseKind,
    MeterAggregation,
    MeterSource,
    Money,
    PaymentMethodKind,
    PlanFeature,
    Quantity,
    RefundReason,
    Rounding,
    Scope,
    ScopeKind,
    SubscriptionItem,
    SubscriptionTrigger,
    TaxpayerKind,
    TaxProfile,
    TaxRegime,
    TaxRegistration,
    Tier,
)

# ── shared ──────────────────────────────────────────────────────────────────────


class ErrorBody(BaseModel):
    type: str = Field(description="Typed error name, e.g. BillingValidationError")
    message: str
    request_id: str | None = None
    diagnostics: list[dict] | None = Field(
        default=None, description="Present on 422 — every validation finding, not just the first"
    )


class ErrorEnvelope(BaseModel):
    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "error": {
                    "type": "BillingAuthorizationError",
                    "message": "billing:account:write denied",
                    "request_id": "8f14e45fceea167a5a36dedd4bea2543",
                }
            }
        }
    )
    error: ErrorBody


# Per-route error responses, attached to every documented endpoint.
ERROR_RESPONSES: dict[int | str, dict[str, Any]] = {
    400: {"model": ErrorEnvelope, "description": "Malformed request or unroutable message"},
    401: {"model": ErrorEnvelope, "description": "Missing, invalid, or expired identity token"},
    403: {
        "model": ErrorEnvelope,
        "description": "Authorization denied, or cross-organization access",
    },
    404: {"model": ErrorEnvelope, "description": "No such billing record in this organization"},
    409: {
        "model": ErrorEnvelope,
        "description": "Lifecycle conflict, or an attempt to alter an immutable revenue fact",
    },
    422: {"model": ErrorEnvelope, "description": "Validation failed — see diagnostics"},
    500: {"model": ErrorEnvelope, "description": "Internal error (detail withheld)"},
}


class MoneyBody(BaseModel):
    model_config = ConfigDict(json_schema_extra={"example": {"minor": 99900, "currency": "INR"}})
    minor: int = Field(description="Integer minor units (paise/cents). Never a float.")
    currency: Currency

    def to_money(self) -> Money:
        return Money(self.minor, self.currency)


def _money(amount) -> MoneyResponse | None:
    """Foundation `Money` → wire money. Integer minor units, explicit currency, no float."""
    return (
        None
        if amount is None
        else MoneyResponse(minor=amount.minor, currency=amount.currency.value)
    )


class MoneyResponse(BaseModel):
    minor: int
    currency: str

    @classmethod
    def from_view(cls, view) -> MoneyResponse | None:
        return None if view is None else cls(minor=view.minor, currency=view.currency)


class PeriodBody(BaseModel):
    model_config = ConfigDict(json_schema_extra={"example": {"start": 0.0, "end": 2592000.0}})
    start: float = Field(description="Period start, epoch seconds")
    end: float = Field(description="Period end, epoch seconds (exclusive)")

    def to_period(self) -> BillingPeriod:
        return BillingPeriod(self.start, self.end)


class ScopeBody(BaseModel):
    org_id: str = ""
    kind: ScopeKind = ScopeKind.ORGANIZATION
    workspace_id: str = ""
    project_id: str = ""
    agent_id: str = ""

    def to_scope(self, org_id: str) -> Scope:
        # The organization always comes from the verified token, never from the body — a
        # caller must not be able to name a tenant it did not authenticate as.
        return Scope(
            org_id=org_id,
            kind=self.kind,
            workspace_id=self.workspace_id,
            project_id=self.project_id,
            agent_id=self.agent_id,
        )


# ── accounts ────────────────────────────────────────────────────────────────────


class ContactBody(BaseModel):
    email: str = ""
    name: str = ""
    phone: str = ""

    def to_contact(self) -> BillingContact:
        return BillingContact(email=self.email, name=self.name, phone=self.phone)


class AddressBody(BaseModel):
    line1: str = ""
    line2: str = ""
    city: str = ""
    region: str = ""
    postal_code: str = ""
    country: str = Field(default="", description="ISO-3166 alpha-2")

    def to_address(self) -> BillingAddress:
        return BillingAddress(
            line1=self.line1,
            line2=self.line2,
            city=self.city,
            region=self.region,
            postal_code=self.postal_code,
            country=self.country,
        )


class OpenAccountBody(BaseModel):
    model_config = ConfigDict(
        json_schema_extra={
            "example": {"display_name": "Acme Pvt Ltd", "currency": "INR", "kind": "direct"}
        }
    )
    display_name: str
    currency: Currency
    kind: AccountKind = AccountKind.DIRECT
    parent_account_id: str = ""
    contact: ContactBody = Field(default_factory=ContactBody)
    address: AddressBody = Field(default_factory=AddressBody)


class UpdateProfileBody(BaseModel):
    contact: ContactBody = Field(default_factory=ContactBody)
    address: AddressBody = Field(default_factory=AddressBody)


class CloseAccountBody(BaseModel):
    reason: str = ""


class AccountResponse(BaseModel):
    id: str
    org_id: str
    display_name: str
    currency: str
    kind: str
    status: str
    parent_account_id: str
    is_billable: bool

    @classmethod
    def from_view(cls, view) -> AccountResponse:
        return cls(
            id=view.id,
            org_id=view.org_id,
            display_name=view.display_name,
            currency=view.currency,
            kind=view.kind,
            status=view.status,
            parent_account_id=view.parent_account_id,
            is_billable=view.is_billable,
        )

    @classmethod
    def from_aggregate(cls, account) -> AccountResponse:
        return cls(
            id=account.id,
            org_id=account.org_id,
            display_name=account.display_name,
            currency=account.currency.value,
            kind=account.kind.value,
            status=account.status.value,
            parent_account_id=account.parent_account_id,
            is_billable=account.is_billable,
        )


class AccountHierarchyResponse(BaseModel):
    parent: AccountResponse
    children: list[AccountResponse]
    invoiced_account_id: str = Field(
        description="Which account actually receives the invoice — a billing hierarchy is not "
        "an identity hierarchy"
    )

    @classmethod
    def from_view(cls, view) -> AccountHierarchyResponse:
        return cls(
            parent=AccountResponse.from_view(view.parent),
            children=[AccountResponse.from_view(c) for c in view.children],
            invoiced_account_id=view.invoiced_account_id,
        )


# ── catalog ─────────────────────────────────────────────────────────────────────


class DefineMeterBody(BaseModel):
    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "code": "voice_minutes",
                "display_name": "Voice minutes",
                "unit": "minute",
                "aggregation": "sum",
                "source": "runtime.session",
            }
        }
    )
    code: str
    display_name: str
    unit: str
    aggregation: MeterAggregation
    source: MeterSource
    rounding: Rounding | None = Field(
        default=None, description="Omit to use the Foundation's default rounding policy"
    )


class PlanFeatureBody(BaseModel):
    feature_code: str
    enabled: bool = True
    allowance: int | None = None
    allows_overage: bool = False

    def to_feature(self) -> PlanFeature:
        return PlanFeature(
            feature_code=self.feature_code,
            enabled=self.enabled,
            allowance=self.allowance,
            allows_overage=self.allows_overage,
        )


class DefinePlanBody(BaseModel):
    code: str
    display_name: str
    tier: Tier
    features: list[PlanFeatureBody] = Field(default_factory=list)
    metered_codes: list[str] = Field(default_factory=list)

    def to_features(self) -> tuple[PlanFeature, ...]:
        return tuple(f.to_feature() for f in self.features)


class PublishPlanBody(BaseModel):
    code: str
    version: int = 1


class PlanResponse(BaseModel):
    code: str
    version: int
    display_name: str
    tier: str
    published: bool
    deprecated: bool
    feature_codes: list[str]
    metered_codes: list[str]

    @classmethod
    def from_view(cls, view) -> PlanResponse:
        return cls(
            code=view.code,
            version=view.version,
            display_name=view.display_name,
            tier=view.tier,
            published=view.published,
            deprecated=view.deprecated,
            feature_codes=list(view.feature_codes),
            metered_codes=list(view.metered_codes),
        )

    @classmethod
    def from_aggregate(cls, plan) -> PlanResponse:
        return cls(
            code=plan.code,
            version=plan.version,
            display_name=plan.display_name,
            tier=plan.tier.value,
            published=plan.published,
            deprecated=plan.deprecated,
            feature_codes=[f.feature_code for f in plan.features],
            metered_codes=list(plan.metered_codes),
        )


# ── pricing ─────────────────────────────────────────────────────────────────────


class PublishPriceBookBody(BaseModel):
    code: str
    display_name: str
    currency: Currency


class PriceBookResponse(BaseModel):
    id: str
    code: str
    version: int
    currency: str
    audience: str
    published: bool
    price_codes: list[str]

    @classmethod
    def from_view(cls, view) -> PriceBookResponse:
        return cls(
            id=view.id,
            code=view.code,
            version=view.version,
            currency=view.currency,
            audience=view.audience,
            published=view.published,
            price_codes=list(view.price_codes),
        )

    @classmethod
    def from_aggregate(cls, book) -> PriceBookResponse:
        return cls(
            id=book.id,
            code=book.code,
            version=book.version,
            currency=book.currency.value,
            audience=book.audience.value,
            published=book.published,
            price_codes=[price.code for price in book.prices],
        )


# ── subscriptions ───────────────────────────────────────────────────────────────


class SubscriptionItemBody(BaseModel):
    price_code: str
    price_version: int = 1
    quantity: int = 1
    meter_code: str = ""

    def to_item(self) -> SubscriptionItem:
        return SubscriptionItem(
            price_code=self.price_code,
            price_version=self.price_version,
            quantity=self.quantity,
            meter_code=self.meter_code,
        )


class StartSubscriptionBody(BaseModel):
    account_id: str
    plan_code: str
    plan_version: int = 1
    price_book_id: str
    price_book_version: int = 1
    currency: Currency
    interval: BillingInterval
    items: list[SubscriptionItemBody] = Field(default_factory=list)
    trial_seconds: int = 0
    contract_id: str = ""

    def to_items(self) -> tuple[SubscriptionItem, ...]:
        return tuple(i.to_item() for i in self.items)


class TransitionSubscriptionBody(BaseModel):
    model_config = ConfigDict(json_schema_extra={"example": {"trigger": "activate"}})
    trigger: SubscriptionTrigger = Field(
        description="A lifecycle trigger. Legality is decided by the Foundation's state "
        "machine — an illegal edge is rejected (409), never coerced."
    )


class ChangePlanBody(BaseModel):
    is_expansion: bool
    immediate_downgrade: bool = False
    price_delta: MoneyBody | None = None
    at: float | None = None


class SubscriptionResponse(BaseModel):
    id: str
    account_id: str
    plan_code: str
    plan_version: int
    state: str
    interval: str
    currency: str
    seat_count: int
    period_start: float | None
    period_end: float | None
    trial_end: float | None
    contract_id: str

    @classmethod
    def from_view(cls, view) -> SubscriptionResponse:
        return cls(
            id=view.id,
            account_id=view.account_id,
            plan_code=view.plan_code,
            plan_version=view.plan_version,
            state=view.state,
            interval=view.interval,
            currency=view.currency,
            seat_count=view.seat_count,
            period_start=view.period_start,
            period_end=view.period_end,
            trial_end=view.trial_end,
            contract_id=view.contract_id,
        )

    @classmethod
    def from_aggregate(cls, subscription) -> SubscriptionResponse:
        period = subscription.current_period
        return cls(
            id=subscription.id,
            account_id=subscription.account_id,
            plan_code=subscription.plan_code,
            plan_version=subscription.plan_version,
            state=subscription.state.value,
            interval=subscription.interval.value,
            currency=subscription.currency.value,
            seat_count=subscription.seat_count,
            period_start=period.start if period else None,
            period_end=period.end if period else None,
            trial_end=subscription.trial_end,
            contract_id=subscription.contract_id,
        )


# ── entitlements ────────────────────────────────────────────────────────────────


class EntitlementBody(BaseModel):
    code: str
    kind: FeatureKind
    enabled: bool = True
    allowance: int | None = None
    allows_overage: bool = False

    def to_entitlement(self) -> Entitlement:
        return Entitlement(
            code=self.code,
            kind=self.kind,
            enabled=self.enabled,
            allowance=self.allowance,
            allows_overage=self.allows_overage,
        )


class GrantEntitlementBody(BaseModel):
    account_id: str
    entitlement: EntitlementBody
    source_ref: str = ""


class GateBody(BaseModel):
    code: str
    kind: FeatureKind
    classification: GateClassification = Field(
        description="fail_open | fail_closed | degrade — how the *consuming* plane behaves "
        "when this snapshot is stale. Billing decides entitlement; it never enforces it."
    )
    description: str = ""
    meter_code: str = ""

    def to_gate(self) -> EntitlementGate:
        return EntitlementGate(
            code=self.code,
            kind=self.kind,
            classification=self.classification,
            description=self.description,
            meter_code=self.meter_code,
        )


class PublishSnapshotBody(BaseModel):
    account_id: str
    gates: list[GateBody]
    subscription_id: str = ""
    max_staleness_seconds: float = 300.0

    def to_gates(self) -> tuple[EntitlementGate, ...]:
        return tuple(g.to_gate() for g in self.gates)


class EntitlementResponse(BaseModel):
    code: str
    kind: str
    enabled: bool
    allowance: int | None
    allows_overage: bool
    source: str

    @classmethod
    def from_view(cls, view) -> EntitlementResponse:
        return cls(
            code=view.code,
            kind=view.kind,
            enabled=view.enabled,
            allowance=view.allowance,
            allows_overage=view.allows_overage,
            source=view.source,
        )


class EntitlementSnapshotResponse(BaseModel):
    id: str
    account_id: str
    version: int
    issued_at: float
    max_staleness_seconds: float
    entitlements: list[EntitlementResponse]
    classifications: dict[str, str] = Field(
        description="code → fail_open|fail_closed|degrade, for the enforcing plane"
    )

    @classmethod
    def from_view(cls, view) -> EntitlementSnapshotResponse:
        return cls(
            id=view.id,
            account_id=view.account_id,
            version=view.version,
            issued_at=view.issued_at,
            max_staleness_seconds=view.max_staleness_seconds,
            entitlements=[EntitlementResponse.from_view(e) for e in view.entitlements],
            classifications=dict(view.classifications),
        )


class QuotaVerdictResponse(BaseModel):
    code: str
    allowance: int | None
    consumed: int
    allows_overage: bool
    classification: str
    within_allowance: bool
    would_exceed: bool
    stale: bool

    @classmethod
    def from_view(cls, view) -> QuotaVerdictResponse:
        return cls(
            code=view.code,
            allowance=view.allowance,
            consumed=view.consumed,
            allows_overage=view.allows_overage,
            classification=view.classification,
            within_allowance=view.within_allowance,
            would_exceed=view.would_exceed,
            stale=view.stale,
        )


# ── metering ────────────────────────────────────────────────────────────────────


class RecordUsageBody(BaseModel):
    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "account_id": "acc_1",
                "meter_code": "voice_minutes",
                "units": 600,
                "unit": "minute",
                "period": {"start": 0.0, "end": 2592000.0},
                "event_id": "evt_9f31",
                "event_type": "metric.session.completed",
                "observed_at": 1754200000.0,
            }
        }
    )
    account_id: str
    meter_code: str
    units: int
    unit: str
    period: PeriodBody
    event_id: str = Field(
        description="The source event id. Ingestion is idempotent on (event_id, meter_code): "
        "replaying a delivery returns the first record and bills once."
    )
    event_type: str
    observed_at: float
    scope: ScopeBody = Field(default_factory=ScopeBody)
    subscription_id: str = ""


class QuarantineUsageBody(RecordUsageBody):
    reason: str = ""


class UsageSummaryResponse(BaseModel):
    account_id: str
    meter_code: str
    unit: str
    units: int
    record_count: int
    period_start: float
    period_end: float

    @classmethod
    def from_view(cls, view) -> UsageSummaryResponse:
        return cls(
            account_id=view.account_id,
            meter_code=view.meter_code,
            unit=view.unit,
            units=view.units,
            record_count=view.record_count,
            period_start=view.period_start,
            period_end=view.period_end,
        )


# ── ledger + credits ────────────────────────────────────────────────────────────


class PostLedgerEntryBody(BaseModel):
    account_id: str
    entry_type: LedgerEntryType
    direction: LedgerDirection
    amount: MoneyBody
    invoice_id: str = ""
    payment_id: str = ""
    subscription_id: str = ""
    description: str = ""


class GrantCreditBody(BaseModel):
    account_id: str
    kind: CreditKind
    amount: MoneyBody
    expires_at: float | None = None
    source_ref: str = ""


class BalanceResponse(BaseModel):
    account_id: str
    amount: MoneyResponse
    entry_count: int

    @classmethod
    def from_view(cls, view) -> BalanceResponse:
        return cls(
            account_id=view.account_id,
            amount=MoneyResponse.from_view(view.amount),
            entry_count=view.entry_count,
        )


# ── invoicing ───────────────────────────────────────────────────────────────────


class ChargeLineBody(BaseModel):
    id: str
    kind: ChargeLineKind
    description: str
    amount: MoneyBody
    period: PeriodBody
    subscription_id: str = ""
    price_code: str = ""
    price_version: int = 1
    meter_code: str = ""
    quantity_units: int | None = None
    quantity_unit: str = ""
    unit_amount: MoneyBody | None = None
    usage_record_ids: list[str] = Field(default_factory=list)
    discount_code: str = ""

    def to_line(self, org_id: str, account_id: str) -> ChargeLine:
        return ChargeLine(
            id=self.id,
            org_id=org_id,
            account_id=account_id,
            kind=self.kind,
            description=self.description,
            amount=self.amount.to_money(),
            period=self.period.to_period(),
            subscription_id=self.subscription_id,
            price_code=self.price_code,
            price_version=self.price_version,
            meter_code=self.meter_code,
            quantity=(
                Quantity(self.quantity_units, self.quantity_unit)
                if self.quantity_units is not None
                else None
            ),
            unit_amount=self.unit_amount.to_money() if self.unit_amount else None,
            usage_record_ids=tuple(self.usage_record_ids),
            discount_code=self.discount_code,
        )


class DraftInvoiceBody(BaseModel):
    account_id: str
    currency: Currency
    period: PeriodBody
    lines: list[ChargeLineBody] = Field(default_factory=list)
    subscription_id: str = ""
    payment_terms_days: int = 0
    purchase_order_ref: str = ""

    def to_lines(self, org_id: str) -> tuple[ChargeLine, ...]:
        return tuple(line.to_line(org_id, self.account_id) for line in self.lines)


class IssueInvoiceBody(BaseModel):
    entity_code: str = "VOX"
    jurisdiction: str = Field(default="IN", description="Legal jurisdiction of the issuing entity")


class InvoiceResponse(BaseModel):
    id: str
    account_id: str
    state: str
    number: str
    currency: str
    period_start: float
    period_end: float
    line_count: int
    total: MoneyResponse | None
    amount_due: MoneyResponse | None
    issued_at: float | None
    due_at: float | None
    evidence: list[str]

    @classmethod
    def from_view(cls, view) -> InvoiceResponse:
        return cls(
            id=view.id,
            account_id=view.account_id,
            state=view.state,
            number=view.number,
            currency=view.currency,
            period_start=view.period_start,
            period_end=view.period_end,
            line_count=view.line_count,
            total=MoneyResponse.from_view(view.total),
            amount_due=MoneyResponse.from_view(view.amount_due),
            issued_at=view.issued_at,
            due_at=view.due_at,
            evidence=list(view.evidence),
        )

    @classmethod
    def from_aggregate(cls, invoice) -> InvoiceResponse:
        totals = invoice.totals
        return cls(
            id=invoice.id,
            account_id=invoice.account_id,
            state=invoice.state.value,
            number=invoice.number.formatted if invoice.number is not None else "",
            currency=invoice.currency.value,
            period_start=invoice.period.start,
            period_end=invoice.period.end,
            line_count=len(invoice.lines),
            total=_money(totals.total) if totals else None,
            amount_due=_money(totals.amount_due) if totals else None,
            issued_at=invoice.issued_at,
            due_at=invoice.due_at,
            evidence=list(invoice.evidence),
        )


# ── payments + refunds ──────────────────────────────────────────────────────────


class RequestPaymentBody(BaseModel):
    account_id: str
    invoice_id: str
    amount: MoneyBody
    method: PaymentMethodKind = PaymentMethodKind.CARD
    instrument_id: str = ""


class PaymentResponse(BaseModel):
    id: str
    invoice_id: str
    state: str
    method: str
    amount: MoneyResponse
    attempt: int
    provider_code: str

    @classmethod
    def from_view(cls, view) -> PaymentResponse:
        return cls(
            id=view.id,
            invoice_id=view.invoice_id,
            state=view.state,
            method=view.method,
            amount=MoneyResponse.from_view(view.amount),
            attempt=view.attempt,
            provider_code=view.provider_code,
        )

    @classmethod
    def from_aggregate(cls, payment) -> PaymentResponse:
        return cls(
            id=payment.id,
            invoice_id=payment.invoice_id,
            state=payment.state.value,
            method=payment.method.value,
            amount=_money(payment.amount),
            attempt=payment.attempt,
            provider_code=payment.provider_code,
        )


class RequestRefundBody(BaseModel):
    account_id: str
    payment_id: str
    invoice_id: str
    amount: MoneyBody
    reason: RefundReason
    request_key: str = Field(
        description="Caller-supplied idempotency key: re-requesting with the same key returns "
        "the existing refund rather than issuing a second one"
    )
    captured: MoneyBody | None = None


class ApproveRefundBody(BaseModel):
    note: str = ""


class RefundResponse(BaseModel):
    id: str
    payment_id: str
    invoice_id: str
    state: str
    reason: str
    destination: str
    amount: MoneyResponse
    approved_by: str

    @classmethod
    def from_view(cls, view) -> RefundResponse:
        return cls(
            id=view.id,
            payment_id=view.payment_id,
            invoice_id=view.invoice_id,
            state=view.state,
            reason=view.reason,
            destination=view.destination,
            amount=MoneyResponse.from_view(view.amount),
            approved_by=view.approved_by,
        )

    @classmethod
    def from_aggregate(cls, refund) -> RefundResponse:
        return cls(
            id=refund.id,
            payment_id=refund.payment_id,
            invoice_id=refund.invoice_id,
            state=refund.state.value,
            reason=refund.reason.value,
            destination=refund.destination.value,
            amount=_money(refund.amount),
            approved_by=refund.approval.approver_id if refund.approval else "",
        )


# ── tax ─────────────────────────────────────────────────────────────────────────


class TaxRegistrationBody(BaseModel):
    regime: TaxRegime
    number: str
    country: str = ""
    validated: bool = False

    def to_registration(self) -> TaxRegistration:
        return TaxRegistration(
            regime=self.regime, number=self.number, country=self.country, validated=self.validated
        )


class UpsertTaxProfileBody(BaseModel):
    account_id: str
    regime: TaxRegime = TaxRegime.NONE
    taxpayer_kind: TaxpayerKind = TaxpayerKind.BUSINESS
    country: str = ""
    place_of_supply: str = ""
    registrations: list[TaxRegistrationBody] = Field(default_factory=list)
    exemption_ref: str = ""
    prices_include_tax: bool = False

    def to_profile(self, *, profile_id: str, org_id: str, updated_at: float) -> TaxProfile:
        return TaxProfile(
            id=profile_id,
            org_id=org_id,
            account_id=self.account_id,
            regime=self.regime,
            taxpayer_kind=self.taxpayer_kind,
            country=self.country,
            place_of_supply=self.place_of_supply,
            registrations=tuple(r.to_registration() for r in self.registrations),
            exemption_ref=self.exemption_ref,
            prices_include_tax=self.prices_include_tax,
            updated_at=updated_at,
        )


class TaxProfileResponse(BaseModel):
    id: str
    account_id: str
    regime: str
    taxpayer_kind: str
    country: str
    place_of_supply: str
    registration_numbers: list[str]

    @classmethod
    def from_view(cls, view) -> TaxProfileResponse:
        return cls(
            id=view.id,
            account_id=view.account_id,
            regime=view.regime,
            taxpayer_kind=view.taxpayer_kind,
            country=view.country,
            place_of_supply=view.place_of_supply,
            registration_numbers=list(view.registration_numbers),
        )

    @classmethod
    def from_aggregate(cls, profile) -> TaxProfileResponse:
        return cls(
            id=profile.id,
            account_id=profile.account_id,
            regime=profile.regime.value,
            taxpayer_kind=profile.taxpayer_kind.value,
            country=profile.country,
            place_of_supply=profile.place_of_supply,
            registration_numbers=[r.number for r in profile.registrations],
        )


class TaxDeterminationResponse(BaseModel):
    id: str
    invoice_id: str
    regime: str
    treatment: str
    taxable_amount: MoneyResponse
    tax_total: MoneyResponse
    gst_split: str
    adapter_code: str

    @classmethod
    def from_view(cls, view) -> TaxDeterminationResponse:
        return cls(
            id=view.id,
            invoice_id=view.invoice_id,
            regime=view.regime,
            treatment=view.treatment,
            taxable_amount=MoneyResponse.from_view(view.taxable_amount),
            tax_total=MoneyResponse.from_view(view.tax_total),
            gst_split=view.gst_split,
            adapter_code=view.adapter_code,
        )


# ── contracts + licenses ────────────────────────────────────────────────────────


class ContractResponse(BaseModel):
    id: str
    account_id: str
    code: str
    state: str
    settlement: str
    plan_code: str
    starts_at: float | None
    ends_at: float | None

    @classmethod
    def from_view(cls, view) -> ContractResponse:
        return cls(
            id=view.id,
            account_id=view.account_id,
            code=view.code,
            state=view.state,
            settlement=view.settlement,
            plan_code=view.plan_code,
            starts_at=view.starts_at,
            ends_at=view.ends_at,
        )


class IssueLicenseBody(BaseModel):
    account_id: str
    kind: LicenseKind
    valid_from: float
    valid_until: float | None = None
    entitlement_codes: list[str] = Field(default_factory=list)
    contract_id: str = ""


class RevokeLicenseBody(BaseModel):
    reason: str = ""


class LicenseResponse(BaseModel):
    id: str
    account_id: str
    kind: str
    state: str
    valid_from: float
    valid_until: float | None
    entitlement_codes: list[str]

    @classmethod
    def from_view(cls, view) -> LicenseResponse:
        return cls(
            id=view.id,
            account_id=view.account_id,
            kind=view.kind,
            state=view.state,
            valid_from=view.valid_from,
            valid_until=view.valid_until,
            entitlement_codes=list(view.entitlement_codes),
        )

    @classmethod
    def from_aggregate(cls, license_) -> LicenseResponse:
        return cls(
            id=license_.id,
            account_id=license_.account_id,
            kind=license_.kind.value,
            state=license_.state.value,
            valid_from=license_.valid_from,
            valid_until=license_.valid_until,
            entitlement_codes=list(license_.entitlement_codes),
        )


# ── system ──────────────────────────────────────────────────────────────────────


class HealthResponse(BaseModel):
    status: str
    environment: str
    plane: str = "billing"


class ReadyResponse(BaseModel):
    ready: bool
    checks: dict[str, bool]


class VersionResponse(BaseModel):
    api: str
    title: str
    prefix: str
