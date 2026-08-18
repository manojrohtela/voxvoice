"""DTO mapping (M9.3) — pure, total, deterministic boundary translation.

One place the translation lives: Foundation aggregate → **view DTO** (the read projection a
future transport layer serializes). No domain logic; no derivation that the Foundation does
not already do. Where a view carries a computed number, the number comes from a Foundation
service or classmethod (`LedgerBalance.of`, `CreditBalance.of`, `MeteringService.total`,
`QuotaService.verdict`) — the Application calls, never recomputes.

Keeping mapping here means use cases read as orchestration rather than plumbing, and the
M9.18 Control Plane maps HTTP↔command without touching this module.
"""

from __future__ import annotations

from dataclasses import dataclass

from billing.foundation import (
    AccountHierarchy,
    BillingAccount,
    Contract,
    CreditBalance,
    Entitlement,
    EntitlementSnapshot,
    Invoice,
    LedgerBalance,
    License,
    Money,
    Payment,
    Plan,
    PriceBook,
    Quantity,
    QuotaVerdict,
    Refund,
    Subscription,
    TaxDetermination,
    TaxProfile,
    UsageRecord,
)

# ── view DTOs ───────────────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class AccountView:
    id: str
    org_id: str
    display_name: str
    currency: str
    kind: str
    status: str
    parent_account_id: str
    is_billable: bool


@dataclass(frozen=True, slots=True)
class AccountHierarchyView:
    parent: AccountView
    children: tuple[AccountView, ...]
    invoiced_account_id: str


@dataclass(frozen=True, slots=True)
class PlanView:
    code: str
    version: int
    display_name: str
    tier: str
    published: bool
    deprecated: bool
    feature_codes: tuple[str, ...]
    metered_codes: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class PriceBookView:
    id: str
    code: str
    version: int
    currency: str
    audience: str
    published: bool
    price_codes: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class SubscriptionView:
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


@dataclass(frozen=True, slots=True)
class EntitlementView:
    code: str
    kind: str
    enabled: bool
    allowance: int | None
    allows_overage: bool
    source: str


@dataclass(frozen=True, slots=True)
class EntitlementSnapshotView:
    id: str
    account_id: str
    version: int
    issued_at: float
    max_staleness_seconds: float
    entitlements: tuple[EntitlementView, ...]
    classifications: tuple[tuple[str, str], ...]


@dataclass(frozen=True, slots=True)
class QuotaVerdictView:
    code: str
    allowance: int | None
    consumed: int
    allows_overage: bool
    classification: str
    within_allowance: bool
    would_exceed: bool
    stale: bool


@dataclass(frozen=True, slots=True)
class UsageSummaryView:
    account_id: str
    meter_code: str
    unit: str
    units: int
    record_count: int
    period_start: float
    period_end: float


@dataclass(frozen=True, slots=True)
class MoneyView:
    minor: int
    currency: str


@dataclass(frozen=True, slots=True)
class BalanceView:
    account_id: str
    amount: MoneyView
    entry_count: int


@dataclass(frozen=True, slots=True)
class InvoiceView:
    id: str
    account_id: str
    state: str
    number: str
    currency: str
    period_start: float
    period_end: float
    line_count: int
    total: MoneyView | None
    amount_due: MoneyView | None
    issued_at: float | None
    due_at: float | None
    evidence: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class PaymentView:
    id: str
    invoice_id: str
    state: str
    method: str
    amount: MoneyView
    attempt: int
    provider_code: str


@dataclass(frozen=True, slots=True)
class RefundView:
    id: str
    payment_id: str
    invoice_id: str
    state: str
    reason: str
    destination: str
    amount: MoneyView
    approved_by: str


@dataclass(frozen=True, slots=True)
class TaxProfileView:
    id: str
    account_id: str
    regime: str
    taxpayer_kind: str
    country: str
    place_of_supply: str
    registration_numbers: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class TaxDeterminationView:
    id: str
    invoice_id: str
    regime: str
    treatment: str
    taxable_amount: MoneyView
    tax_total: MoneyView
    gst_split: str
    adapter_code: str


@dataclass(frozen=True, slots=True)
class ContractView:
    id: str
    account_id: str
    code: str
    state: str
    settlement: str
    plan_code: str
    starts_at: float | None
    ends_at: float | None


@dataclass(frozen=True, slots=True)
class LicenseView:
    id: str
    account_id: str
    kind: str
    state: str
    valid_from: float
    valid_until: float | None
    entitlement_codes: tuple[str, ...]


# ── mappers (pure functions) ────────────────────────────────────────────────────


def money_view(amount: Money) -> MoneyView:
    return MoneyView(amount.minor, amount.currency.value)


def account_view(account: BillingAccount) -> AccountView:
    return AccountView(
        id=account.id,
        org_id=account.org_id,
        display_name=account.display_name,
        currency=account.currency.value,
        kind=account.kind.value,
        status=account.status.value,
        parent_account_id=account.parent_account_id,
        is_billable=account.is_billable,
    )


def hierarchy_view(hierarchy: AccountHierarchy) -> AccountHierarchyView:
    return AccountHierarchyView(
        parent=account_view(hierarchy.parent),
        children=tuple(account_view(c) for c in hierarchy.children),
        invoiced_account_id=hierarchy.invoiced_account.id,
    )


def plan_view(plan: Plan) -> PlanView:
    return PlanView(
        code=plan.code,
        version=plan.version,
        display_name=plan.display_name,
        tier=plan.tier.value,
        published=plan.published,
        deprecated=plan.deprecated,
        feature_codes=tuple(f.feature_code for f in plan.features),
        metered_codes=plan.metered_codes,
    )


