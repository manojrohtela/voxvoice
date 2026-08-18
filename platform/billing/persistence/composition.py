"""Persistence composition root (M9.4).

The one place persistence is wired into the Foundation and the Application. This module
imports `billing.foundation` + `billing.application` + the SQL implementations; **neither
of those layers imports persistence** — dependency direction preserved, both layers
byte-for-byte unchanged and unaware a database exists.

`build_persistent_billing_application(...)` returns a fully wired `BillingApplication` whose
repositories are SQL-backed, whose billing facts are durable, and whose Unit of Work is the
transactional `SqlBillingUnitOfWork`. It swaps only the seams the abstractions were designed
to swap; not one use case, handler, command, or query changes.

Two properties are worth stating plainly, because both are ADR obligations rather than
implementation taste:

* **Deny-by-default authorization survives production wiring.** `authorizer` is optional and
  defaults to nothing, which means the Application's `DenyAllAuthorizer` stands. Billing
  owns no RBAC (ADR-0023 §5.3); the Control Plane injects the §A11.2 evaluator here at
  M9.18. A build that forgets to is inert, not open.
* **This is not wired into any runtime composition root.** ADR-0031 CR2: Billing is absent
  from `platform/bootstrap.py`, so a total Billing outage cannot affect a conversation or a
  deployment. Wiring is an explicit, control-plane-only act — this function.
"""

from __future__ import annotations

from dataclasses import dataclass

from billing.application import (
    AccountRepositories,
    ApplicationEventEmitter,
    BillingApplication,
    BillingApplicationFactory,
    BillingAuthorizer,
    BillingRepositories,
    BillingServices,
    CatalogRepositories,
    CollectionRepositories,
    EntitlementRepositories,
    IncidentRepositories,
    InvestigationRepositories,
    InvoiceRepositories,
    LedgerRepositories,
    MeteringRepositories,
    PaymentRepositories,
    PricingRepositories,
    RatingRepositories,
    ReconciliationRepositories,
    RecordingApplicationEventEmitter,
    ReportingRepositories,
    RevenueRepositories,
    SubscriptionRepositories,
    TaxRepositories,
)
from billing.foundation import (
    AccountFactory,
    BillingFactory,
    BillingValidator,
    CatalogFactory,
    Clock,
    CollectionFactory,
    DefaultIdGenerator,
    EntitlementFactory,
    IdGenerator,
    IncidentFactory,
    IntegrityFactory,
    InvestigationFactory,
    InvoiceFactory,
    LedgerFactory,
    MeteringFactory,
    PaymentFactory,
    PricingFactory,
    RatingFactory,
    ReportingFactory,
    RevenueFactory,
    SubscriptionFactory,
    SystemClock,
    TaxFactory,
)

