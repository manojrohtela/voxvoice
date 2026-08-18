"""The Foundation context (M9.3) — what the Application is given, never what it owns.

The Billing Foundation (M9.2) ships repository **interfaces** and stateless services but no
implementations, by design. The Application therefore receives its dependencies: a set of
repositories, the Foundation factory, and the Foundation services it coordinates. The
persistence layer (M9.4) supplies real repositories at the composition root without
touching a use case.

This is composition, not business logic: `BillingContext` holds references and nothing
else — no rule, no derivation, no state.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from billing.foundation import (
    ALL_LIFECYCLES,
    BillingAccountRepository,
    BillingFactory,
    BillingValidator,
    ContractRepository,
    ContractService,
    CreditRepository,
    EntitlementGrantRepository,
    EntitlementSnapshotRepository,
    FeatureRepository,
    InvoiceNumberAllocator,
    InvoiceRepository,
    InvoiceTotalsService,
    LedgerRepository,
    LicenseRepository,
    MeteringService,
    MeterRepository,
    PaymentRepository,
    PeriodService,
    PlanRepository,
    PriceBookRepository,
    ProrationService,
    QuotaService,
    RefundRepository,
    SubscriptionLifecycleService,
    SubscriptionRepository,
    TaxDeterminationRepository,
    TaxProfileRepository,
    UsageAggregateRepository,
    UsageRecordRepository,
)


@dataclass(frozen=True, slots=True)
class BillingRepositories:
    """The persistence seams the Application coordinates. All interfaces (M9.2); the
    implementations land at M9.4 and are injected here."""

    accounts: BillingAccountRepository
    plans: PlanRepository
    meters: MeterRepository
    price_books: PriceBookRepository
    subscriptions: SubscriptionRepository
    grants: EntitlementGrantRepository
    snapshots: EntitlementSnapshotRepository
    usage: UsageRecordRepository
    aggregates: UsageAggregateRepository
    ledger: LedgerRepository
    credits: CreditRepository
    invoices: InvoiceRepository
    invoice_numbers: InvoiceNumberAllocator
    payments: PaymentRepository
    refunds: RefundRepository
    tax_profiles: TaxProfileRepository
    tax_determinations: TaxDeterminationRepository
    contracts: ContractRepository
    licenses: LicenseRepository
    # M9.6 (additive, defaulted so every existing construction still type-checks and runs):
    # the commercial catalog's own seams. `features` completes the M9.2 catalog trio
    # (plans/meters were already here); `catalog` carries the six M9.6 repositories.
    features: FeatureRepository | None = None
    catalog: object | None = None
    # M9.7 (additive, defaulted): the Pricing Engine's own seams. Grouped in one value object
    # so pricing can be wired — or deliberately left unwired — as a single decision.
    pricing: object | None = None
    # M9.8 (additive, defaulted): the Billing Accounts domain's own seams.
    commercial_accounts: object | None = None
    # M9.9 (additive, defaulted): the Subscription Engine's own seams.
    subscriptions_engine: object | None = None
    # M9.10 (additive, defaulted): the Entitlement Engine's own seams.
    entitlements_engine: object | None = None
    # M9.11 (additive, defaulted): the Metering Engine's own seams.
    metering_engine: object | None = None
    # M9.12 (additive, defaulted): the Rating Engine's own seams.
    rating_engine: object | None = None
    # M9.13 (additive, defaulted): the Credit & Revenue Ledger's own seams.
    ledger_engine: object | None = None
    # M9.14 (additive, defaulted): the Invoice Engine's own seams.
    invoice_engine: object | None = None
    # M9.15 (additive, defaulted): the Tax Determination Engine's own seams.
    tax_engine: object | None = None
    # M9.16 (additive, defaulted): the Payment Engine's own seams.
    payment_engine: object | None = None
    # M9.17 (additive, defaulted): the Collections Engine's own seams.
    collections_engine: object | None = None
    # M9.18 (additive, defaulted): the Revenue Recognition Engine's own seams.
    revenue_engine: object | None = None
    # M9.19 (additive, defaulted): the Reconciliation & Financial Integrity Engine's seams.
    reconciliation_engine: object | None = None
    # M9.20 (additive, defaulted): the Financial Integrity Correlation Engine's seams.
    incident_engine: object | None = None
    # M9.21 (additive, defaulted): the Financial Investigation & Audit Evidence Engine's seams.
    investigation_engine: object | None = None
    # M9.22 (additive, defaulted): the Compliance & Regulatory Reporting Engine's seams.
    reporting_engine: object | None = None
    consolidation_engine: object | None = None


@dataclass(frozen=True, slots=True)
class BillingServices:
    """The stateless Foundation services the Application coordinates. Constructed here so
    a use case never builds domain behaviour — it only calls it."""

    lifecycle: SubscriptionLifecycleService = field(
        default_factory=lambda: SubscriptionLifecycleService(ALL_LIFECYCLES["subscription"])
    )
    periods: PeriodService = field(default_factory=PeriodService)
    proration: ProrationService = field(default_factory=ProrationService)
    metering: MeteringService = field(default_factory=MeteringService)
    quotas: QuotaService = field(default_factory=QuotaService)
    totals: InvoiceTotalsService = field(default_factory=InvoiceTotalsService)
    contracts: ContractService = field(default_factory=ContractService)
    validator: BillingValidator = field(default_factory=BillingValidator)


@dataclass(frozen=True, slots=True)
class BillingContext:
    """Everything the Application orchestrates: the Foundation factory, its repositories,
    and its services. Immutable; holds references only."""

    factory: BillingFactory
    repositories: BillingRepositories
    services: BillingServices = field(default_factory=BillingServices)
    # M9.6 (additive): the commercial-catalog repositories, when wired. `None` means the
    # Application is exactly what M9.3 assembled — the catalog is additive, never required.
    catalog: object | None = None
    # M9.7 (additive): the pricing repositories, when wired. Same rule — `None` means the
    # Pricing Engine is simply absent, not half-present.
    pricing: object | None = None
    # M9.8 (additive): the billing-account repositories, when wired. Same rule again.
    commercial_accounts: object | None = None
    # M9.9 (additive): the subscription repositories, when wired. Same rule again.
    subscriptions_engine: object | None = None
    # M9.10 (additive): the entitlement repositories, when wired. Same rule again.
    entitlements_engine: object | None = None
    # M9.11 (additive): the metering repositories, when wired. Same rule again.
    metering_engine: object | None = None
    # M9.12 (additive): the rating repositories, when wired. Same rule again.
    rating_engine: object | None = None
    # M9.13 (additive): the ledger repositories, when wired. Same rule again.
    ledger_engine: object | None = None
    # M9.14 (additive): the invoice repositories, when wired. Same rule again.
    invoice_engine: object | None = None
    # M9.15 (additive): the tax repositories, when wired. Same rule again.
    tax_engine: object | None = None
    # M9.16 (additive): the payment repositories, when wired. Same rule again.
    payment_engine: object | None = None
    # M9.17 (additive): the collections repositories, when wired. Same rule again — an
    # unwired Collections engine is absent, never half-present.
    collections_engine: object | None = None
    # M9.18 (additive): the recognition repositories, when wired. Same rule again.
    revenue_engine: object | None = None
    # M9.19 (additive): the integrity repositories, when wired. Same rule again.
    reconciliation_engine: object | None = None
    # M9.20 (additive): the correlation repositories, when wired. Same rule again.
    incident_engine: object | None = None
    # M9.21 (additive): the investigation repositories, when wired. Same rule again — an unwired
    # Investigation engine is absent, never half-present.
    investigation_engine: object | None = None
    # M9.22 (additive): the reporting repositories, when wired. Same rule again.
    reporting_engine: object | None = None
    consolidation_engine: object | None = None