def price_book_view(book: PriceBook) -> PriceBookView:
    return PriceBookView(
        id=book.id,
        code=book.code,
        version=book.version,
        currency=book.currency.value,
        audience=book.audience.value,
        published=book.published,
        price_codes=tuple(sorted({p.code for p in book.prices})),
    )


def subscription_view(subscription: Subscription) -> SubscriptionView:
    period = subscription.current_period
    return SubscriptionView(
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


def entitlement_view(entitlement: Entitlement) -> EntitlementView:
    return EntitlementView(
        code=entitlement.code,
        kind=entitlement.kind.value,
        enabled=entitlement.enabled,
        allowance=entitlement.allowance,
        allows_overage=entitlement.allows_overage,
        source=entitlement.source.value,
    )


def snapshot_view(snapshot: EntitlementSnapshot) -> EntitlementSnapshotView:
    return EntitlementSnapshotView(
        id=snapshot.id,
        account_id=snapshot.account_id,
        version=snapshot.version,
        issued_at=snapshot.issued_at,
        max_staleness_seconds=snapshot.max_staleness_seconds,
        entitlements=tuple(entitlement_view(e) for e in snapshot.entitlements),
        classifications=tuple((g.code, g.classification.value) for g in snapshot.gates),
    )


def quota_view(verdict: QuotaVerdict) -> QuotaVerdictView:
    return QuotaVerdictView(
        code=verdict.code,
        allowance=verdict.allowance,
        consumed=verdict.consumed,
        allows_overage=verdict.allows_overage,
        classification=verdict.classification.value if verdict.classification else "",
        within_allowance=verdict.within_allowance,
        would_exceed=verdict.would_exceed,
        stale=verdict.stale,
    )


def usage_summary_view(
    *,
    account_id: str,
    meter_code: str,
    total: Quantity,
    records: tuple[UsageRecord, ...],
    period_start: float,
    period_end: float,
) -> UsageSummaryView:
    return UsageSummaryView(
        account_id=account_id,
        meter_code=meter_code,
        unit=total.unit,
        units=total.units,
        record_count=len(records),
        period_start=period_start,
        period_end=period_end,
    )


def ledger_balance_view(balance: LedgerBalance) -> BalanceView:
    return BalanceView(balance.account_id, money_view(balance.outstanding), balance.entry_count)


def credit_balance_view(balance: CreditBalance) -> BalanceView:
    return BalanceView(balance.account_id, money_view(balance.available), balance.movement_count)


def invoice_view(invoice: Invoice) -> InvoiceView:
    totals = invoice.totals
    period = invoice.period
    return InvoiceView(
        id=invoice.id,
        account_id=invoice.account_id,
        state=invoice.state.value,
        number=invoice.number.formatted if invoice.number else "",
        currency=invoice.currency.value,
        period_start=period.start,
        period_end=period.end,
        line_count=len(invoice.lines),
        total=money_view(totals.total) if totals else None,
        amount_due=money_view(totals.amount_due) if totals else None,
        issued_at=invoice.issued_at,
        due_at=invoice.due_at,
        evidence=invoice.evidence,
    )


def payment_view(payment: Payment) -> PaymentView:
    return PaymentView(
        id=payment.id,
        invoice_id=payment.invoice_id,
        state=payment.state.value,
        method=payment.method.value,
        amount=money_view(payment.amount),
        attempt=payment.attempt,
        provider_code=payment.provider_code,
    )


def refund_view(refund: Refund) -> RefundView:
    return RefundView(
        id=refund.id,
        payment_id=refund.payment_id,
        invoice_id=refund.invoice_id,
        state=refund.state.value,
        reason=refund.reason.value,
        destination=refund.destination.value,
        amount=money_view(refund.amount),
        approved_by=refund.approval.approver_id if refund.approval else "",
    )


def tax_profile_view(profile: TaxProfile) -> TaxProfileView:
    return TaxProfileView(
        id=profile.id,
        account_id=profile.account_id,
        regime=profile.regime.value,
        taxpayer_kind=profile.taxpayer_kind.value,
        country=profile.country,
        place_of_supply=profile.place_of_supply,
        registration_numbers=tuple(r.number for r in profile.registrations),
    )


def tax_determination_view(determination: TaxDetermination) -> TaxDeterminationView:
    return TaxDeterminationView(
        id=determination.id,
        invoice_id=determination.invoice_id,
        regime=determination.regime.value,
        treatment=determination.treatment.value,
        taxable_amount=money_view(determination.taxable_amount),
        tax_total=money_view(determination.tax_total),
        gst_split=determination.gst_split.value,
        adapter_code=determination.adapter_code,
    )


def contract_view(contract: Contract) -> ContractView:
    term = contract.term
    return ContractView(
        id=contract.id,
        account_id=contract.account_id,
        code=contract.code,
        state=contract.state.value,
        settlement=contract.settlement.value,
        plan_code=contract.plan_code,
        starts_at=term.starts_at if term else None,
        ends_at=term.ends_at if term else None,
    )


def license_view(license_: License) -> LicenseView:
    return LicenseView(
        id=license_.id,
        account_id=license_.account_id,
        kind=license_.kind.value,
        state=license_.state.value,
        valid_from=license_.valid_from,
        valid_until=license_.valid_until,
        entitlement_codes=license_.entitlement_codes,
    )