from .account_repositories import (
    SqlAccountActivityRepository,
    SqlAccountNumberAllocator,
    SqlCommercialAccountRepository,
)
from .catalog_repositories import (
    SqlBundleRepository,
    SqlCatalogActivityRepository,
    SqlCatalogVersionRepository,
    SqlEditionRepository,
    SqlFeatureGroupRepository,
    SqlProductRepository,
)
from .collection_repositories import (
    SqlCollectionActionRepository,
    SqlCollectionActivityRepository,
    SqlCollectionAttemptRepository,
    SqlCollectionCaseRepository,
    SqlCollectionDecisionRepository,
    SqlCollectionPolicyRepository,
)
from .consolidation_repositories import (
    SqlConsolidationEventRepository,
    SqlConsolidationExceptionRepository,
    SqlConsolidationPeriodRepository,
    SqlConsolidationRepository,
    SqlConsolidationSnapshotRepository,
    SqlConsolidationValidationRepository,
    SqlScopeDefinitionRepository,
    SqlScopeSnapshotRepository,
    SqlSourceBindingRepository,
)
from .database import BillingDatabase
from .entitlement_repositories import (
    SqlEntitlementActivityRepository,
    SqlEntitlementBundleRepository,
    SqlEntitlementDefinitionRepository,
    SqlEntitlementProfileRepository,
    SqlEntitlementVersionRepository,
)
from .event_store import SqlBillingEventStore
from .incident_repositories import (
    SqlCorrelationCorrectionRepository,
    SqlFindingLinkRepository,
    SqlIncidentEventRepository,
    SqlIncidentMergeRepository,
    SqlIncidentRepairRepository,
    SqlIncidentRepository,
)
from .investigation_repositories import (
    SqlAuditPackageRepository,
    SqlEvidenceRepository,
    SqlInvestigationConclusionRepository,
    SqlInvestigationEventRepository,
    SqlInvestigationObservationRepository,
    SqlInvestigationQuestionRepository,
    SqlInvestigationRepository,
)
from .invoice_repositories import (
    SqlCommercialInvoiceRepository,
    SqlInvoiceActivityRepository,
    SqlInvoiceRevisionRepository,
    SqlInvoiceSnapshotRepository,
    SqlNumberSeriesAllocator,
    SqlRenderedDocumentRepository,
)
from .ledger_repositories import (
    SqlAccountingPeriodRepository,
    SqlBalanceCheckpointRepository,
    SqlCommercialCreditRepository,
    SqlCreditApplicationRepository,
    SqlJournalEntryRepository,
    SqlLedgerActivityRepository,
)
from .metering_repositories import (
    SqlCommercialMeterRepository,
    SqlCommercialUsageRepository,
    SqlMeteringActivityRepository,
    SqlQuarantineEntryRepository,
    SqlUsageAggregateSnapshotRepository,
    SqlUsageCorrectionRepository,
    SqlUsagePeriodRepository,
)
from .migrations import upgrade_all
from .payment_repositories import (
    SqlCommercialRefundRepository,
    SqlPaymentActivityRepository,
    SqlPaymentAttemptRepository,
    SqlPaymentIntentRepository,
    SqlPaymentReconciliationRepository,
    SqlPaymentRoutePolicyRepository,
    SqlProviderEventRepository,
    SqlProviderOperationRepository,
)
from .pricing_repositories import (
    SqlCouponRepository,
    SqlDiscountPolicyRepository,
    SqlPriceBookVersionRepository,
    SqlPricingActivityRepository,
    SqlPricingBookRepository,
    SqlPricingVersionRepository,
    SqlPromotionRepository,
)
from .rating_repositories import (
    SqlMinimumCommitmentRepository,
    SqlRatedChargeRepository,
    SqlRatingActivityRepository,
    SqlRatingRunRepository,
    SqlRatingSnapshotRepository,
)
from .reconciliation_repositories import (
    SqlIntegrityActivityRepository,
    SqlIntegrityFindingRepository,
    SqlIntegrityResolutionRepository,
    SqlIntegrityRunRepository,
    SqlRepairRequestRepository,
)
from .reporting_repositories import (
    SqlReportDefinitionRepository,
    SqlReportEventRepository,
    SqlReportExceptionRepository,
    SqlReportingPeriodRepository,
    SqlReportManifestRepository,
    SqlReportRepository,
    SqlReportSnapshotRepository,
    SqlReportValidationRepository,
    SqlSubmissionPackageRepository,
)
from .repositories import (
    SqlBillingAccountRepository,
    SqlBillingRunRepository,
    SqlContractRepository,
    SqlCreditNoteRepository,
    SqlCreditRepository,
    SqlDisputeRepository,
    SqlDunningRepository,
    SqlEntitlementGrantRepository,
    SqlEntitlementSnapshotRepository,
    SqlFeatureRepository,
    SqlInvoiceNumberAllocator,
    SqlInvoiceRepository,
    SqlLedgerRepository,
    SqlLicenseRepository,
    SqlMeterRepository,
    SqlPaymentInstrumentRepository,
    SqlPaymentRepository,
    SqlPlanRepository,
    SqlPriceBookRepository,
    SqlReconciliationRepository,
    SqlRefundRepository,
    SqlSubscriptionRepository,
    SqlTaxDeterminationRepository,
    SqlTaxProfileRepository,
    SqlUsageAggregateRepository,
    SqlUsageQuarantineRepository,
    SqlUsageRecordRepository,
)
from .revenue_repositories import (
    SqlRecognitionActivityRepository,
    SqlRecognitionAdjustmentRepository,
    SqlRecognitionEntryRepository,
    SqlRecognitionPolicyRepository,
    SqlRevenueScheduleRepository,
)
from .subscription_repositories import (
    SqlCommercialSubscriptionRepository,
    SqlSubscriptionActivityRepository,
    SqlSubscriptionNumberAllocator,
    SqlSubscriptionVersionRepository,
)
from .tax_repositories import (
    SqlTaxActivityRepository,
    SqlTaxAssessmentRepository,
    SqlTaxClassificationRepository,
    SqlTaxExemptionRepository,
    SqlTaxJurisdictionRepository,
    SqlTaxNexusRepository,
    SqlTaxRateRepository,
)
from .unit_of_work import BillingUnitOfWorkFactory


@dataclass(frozen=True, slots=True)
class PersistentBillingApplication:
    """The wired result: the Application plus the collaborators a caller may legitimately
    need to operate it (`create_all()`, `close()`, read the durable fact stream)."""

    application: BillingApplication
    database: BillingDatabase
    events: SqlBillingEventStore


def build_repositories(database: BillingDatabase) -> BillingRepositories:
    """The 19 seams the Application declares, backed by SQL. The remaining 10 contracts
    (features, catalog snapshots, credit notes, billing runs, instruments, dunning,
    reconciliation, disputes, quarantine, and the event stream) are implemented and used
    by the Foundation services and the fact stream; they join `BillingRepositories` when the
    use cases that need them land (M9.10–M9.16), additively and without a signature change
    here."""
    return BillingRepositories(
        accounts=SqlBillingAccountRepository(database),
        plans=SqlPlanRepository(database),
        meters=SqlMeterRepository(database),
        price_books=SqlPriceBookRepository(database),
        subscriptions=SqlSubscriptionRepository(database),
        grants=SqlEntitlementGrantRepository(database),
        snapshots=SqlEntitlementSnapshotRepository(database),
        usage=SqlUsageRecordRepository(database),
        aggregates=SqlUsageAggregateRepository(database),
        ledger=SqlLedgerRepository(database),
        credits=SqlCreditRepository(database),
        invoices=SqlInvoiceRepository(database),
        invoice_numbers=SqlInvoiceNumberAllocator(database),
        payments=SqlPaymentRepository(database),
        refunds=SqlRefundRepository(database),
        tax_profiles=SqlTaxProfileRepository(database),
        tax_determinations=SqlTaxDeterminationRepository(database),
        contracts=SqlContractRepository(database),
        licenses=SqlLicenseRepository(database),
        # M9.6 (additive) — the commercial catalog's seams.
        features=SqlFeatureRepository(database),
        catalog=build_catalog_repositories(database),
        # M9.7 (additive) — the Pricing Engine's seams.
        pricing=build_pricing_repositories(database),
        # M9.8 (additive) — the Billing Accounts domain's seams.
        commercial_accounts=build_account_repositories(database),
        # M9.9 (additive) — the Subscription Engine's seams.
        subscriptions_engine=build_subscription_repositories(database),
        # M9.10 (additive) — the Entitlement Engine's seams.
        entitlements_engine=build_entitlement_repositories(database),
        # M9.11 (additive) — the Metering Engine's seams.
        metering_engine=build_metering_repositories(database),
        # M9.12 (additive) — the Rating Engine's seams.
        rating_engine=build_rating_repositories(database),
        # M9.13 (additive) — the Credit & Revenue Ledger's seams.
        ledger_engine=build_ledger_repositories(database),
        # M9.14 (additive) — the Invoice Engine's seams.
        invoice_engine=build_invoice_repositories(database),
        # M9.15 (additive) — the Tax Determination Engine's seams.
        tax_engine=build_tax_repositories(database),
        payment_engine=build_payment_repositories(database),
        # M9.17 (additive) — the Dunning & Collections seams.
        collections_engine=build_collection_repositories(database),
        # M9.18 (additive) — the Revenue Recognition seams.
        revenue_engine=build_revenue_repositories(database),
        # M9.19 (additive) — the Reconciliation & Financial Integrity seams.
        reconciliation_engine=build_reconciliation_repositories(database),
        # M9.20 (additive) — the Financial Integrity Correlation seams.
        incident_engine=build_incident_repositories(database),
        investigation_engine=build_investigation_repositories(database),
        reporting_engine=build_reporting_repositories(database),
        # M9.23 (additive) — the Period Scope \& Consolidation seams.
        consolidation_engine=build_consolidation_repositories(database),
    )


def build_catalog_repositories(database: BillingDatabase) -> CatalogRepositories:
    """The six M9.6 catalog seams, backed by SQL. Grouped in their own value object so the
    catalog can be wired — or deliberately left unwired — as one decision."""
    return CatalogRepositories(
        products=SqlProductRepository(database),
        editions=SqlEditionRepository(database),
        bundles=SqlBundleRepository(database),
        feature_groups=SqlFeatureGroupRepository(database),
        versions=SqlCatalogVersionRepository(database),
        activity=SqlCatalogActivityRepository(database),
    )


def build_pricing_repositories(database: BillingDatabase) -> PricingRepositories:
    """The seven M9.7 pricing seams, backed by SQL. Grouped in their own value object so the
    Pricing Engine can be wired — or deliberately left unwired — as one decision."""
    return PricingRepositories(
        books=SqlPricingBookRepository(database),
        book_versions=SqlPriceBookVersionRepository(database),
        discounts=SqlDiscountPolicyRepository(database),
        promotions=SqlPromotionRepository(database),
        coupons=SqlCouponRepository(database),
        versions=SqlPricingVersionRepository(database),
        activity=SqlPricingActivityRepository(database),
    )


def build_account_repositories(database: BillingDatabase) -> AccountRepositories:
    """The three M9.8 billing-account seams, backed by SQL. Grouped in their own value object
    so the domain can be wired — or deliberately left unwired — as one decision."""
    return AccountRepositories(
        accounts=SqlCommercialAccountRepository(database),
        activity=SqlAccountActivityRepository(database),
        numbers=SqlAccountNumberAllocator(database),
    )


def build_subscription_repositories(database: BillingDatabase) -> SubscriptionRepositories:
    """The four M9.9 subscription seams, backed by SQL. Grouped in their own value object so
    the Subscription Engine can be wired — or deliberately left unwired — as one decision."""
    return SubscriptionRepositories(
        subscriptions=SqlCommercialSubscriptionRepository(database),
        versions=SqlSubscriptionVersionRepository(database),
        activity=SqlSubscriptionActivityRepository(database),
        numbers=SqlSubscriptionNumberAllocator(database),
    )


def build_entitlement_repositories(database: BillingDatabase) -> EntitlementRepositories:
    """The five M9.10 entitlement seams, backed by SQL. Grouped in their own value object so the
    Entitlement Engine can be wired — or deliberately left unwired — as one decision."""
    return EntitlementRepositories(
        definitions=SqlEntitlementDefinitionRepository(database),
        bundles=SqlEntitlementBundleRepository(database),
        profiles=SqlEntitlementProfileRepository(database),
        versions=SqlEntitlementVersionRepository(database),
        activity=SqlEntitlementActivityRepository(database),
    )


def build_metering_repositories(database: BillingDatabase) -> MeteringRepositories:
    """The seven M9.11 metering seams, backed by SQL. Grouped in their own value object so the
    Metering Engine can be wired — or deliberately left unwired — as one decision."""
    return MeteringRepositories(
        meters=SqlCommercialMeterRepository(database),
        usage=SqlCommercialUsageRepository(database),
        periods=SqlUsagePeriodRepository(database),
        aggregates=SqlUsageAggregateSnapshotRepository(database),
        quarantine=SqlQuarantineEntryRepository(database),
        corrections=SqlUsageCorrectionRepository(database),
        activity=SqlMeteringActivityRepository(database),
    )


def build_rating_repositories(database: BillingDatabase) -> RatingRepositories:
    """The five M9.12 rating seams, backed by SQL. Grouped in their own value object so the
    Rating Engine can be wired — or deliberately left unwired — as one decision."""
    return RatingRepositories(
        runs=SqlRatingRunRepository(database),
        charges=SqlRatedChargeRepository(database),
        snapshots=SqlRatingSnapshotRepository(database),
        commitments=SqlMinimumCommitmentRepository(database),
        activity=SqlRatingActivityRepository(database),
    )


def build_ledger_repositories(database: BillingDatabase) -> LedgerRepositories:
    """The six M9.13 ledger seams, backed by SQL. Grouped in their own value object so the Credit &
    Revenue Ledger can be wired — or deliberately left unwired — as one decision."""
    return LedgerRepositories(
        entries=SqlJournalEntryRepository(database),
        periods=SqlAccountingPeriodRepository(database),
        checkpoints=SqlBalanceCheckpointRepository(database),
        credits=SqlCommercialCreditRepository(database),
        applications=SqlCreditApplicationRepository(database),
        activity=SqlLedgerActivityRepository(database),
    )


def build_invoice_repositories(database: BillingDatabase) -> InvoiceRepositories:
    """The six M9.14 invoice seams, backed by SQL. Grouped in their own value object so the Invoice
    Engine can be wired — or deliberately left unwired — as one decision.

    `numbers` is the gapless allocator, and it is a *repository* rather than a service for exactly
    one reason: it must allocate inside the caller's transaction, so a crash between allocation and
    issue rolls both back and the number is never consumed."""
    return InvoiceRepositories(
        documents=SqlCommercialInvoiceRepository(database),
        snapshots=SqlInvoiceSnapshotRepository(database),
        revisions=SqlInvoiceRevisionRepository(database),
        numbers=SqlNumberSeriesAllocator(database),
        renderings=SqlRenderedDocumentRepository(database),
        activity=SqlInvoiceActivityRepository(database),
    )


def build_tax_repositories(database: BillingDatabase) -> TaxRepositories:
    """The eight M9.15 tax seams, backed by SQL. Grouped in their own value object so the Tax
    Determination Engine can be wired — or deliberately left unwired — as one decision.

    `determinations` is M9.2's `TaxDeterminationRepository`, which this milestone finally implements
    against: it is Tax's own seam, and holding it here rather than reaching through the shared
    context is what keeps the "writes only to its own repositories" assertion a structural check."""
    return TaxRepositories(
        jurisdictions=SqlTaxJurisdictionRepository(database),
        classifications=SqlTaxClassificationRepository(database),
        nexus=SqlTaxNexusRepository(database),
        exemptions=SqlTaxExemptionRepository(database),
        rates=SqlTaxRateRepository(database),
        assessments=SqlTaxAssessmentRepository(database),
        activity=SqlTaxActivityRepository(database),
        determinations=SqlTaxDeterminationRepository(database),
    )


def build_payment_repositories(database: BillingDatabase) -> PaymentRepositories:
    """The eight M9.16 payment seams, backed by SQL. Grouped in their own value object so the
    Payment Engine can be wired — or deliberately left unwired — as one decision.

    `operations` is the one that matters: it is a *repository* rather than a service because the
    claim on an operation key must be a database constraint inside the caller's transaction. A
    service holding a lock in memory would protect one process from itself and nothing else."""
    return PaymentRepositories(
        routes=SqlPaymentRoutePolicyRepository(database),
        intents=SqlPaymentIntentRepository(database),
        attempts=SqlPaymentAttemptRepository(database),
        operations=SqlProviderOperationRepository(database),
        events=SqlProviderEventRepository(database),
        refunds=SqlCommercialRefundRepository(database),
        reconciliations=SqlPaymentReconciliationRepository(database),
        activity=SqlPaymentActivityRepository(database),
    )


def build_collection_repositories(database: BillingDatabase) -> CollectionRepositories:
    """The six M9.17 collection seams, backed by SQL. Grouped in their own value object so the
    Collections Engine can be wired — or deliberately left unwired — as one decision.

    `cases` is the one that matters: `advance` is a compare-and-set inside the caller's
    transaction, which is why it is a repository rather than a service. A service holding a lock in
    memory would protect one process from itself and nothing else — the same reason the payment
    operation claim lives here."""
    return CollectionRepositories(
        policies=SqlCollectionPolicyRepository(database),
        cases=SqlCollectionCaseRepository(database),
        actions=SqlCollectionActionRepository(database),
        attempts=SqlCollectionAttemptRepository(database),
        decisions=SqlCollectionDecisionRepository(database),
        activity=SqlCollectionActivityRepository(database),
    )


def build_revenue_repositories(database: BillingDatabase) -> RevenueRepositories:
    """The five M9.18 recognition seams, backed by SQL. Grouped in their own value object so the
    Revenue Recognition Engine can be wired — or deliberately left unwired — as one decision.

    `entries` is the one that matters: its `recognize` is a conditional UPDATE inside the caller's
    transaction, which is why it is a repository rather than a service. Two ledger entries for one
    month of revenue cannot be undone by noticing later, so the arbitrator has to be the
    database."""
    return RevenueRepositories(
        policies=SqlRecognitionPolicyRepository(database),
        schedules=SqlRevenueScheduleRepository(database),
        entries=SqlRecognitionEntryRepository(database),
        adjustments=SqlRecognitionAdjustmentRepository(database),
        activity=SqlRecognitionActivityRepository(database),
    )


def build_reconciliation_repositories(
    database: BillingDatabase,
) -> ReconciliationRepositories:
    """The five M9.19 integrity seams, backed by SQL. Grouped in their own value object so the
    Reconciliation Engine can be wired — or deliberately left unwired — as one decision.

    `findings` is the one that matters: its `advance` is a conditional UPDATE on `version` inside
    the caller's transaction, which is why it is a repository rather than a service. A stale worker
    overwriting somebody else's resolution cannot be undone by noticing later, so the arbitrator has
    to be the database.

    Note what is **not** here: no ledger, invoice, tax, payment or recognition repository. M9.19
    reads those through the owning domain's own seams and writes to none of them."""
    return ReconciliationRepositories(
        runs=SqlIntegrityRunRepository(database),
        findings=SqlIntegrityFindingRepository(database),
        requests=SqlRepairRequestRepository(database),
        resolutions=SqlIntegrityResolutionRepository(database),
        activity=SqlIntegrityActivityRepository(database),
    )


def build_incident_repositories(database: BillingDatabase) -> IncidentRepositories:
    """The six M9.20 correlation seams, backed by SQL. Grouped in their own value object so the
    Financial Integrity Correlation Engine can be wired — or deliberately left unwired — as one
    decision.

    `incidents` is the one that matters: its `advance` is a conditional UPDATE on `version` inside
    the caller's transaction, which is why it is a repository rather than a service. A stale worker
    overwriting somebody else's conclusion about a financial incident cannot be undone by noticing
    later, so the arbitrator has to be the database.

    Note what is **not** here: no reconciliation, ledger, invoice, tax, payment or recognition
    repository. M9.20 reads those through the owning domain's own seams and writes to none of them —
    including M9.19's."""
    return IncidentRepositories(
        incidents=SqlIncidentRepository(database),
        links=SqlFindingLinkRepository(database),
        events=SqlIncidentEventRepository(database),
        merges=SqlIncidentMergeRepository(database),
        corrections=SqlCorrelationCorrectionRepository(database),
        repairs=SqlIncidentRepairRepository(database),
    )


def build_investigation_repositories(database: BillingDatabase) -> InvestigationRepositories:
    """The seven M9.21 investigation seams, backed by SQL. Grouped in their own value object so
    the Financial Investigation & Audit Evidence Engine can be wired — or deliberately left
    unwired — as one decision.

    `investigations` carries the compare-and-set: its `advance` is a conditional UPDATE on
    `version` inside the caller's transaction. A stale investigator overwriting somebody else's
    conclusion cannot be undone by noticing later, so the arbitrator has to be the database.

    `evidence`, `events`, `conclusions` and `packages` have **no update path at all** — history is
    evidence, and evidence is not edited.

    Note what is **not** here: no incident, reconciliation, ledger, invoice, tax, payment or
    recognition repository. M9.21 reads those through the owning domain's own seams and writes to
    none of them — including M9.20's."""
    return InvestigationRepositories(
        investigations=SqlInvestigationRepository(database),
        evidence=SqlEvidenceRepository(database),
        questions=SqlInvestigationQuestionRepository(database),
        observations=SqlInvestigationObservationRepository(database),
        conclusions=SqlInvestigationConclusionRepository(database),
        events=SqlInvestigationEventRepository(database),
        packages=SqlAuditPackageRepository(database),
    )


def build_reporting_repositories(database: BillingDatabase) -> ReportingRepositories:
    """The nine M9.22 reporting seams, backed by SQL. Grouped in their own value object so the
    Compliance & Regulatory Reporting Engine can be wired — or deliberately left unwired — as one
    decision.

    `reports` carries the compare-and-set: its `advance` is a conditional UPDATE on `version`
    inside the caller's transaction. A stale worker overwriting a filing somebody else approved
    cannot be undone by noticing later, so the arbitrator has to be the database.

    `manifests`, `snapshots`, `submissions` and `events` have **no update path at all** — a filing
    is evidence, and evidence is not edited.

    Note what is **not** here: no ledger, invoice, tax, payment, recognition, collection,
    reconciliation, incident or investigation repository. M9.22 reads those through the owning
    domain's own seams and writes to none of them."""
    return ReportingRepositories(
        definitions=SqlReportDefinitionRepository(database),
        periods=SqlReportingPeriodRepository(database),
        manifests=SqlReportManifestRepository(database),
        reports=SqlReportRepository(database),
        exceptions=SqlReportExceptionRepository(database),
        snapshots=SqlReportSnapshotRepository(database),
        submissions=SqlSubmissionPackageRepository(database),
        events=SqlReportEventRepository(database),
        validations=SqlReportValidationRepository(database),
    )


def build_consolidation_repositories(database: BillingDatabase):
    """The M9.23 seams. **The database is the arbitrator**, for the same reason M9.22's is.

    Two workers opening one consolidation, collecting one batch, or finalizing one run all resolve
    at the primary key or at the compare-and-set — never in Python, where a stale read is exactly
    the thing the check exists to catch.

    `snapshots`, `bindings`, `results` and `events` have **no update path at all**. A resolved
    membership is what a finalized artifact means by "these accounts"; editing it later would
    rewrite history without leaving a trace.

    `accounts` is M9.8's account book, injected as `ScopeAccountReader` — a read-only view onto a
    hierarchy this milestone consumes and cannot change. Note what is **not** here: no ledger,
    invoice, tax, payment, recognition, collection, reconciliation, incident, investigation or
    reporting repository. M9.23 reads those through the owning domain's own seams and writes to
    none of them."""
    from billing.application.consolidation_engine import ConsolidationRepositories

    return ConsolidationRepositories(
        definitions=SqlScopeDefinitionRepository(database),
        snapshots=SqlScopeSnapshotRepository(database),
        periods=SqlConsolidationPeriodRepository(database),
        consolidations=SqlConsolidationRepository(database),
        bindings=SqlSourceBindingRepository(database),
        exceptions=SqlConsolidationExceptionRepository(database),
        validations=SqlConsolidationValidationRepository(database),
        results=SqlConsolidationSnapshotRepository(database),
        events=SqlConsolidationEventRepository(database),
        accounts=SqlCommercialAccountRepository(database),
    )


def build_auxiliary_repositories(database: BillingDatabase) -> dict[str, object]:
    """The contracts implemented for the Foundation services and background processes that
    are not (yet) Application seams. Returned as a mapping so a later milestone can wire one
    without this module gaining a new positional parameter."""
    return {
        "features": SqlFeatureRepository(database),
        "quarantine": SqlUsageQuarantineRepository(database),
        "credit_notes": SqlCreditNoteRepository(database),
        "runs": SqlBillingRunRepository(database),
        "instruments": SqlPaymentInstrumentRepository(database),
        "dunning": SqlDunningRepository(database),
        "reconciliation": SqlReconciliationRepository(database),
        "disputes": SqlDisputeRepository(database),
    }


def build_persistent_billing_application(
    *,
    url: str = "sqlite://",
    ids: IdGenerator | None = None,
    clock: Clock | None = None,
    create_all: bool = True,
    authorizer: BillingAuthorizer | None = None,
    emitter: ApplicationEventEmitter | None = None,
    database: BillingDatabase | None = None,
    metric_stream=None,  # M9.11: the platform's `metric.*` port; None ⇒ ingest nothing
    payment_providers=None,  # M9.16: a `ProviderRegistry`; None ⇒ collect nothing at all
    collection_notifiers=None,  # M9.17: a `NotifierRegistry`; None ⇒ communicate nothing
) -> PersistentBillingApplication:
    """A production-shaped Billing Application over a real database.

    Pass a `SequentialIdGenerator` + `FixedClock` for deterministic persistence tests, or an
    existing `database` to open a **second** Application over the same storage — which is how
    the cross-instance durability tests prove data actually landed in the database rather
    than in some object's memory.
    """
    ids = ids or DefaultIdGenerator()
    clock = clock or SystemClock()
    database = database if database is not None else BillingDatabase(url)
    if create_all:
        database.create_all()

    # Billing facts are written into the caller's transaction, so the fact stream and the
    # revenue records commit or vanish together.
    event_store = SqlBillingEventStore(database)
    factory = BillingFactory(
        ids=ids, clock=clock, emitter=event_store, validator=BillingValidator()
    )
    # M9.6 — the catalog factory shares the same seams and the same durable fact stream, so
    # a `billing.catalog.*` fact commits with the catalog change it describes.
    catalog_factory = CatalogFactory(ids=ids, clock=clock, emitter=event_store)
    # M9.7 — likewise for pricing: a `billing.price_book.published` fact commits with the
    # price it describes, or neither happens.
    pricing_factory = PricingFactory(ids=ids, clock=clock, emitter=event_store)
    # M9.8 — the account factory shares the seams *and* the number allocator, so an account
    # number is drawn inside the same transaction as the account it identifies.
    account_factory = AccountFactory(
        ids=ids,
        clock=clock,
        emitter=event_store,
        numbers=SqlAccountNumberAllocator(database),
    )
    # M9.9 — likewise for subscriptions: the number is drawn inside the same transaction as the
    # agreement it identifies, so a rolled-back creation never burns one.
    subscription_factory = SubscriptionFactory(
        ids=ids,
        clock=clock,
        emitter=event_store,
        numbers=SqlSubscriptionNumberAllocator(database),
    )
    # M9.10 — the entitlement factory shares the seams and the durable fact stream, so a
    # `billing.entitlement.*` fact commits with the decision it describes, or neither happens.
    entitlement_factory = EntitlementFactory(ids=ids, clock=clock, emitter=event_store)
    # M9.11 — likewise for metering: a `billing.usage.*` fact commits with the usage record it
    # describes, or neither happens. **No metric stream is wired here**: the platform implements
    # `MetricStreamPort` and injects it, and an unwired port ingests nothing rather than
    # inventing usage.
    metering_factory = MeteringFactory(ids=ids, clock=clock, emitter=event_store)
    # M9.12 — likewise for rating: a `billing.rating.*` fact commits with the run it describes, or
    # neither happens. **No price is resolved here**: the Application calls M9.7's resolver against
    # the pinned pricing version, and rating owns no pricing decision of its own.
    rating_factory = RatingFactory(ids=ids, clock=clock, emitter=event_store)
    # M9.13 — and for the ledger: a `billing.ledger.*` fact commits with the entry it describes, or
    # neither happens. **No charge is recalculated here**: the Application reads M9.12's finalized
    # charges and records them, and the ledger owns no commercial decision of its own.
    ledger_factory = LedgerFactory(ids=ids, clock=clock, emitter=event_store)
    # M9.14 — and for invoicing: a `billing.invoice.*` fact commits with the document it describes,
    # or neither happens. **No figure is recalculated here**: the Application reads M9.13's journal
    # entries for a *closed* period and presents them, and the Invoice owns no financial decision.
    invoice_factory = InvoiceFactory(ids=ids, clock=clock, emitter=event_store)
    # M9.15 — and for tax: a `billing.tax.*` fact commits with the determination it describes, or
    # neither happens. **No rate is invented here**: the Application asks a `TaxRateProvider` built
    # from the org's declared rate evidence, and Tax owns no percentage of its own (ADR-0023 TX1).
    tax_factory = TaxFactory(ids=ids, clock=clock, emitter=event_store)
    # M9.16 — and for payments: a `billing.payment.*` fact commits with the attempt it describes,
    # or neither happens. **No provider is registered here**: the platform builds a
    # `ProviderRegistry` and injects it, and an empty registry collects nothing rather than
    # inventing a way to move money.
    payment_factory = PaymentFactory(ids=ids, clock=clock, emitter=event_store)
    # M9.17 — and for collections: a `billing.collection.*` fact commits with the case or
    # action it describes, or neither happens. **No notifier is registered here**: the platform
    # builds a `NotifierRegistry` and injects it, and an empty registry sends nothing rather
    # than inventing a way to contact a customer.
    collection_factory = CollectionFactory(ids=ids, clock=clock, emitter=event_store)
    # M9.18 — and for recognition: a `billing.revenue.*` fact commits with the schedule or entry
    # it describes, or neither happens. **No accounting method is chosen here**: an operator
    # publishes a recognition policy, and an installation with none recognizes nothing rather
    # than inventing when revenue is earned.
    revenue_factory = RevenueFactory(ids=ids, clock=clock, emitter=event_store)
    # M9.19 — and for integrity: a `billing.integrity.*` fact commits with the run or finding that
    # produced it, so a divergence is never announced by an event whose evidence rolled back.
    integrity_factory = IntegrityFactory(ids=ids, clock=clock, emitter=event_store)
    # M9.20 — and for correlation: a `billing.incident.*` fact commits with the incident or link
    # that produced it, so an incident is never announced by an event whose evidence rolled back.
    incident_factory = IncidentFactory(ids=ids, clock=clock, emitter=event_store)
    # M9.21 — and for investigation: a `billing.investigation.*` fact commits with the
    # investigation, evidence capture, conclusion or audit package that produced it. **No evidence
    # is fabricated here**: an unreadable source is captured as UNAVAILABLE rather than replaced
    # with a live object, so "could not retrieve" never commits as "verified".
    investigation_factory = InvestigationFactory(ids=ids, clock=clock, emitter=event_store)
    # M9.22 — and for reporting: a `billing.report.*` fact commits with the definition, filing,
    # correction or package that produced it. **No figure is recalculated here**: a report carries
    # the authoritative amounts the owning domains wrote, and an unreadable source commits as a
    # named gap rather than as zero.
    reporting_factory = ReportingFactory(ids=ids, clock=clock, emitter=event_store)
    context = BillingApplicationFactory.context(
        repositories=build_repositories(database),
        factory=factory,
        services=BillingServices(),
    )
    app_emitter = emitter if emitter is not None else RecordingApplicationEventEmitter()
    application = BillingApplicationFactory.create(
        context=context,
        ids=ids,
        clock=clock,
        authorizer=authorizer,  # None ⇒ the Application's deny-all default stands
        emitter=app_emitter,
        uow_factory=BillingUnitOfWorkFactory(database, app_emitter),
        catalog_factory=catalog_factory,
        pricing_factory=pricing_factory,
        account_factory=account_factory,
        subscription_factory=subscription_factory,
        entitlement_factory=entitlement_factory,
        metering_factory=metering_factory,
        metric_stream=metric_stream,
        rating_factory=rating_factory,
        ledger_factory=ledger_factory,
        invoice_factory=invoice_factory,
        tax_factory=tax_factory,
        payment_factory=payment_factory,
        payment_providers=payment_providers,
        collection_factory=collection_factory,
        collection_notifiers=collection_notifiers,
        revenue_factory=revenue_factory,
        integrity_factory=integrity_factory,
        incident_factory=incident_factory,
        investigation_factory=investigation_factory,
        reporting_factory=reporting_factory,
    )
    return PersistentBillingApplication(
        application=application, database=database, events=event_store
    )


def migrate(database: BillingDatabase) -> tuple[str, ...]:
    """Apply the Billing migration chain (0013–0028) to an existing database. Idempotent."""
    return upgrade_all(database)
