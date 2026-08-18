"""`BillingApplication` facade + factory (M9.3) — the Application composition root.

The one place the buses, use cases, Unit of Work factory, authorization orchestrator, and
application-event emitter are wired over a `BillingContext`. A client dispatches through
`app.commands` / `app.queries` and needs no other setup.

**No `.default()` exists, deliberately.** The Foundation ships repository *interfaces*
only (M9.2) and persistence does not exist until M9.4, so there is nothing honest to
default to; a caller must inject repositories. Defaulting to a hidden in-memory store would
be a persistence implementation smuggled into the Application layer.

The production wiring swaps the seams (real clock/ids), the Unit of Work factory (a
DB-backed one), the authorizer (the Control Plane's §A11.2 RBAC evaluator), and the
repositories — without touching a use case, a handler, or the Foundation.
"""

from __future__ import annotations

from dataclasses import dataclass

from billing.foundation import BillingFactory, Clock, IdGenerator

from .authorization import AuthorizationOrchestrator, BillingAuthorizer, DenyAllAuthorizer
from .billing_accounts import (
    AccountCommandUseCases,
    AccountQueryUseCases,
    register_account_command_handlers,
    register_account_query_handlers,
)
from .catalog import (
    CatalogCommandUseCases,
    CatalogQueryUseCases,
    register_catalog_command_handlers,
    register_catalog_query_handlers,
)
from .collections_engine import (
    CollectionQueries,
    CollectionServices,
    CollectionUseCases,
    register_collection_use_cases,
)
from .consolidation_engine import (
    ConsolidationQueries,
    ConsolidationServices,
    ConsolidationUseCases,
    register_consolidation_use_cases,
)
from .context import BillingContext, BillingRepositories, BillingServices
from .dispatcher import CommandBus, QueryBus
from .entitlements_engine import (
    EntitlementCommandUseCases,
    EntitlementQueryUseCases,
    register_entitlement_command_handlers,
    register_entitlement_query_handlers,
)
from .events import ApplicationEventEmitter, RecordingApplicationEventEmitter
from .handlers import register_command_handlers, register_query_handlers
from .incident_engine import (
    IncidentQueries,
    IncidentServices,
    IncidentUseCases,
    register_incident_use_cases,
)
from .investigation_engine import (
    InvestigationQueries,
    InvestigationServices,
    InvestigationUseCases,
    register_investigation_use_cases,
)
from .invoice_engine import (
    InvoiceCommandUseCases,
    InvoiceQueryUseCases,
    register_invoice_command_handlers,
    register_invoice_query_handlers,
)
from .ledger_engine import (
    LedgerCommandUseCases,
    LedgerQueryUseCases,
    register_ledger_command_handlers,
    register_ledger_query_handlers,
)
from .metering_engine import (
    MeteringCommandUseCases,
    MeteringQueryUseCases,
    register_metering_command_handlers,
    register_metering_query_handlers,
)
from .payment_engine import (
    PaymentQueries,
    PaymentServices,
    PaymentUseCases,
    StartPaymentAttempt,
    register_payment_use_cases,
)
from .pricing import (
    PricingCommandUseCases,
    PricingQueryUseCases,
    register_pricing_command_handlers,
    register_pricing_query_handlers,
)
from .rating_engine import (
    RatingCommandUseCases,
    RatingQueryUseCases,
    _RatingInputs,
    register_rating_command_handlers,
    register_rating_query_handlers,
)
from .reconciliation_engine import (
    ReconciliationQueries,
    ReconciliationServices,
    ReconciliationUseCases,
    register_reconciliation_use_cases,
)
from .reporting_engine import (
    ReportingQueries,
    ReportingServices,
    ReportingUseCases,
    register_reporting_use_cases,
)
from .revenue_engine import (
    RevenueQueries,
    RevenueServices,
    RevenueUseCases,
    register_revenue_use_cases,
)
from .subscriptions_engine import (
    SubscriptionCommandUseCases,
    SubscriptionQueryUseCases,
    register_subscription_command_handlers,
    register_subscription_query_handlers,
)
from .tax_engine import (
    TaxCommandUseCases,
    TaxQueryUseCases,
    register_tax_command_handlers,
    register_tax_query_handlers,
)
from .unit_of_work import StagingUnitOfWorkFactory, UnitOfWorkFactory
from .use_cases import CommandUseCases, QueryUseCases


@dataclass(frozen=True, slots=True)
class BillingApplication:
    """The assembled Application layer: the CQRS surface a future Control Plane dispatches
    through, plus the collaborators it was wired with."""

    commands: CommandBus
    queries: QueryBus
    command_use_cases: CommandUseCases
    query_use_cases: QueryUseCases
    authorization: AuthorizationOrchestrator
    uow_factory: UnitOfWorkFactory
    emitter: ApplicationEventEmitter
    context: BillingContext
    # M9.6 (additive, optional): present when catalog repositories were injected.
    catalog_command_use_cases: CatalogCommandUseCases | None = None
    catalog_query_use_cases: CatalogQueryUseCases | None = None
    # M9.7 (additive, optional): present when pricing repositories were injected.
    pricing_command_use_cases: PricingCommandUseCases | None = None
    pricing_query_use_cases: PricingQueryUseCases | None = None
    # M9.8 (additive, optional): present when account repositories were injected.
    account_command_use_cases: AccountCommandUseCases | None = None
    account_query_use_cases: AccountQueryUseCases | None = None
    # M9.9 (additive, optional): present when subscription repositories were injected.
    subscription_command_use_cases: SubscriptionCommandUseCases | None = None
    subscription_query_use_cases: SubscriptionQueryUseCases | None = None
    # M9.10 (additive, optional): present when entitlement repositories were injected.
    entitlement_command_use_cases: EntitlementCommandUseCases | None = None
    entitlement_query_use_cases: EntitlementQueryUseCases | None = None
    # M9.11 (additive, optional): present when metering repositories were injected.
    metering_command_use_cases: MeteringCommandUseCases | None = None
    metering_query_use_cases: MeteringQueryUseCases | None = None
    # M9.12 (additive, optional): present when rating repositories were injected.
    rating_command_use_cases: RatingCommandUseCases | None = None
    rating_query_use_cases: RatingQueryUseCases | None = None
    # M9.13 (additive, optional): present when ledger repositories were injected.
    ledger_command_use_cases: LedgerCommandUseCases | None = None
    ledger_query_use_cases: LedgerQueryUseCases | None = None
    # M9.14 (additive, optional): present when invoice repositories were injected.
    invoice_command_use_cases: InvoiceCommandUseCases | None = None
    invoice_query_use_cases: InvoiceQueryUseCases | None = None
    # M9.15 (additive, optional): present when tax repositories were injected.
    tax_command_use_cases: TaxCommandUseCases | None = None
    tax_query_use_cases: TaxQueryUseCases | None = None
    # M9.16 (additive, defaulted): the Payment Engine's use cases, when wired.
    payment_command_use_cases: PaymentUseCases | None = None
    payment_query_use_cases: PaymentQueries | None = None
    collection_command_use_cases: CollectionUseCases | None = None
    collection_query_use_cases: CollectionQueries | None = None
    revenue_command_use_cases: RevenueUseCases | None = None
    revenue_query_use_cases: RevenueQueries | None = None
    reconciliation_command_use_cases: ReconciliationUseCases | None = None
    reconciliation_query_use_cases: ReconciliationQueries | None = None
    incident_command_use_cases: IncidentUseCases | None = None
    incident_query_use_cases: IncidentQueries | None = None
    # M9.21 (additive): the Financial Investigation & Audit Evidence Engine, when wired.
    investigation_command_use_cases: InvestigationUseCases | None = None
    investigation_query_use_cases: InvestigationQueries | None = None
    # M9.22 (additive): the Compliance & Regulatory Reporting Engine, when wired.
    reporting_command_use_cases: ReportingUseCases | None = None
    reporting_query_use_cases: ReportingQueries | None = None
    consolidation_command_use_cases: ConsolidationUseCases | None = None
    consolidation_query_use_cases: ConsolidationQueries | None = None


class BillingApplicationFactory:
    """Wires an Application over an injected context. Construction only — no behaviour."""

    @staticmethod
    def create(
        *,
        context: BillingContext,
        ids: IdGenerator,
        clock: Clock,
        authorizer: BillingAuthorizer | None = None,
        emitter: ApplicationEventEmitter | None = None,
        uow_factory: UnitOfWorkFactory | None = None,
        catalog_factory=None,  # M9.6 (additive): inject a seam-bound CatalogFactory
        pricing_factory=None,  # M9.7 (additive): inject a seam-bound PricingFactory
        account_factory=None,  # M9.8 (additive): inject a seam-bound AccountFactory
        subscription_factory=None,  # M9.9 (additive): inject a seam-bound SubscriptionFactory
        entitlement_factory=None,  # M9.10 (additive): inject a seam-bound EntitlementFactory
        metering_factory=None,  # M9.11 (additive): inject a seam-bound MeteringFactory
        metric_stream=None,  # M9.11 (additive): the platform's `metric.*` port
        rating_factory=None,  # M9.12 (additive): inject a seam-bound RatingFactory
        ledger_factory=None,  # M9.13 (additive): inject a seam-bound LedgerFactory
        invoice_factory=None,  # M9.14 (additive): inject a seam-bound InvoiceFactory
        tax_factory=None,  # M9.15 (additive): inject a seam-bound TaxFactory
        payment_factory=None,  # M9.16 (additive): inject a seam-bound PaymentFactory
        payment_providers=None,  # M9.16: a ProviderRegistry; None ⇒ collect nothing
        collection_factory=None,  # M9.17 (additive): a seam-bound CollectionFactory
        collection_notifiers=None,  # M9.17: a NotifierRegistry; None ⇒ communicate nothing
        revenue_factory=None,  # M9.18 (additive): a seam-bound RevenueFactory
        integrity_factory=None,  # M9.19 (additive): a seam-bound IntegrityFactory
        incident_factory=None,  # M9.20 (additive): a seam-bound IncidentFactory
        investigation_factory=None,  # M9.21 (additive): a seam-bound InvestigationFactory
        reporting_factory=None,  # M9.22 (additive): a seam-bound ReportingFactory
        consolidation_factory=None,  # M9.23 (additive): a seam-bound ConsolidationFactory
    ) -> BillingApplication:
        emitter = emitter if emitter is not None else RecordingApplicationEventEmitter()
        uow_factory = uow_factory if uow_factory is not None else StagingUnitOfWorkFactory(emitter)
        # Deny-by-default: a forgotten authorization wiring must fail closed, never open.
        authz = AuthorizationOrchestrator(
            authorizer if authorizer is not None else DenyAllAuthorizer()
        )
        command_uc = CommandUseCases(
            context=context, authz=authz, uow_factory=uow_factory, ids=ids, clock=clock
        )
        query_uc = QueryUseCases(context=context, authz=authz)
        command_bus = CommandBus(emitter=emitter, ids=ids, clock=clock)
        query_bus = QueryBus()
        register_command_handlers(command_bus, command_uc)
        register_query_handlers(query_bus, query_uc)

        # M9.6 — the Commercial Catalog registers onto the *same* buses when its
        # repositories are wired. Absent them, the Application is exactly what M9.3 built:
        # the catalog is additive, never a precondition.
        catalog_command_uc = catalog_query_uc = None
        if context.catalog is not None:
            from billing.foundation import (
                BundleResolver,
                CatalogFactory,
                CatalogLifecycleService,
                CatalogSearchService,
                CatalogVisibilityService,
            )

            catalog_factory = catalog_factory or CatalogFactory(ids=ids, clock=clock)
            resolver = BundleResolver()
            catalog_command_uc = CatalogCommandUseCases(
                context=context,
                catalog_repos=context.catalog,
                factory=catalog_factory,
                lifecycle=CatalogLifecycleService(),
                resolver=resolver,
                authz=authz,
                uow_factory=uow_factory,
                ids=ids,
                clock=clock,
            )
            catalog_query_uc = CatalogQueryUseCases(
                context=context,
                catalog_repos=context.catalog,
                search=CatalogSearchService(),
                resolver=resolver,
                visibility=CatalogVisibilityService(),
                authz=authz,
            )
            register_catalog_command_handlers(command_bus, catalog_command_uc)
            register_catalog_query_handlers(query_bus, catalog_query_uc)

        # M9.7 — the Pricing Engine registers onto the *same* buses when its repositories are
        # wired. Absent them, pricing is simply not there: additive, never a precondition.
        pricing_command_uc = pricing_query_uc = None
        if context.pricing is not None:
            from billing.foundation import (
                PriceResolver,
                PricingFactory,
                PricingLifecycleService,
                PricingSearchService,
            )

            pricing_factory = pricing_factory or PricingFactory(ids=ids, clock=clock)
            price_resolver = PriceResolver()
            pricing_command_uc = PricingCommandUseCases(
                context=context,
                pricing_repos=context.pricing,
                factory=pricing_factory,
                lifecycle=PricingLifecycleService(),
                resolver=price_resolver,
                authz=authz,
                uow_factory=uow_factory,
                ids=ids,
                clock=clock,
            )
            pricing_query_uc = PricingQueryUseCases(
                context=context,
                pricing_repos=context.pricing,
                search=PricingSearchService(),
                resolver=price_resolver,
                authz=authz,
            )
            register_pricing_command_handlers(command_bus, pricing_command_uc)
            register_pricing_query_handlers(query_bus, pricing_query_uc)

        # M9.8 — Billing Accounts register onto the *same* buses when their repositories are
        # wired. Absent them, the domain is simply not there: additive, never a precondition.
        account_command_uc = account_query_uc = None
        if context.commercial_accounts is not None:
            from billing.foundation import (
                AccountFactory,
                AccountHierarchyResolver,
                AccountLifecycleService,
                AccountSearchService,
                CommercialProfileService,
            )

            hierarchy = AccountHierarchyResolver()
            account_factory = account_factory or AccountFactory(ids=ids, clock=clock)
            account_command_uc = AccountCommandUseCases(
                context=context,
                account_repos=context.commercial_accounts,
                factory=account_factory,
                lifecycle=AccountLifecycleService(),
                hierarchy=hierarchy,
                authz=authz,
                uow_factory=uow_factory,
                ids=ids,
                clock=clock,
            )
            account_query_uc = AccountQueryUseCases(
                context=context,
                account_repos=context.commercial_accounts,
                search=AccountSearchService(),
                hierarchy=hierarchy,
                profiles=CommercialProfileService(hierarchy),
                authz=authz,
            )
            register_account_command_handlers(command_bus, account_command_uc)
            register_account_query_handlers(query_bus, account_query_uc)

        # M9.9 — the Subscription Engine registers onto the *same* buses when its repositories
        # are wired. Absent them, the domain is simply not there: additive, never a precondition.
        subscription_command_uc = subscription_query_uc = None
        if context.subscriptions_engine is not None:
            from billing.foundation import (
                AgreementLifecycleService,
                BillingCycleService,
                ScheduleService,
                SubscriptionAssembler,
                SubscriptionFactory,
                SubscriptionSearchService,
                TrialService,
            )

            schedules = ScheduleService()
            assembler = SubscriptionAssembler()
            subscription_factory = subscription_factory or SubscriptionFactory(ids=ids, clock=clock)
            subscription_command_uc = SubscriptionCommandUseCases(
                context=context,
                subscription_repos=context.subscriptions_engine,
                factory=subscription_factory,
                lifecycle=AgreementLifecycleService(),
                cycles=BillingCycleService(),
                trials=TrialService(),
                schedules=schedules,
                assembler=assembler,
                authz=authz,
                uow_factory=uow_factory,
                ids=ids,
                clock=clock,
            )
            subscription_query_uc = SubscriptionQueryUseCases(
                context=context,
                subscription_repos=context.subscriptions_engine,
                search=SubscriptionSearchService(),
                schedules=schedules,
                assembler=assembler,
                authz=authz,
            )
            register_subscription_command_handlers(command_bus, subscription_command_uc)
            register_subscription_query_handlers(query_bus, subscription_query_uc)

        # M9.10 — the Entitlement Engine registers onto the *same* buses when its repositories
        # are wired. Absent them, the domain is simply not there: additive, never a precondition.
        entitlement_command_uc = entitlement_query_uc = None
        if context.entitlements_engine is not None:
            from billing.foundation import (
                EntitlementAssembler,
                EntitlementBundleExpander,
                EntitlementFactory,
                EntitlementInspector,
                EntitlementPrecedenceResolver,
                EntitlementScheduleService,
                EntitlementSearchService,
                EntitlementStalenessService,
                GrantLifecycleService,
            )

            resolver = EntitlementPrecedenceResolver()
            expander = EntitlementBundleExpander()
            ent_assembler = EntitlementAssembler(resolver)
            ent_schedules = EntitlementScheduleService()
            ent_lifecycle = GrantLifecycleService()
            entitlement_factory = entitlement_factory or EntitlementFactory(
                ids=ids, clock=clock, assembler=ent_assembler, expander=expander
            )
            entitlement_command_uc = EntitlementCommandUseCases(
                context=context,
                entitlement_repos=context.entitlements_engine,
                factory=entitlement_factory,
                lifecycle=ent_lifecycle,
                expander=expander,
                assembler=ent_assembler,
                schedules=ent_schedules,
                authz=authz,
                uow_factory=uow_factory,
                ids=ids,
                clock=clock,
            )
            entitlement_query_uc = EntitlementQueryUseCases(
                context=context,
                entitlement_repos=context.entitlements_engine,
                factory=entitlement_factory,
                lifecycle=ent_lifecycle,
                expander=expander,
                assembler=ent_assembler,
                resolver=resolver,
                inspector=EntitlementInspector(resolver),
                staleness=EntitlementStalenessService(),
                schedules=ent_schedules,
                search=EntitlementSearchService(),
                authz=authz,
            )
            register_entitlement_command_handlers(command_bus, entitlement_command_uc)
            register_entitlement_query_handlers(query_bus, entitlement_query_uc)

        # M9.11 — the Metering Engine registers onto the *same* buses when its repositories are
        # wired. Absent them, the domain is simply not there: additive, never a precondition.
        # The metric stream defaults to `NullMetricStream`, so a build that forgets to wire the
        # platform's port **ingests nothing** rather than inventing usage.
        metering_command_uc = metering_query_uc = None
        if context.metering_engine is not None:
            from billing.foundation import (
                AttributionService,
                LateArrivalPolicy,
                MeteringFactory,
                MeteringValidator,
                MeterLifecycleService,
                NullMetricStream,
                QuarantineLifecycleService,
                UsageAggregator,
                UsageCorrectionService,
                UsageExportService,
                UsageIngestionService,
                UsageInspector,
                UsageNormalizer,
                UsagePeriodLifecycleService,
                UsageReconciliationService,
                UsageReplayService,
                UsageSealingService,
                UsageSearchService,
            )

            normalizer = UsageNormalizer()
            late_policy = LateArrivalPolicy()
            ingestion = UsageIngestionService(normalizer, late_policy, AttributionService())
            aggregator = UsageAggregator()
            sealing = UsageSealingService(aggregator)
            stream = metric_stream if metric_stream is not None else NullMetricStream()
            metering_factory = metering_factory or MeteringFactory(ids=ids, clock=clock)
            metering_command_uc = MeteringCommandUseCases(
                context=context,
                metering_repos=context.metering_engine,
                factory=metering_factory,
                meter_lifecycle=MeterLifecycleService(),
                period_lifecycle=UsagePeriodLifecycleService(),
                quarantine_lifecycle=QuarantineLifecycleService(),
                ingestion=ingestion,
                late_policy=late_policy,
                aggregator=aggregator,
                sealing=sealing,
                replay=UsageReplayService(ingestion),
                reconciliation=UsageReconciliationService(),
                corrections=UsageCorrectionService(),
                stream=stream,
                authz=authz,
                uow_factory=uow_factory,
                ids=ids,
                clock=clock,
            )
            metering_query_uc = MeteringQueryUseCases(
                context=context,
                metering_repos=context.metering_engine,
                factory=metering_factory,
                meter_lifecycle=MeterLifecycleService(),
                period_lifecycle=UsagePeriodLifecycleService(),
                quarantine_lifecycle=QuarantineLifecycleService(),
                aggregator=aggregator,
                inspector=UsageInspector(MeteringValidator()),
                search=UsageSearchService(),
                exporter=UsageExportService(),
                reconciliation=UsageReconciliationService(),
                stream=stream,
                ingestion=ingestion,
                authz=authz,
            )
            register_metering_command_handlers(command_bus, metering_command_uc)
            register_metering_query_handlers(query_bus, metering_query_uc)

        # M9.12 — the Rating Engine registers onto the *same* buses when its repositories are
        # wired. Absent them, the domain is simply not there: additive, never a precondition.
        # It reads the pricing, subscription, entitlement, and metering seams through the same
        # context, and writes only to its own — so a build with rating unwired rates nothing
        # rather than half-rating something.
        rating_command_uc = rating_query_uc = None
        if context.rating_engine is not None:
            from billing.foundation import (
                ChargeRequestBuilder,
                DiscountEngine,
                PriceResolver,
                RatingAdjustmentService,
                RatingBasisBuilder,
                RatingComparisonService,
                RatingEngine,
                RatingExplainer,
                RatingFactory,
                RatingHistoryService,
                RatingInspector,
                RatingLifecycleService,
                RatingSearchService,
                RatingValidator,
                RerateService,
                UsageAggregator,
                UsageSealingService,
            )

            rating_aggregator = UsageAggregator()
            rating_inputs = _RatingInputs(
                context=context,
                resolver=PriceResolver(discounts=DiscountEngine()),
                aggregator=rating_aggregator,
                sealing=UsageSealingService(rating_aggregator),
                requests=ChargeRequestBuilder(),
                basis_builder=RatingBasisBuilder(),
                commitments=context.rating_engine.commitments,
            )
            rating_engine_service = RatingEngine()
            rating_lifecycle = RatingLifecycleService()
            rating_rerating = RerateService()
            rating_comparison = RatingComparisonService()
            rating_validator = RatingValidator()
            rating_factory = rating_factory or RatingFactory(ids=ids, clock=clock)
            rating_command_uc = RatingCommandUseCases(
                context=context,
                rating_repos=context.rating_engine,
                factory=rating_factory,
                engine=rating_engine_service,
                lifecycle=rating_lifecycle,
                rerating=rating_rerating,
                comparison=rating_comparison,
                adjustments=RatingAdjustmentService(),
                inputs=rating_inputs,
                validator=rating_validator,
                authz=authz,
                uow_factory=uow_factory,
                ids=ids,
                clock=clock,
            )
            rating_query_uc = RatingQueryUseCases(
                context=context,
                rating_repos=context.rating_engine,
                engine=rating_engine_service,
                lifecycle=rating_lifecycle,
                rerating=rating_rerating,
                comparison=rating_comparison,
                explainer=RatingExplainer(),
                inspector=RatingInspector(),
                search=RatingSearchService(),
                history=RatingHistoryService(),
                validator=rating_validator,
                inputs=rating_inputs,
                authz=authz,
                clock=clock,
            )
            register_rating_command_handlers(command_bus, rating_command_uc)
            register_rating_query_handlers(query_bus, rating_query_uc)

        # M9.13 — the Credit & Revenue Ledger registers onto the *same* buses when its
        # repositories are wired. Absent them the domain is simply not there: additive, never a
        # precondition. It reads the rating seams through the same context and writes only to its
        # own six, so a build with the ledger unwired records nothing rather than half-recording.
        ledger_command_uc = ledger_query_uc = None
        if context.ledger_engine is not None:
            from billing.foundation import (
                AccountingPeriodService,
                AdjustmentService,
                BalanceService,
                ChargePostingService,
                CreditApplicationService,
                CreditLifecycleService,
                LedgerFactory,
                LedgerInspector,
                LedgerReconciliationService,
                LedgerReplayService,
                LedgerSearchService,
                LedgerValidator,
                PostingService,
                ReversalService,
                TrialBalanceService,
            )

            ledger_periods = AccountingPeriodService()
            ledger_balances = BalanceService()
            ledger_credits = CreditApplicationService()
            ledger_credit_lifecycle = CreditLifecycleService()
            ledger_validator = LedgerValidator()
            ledger_factory = ledger_factory or LedgerFactory(ids=ids, clock=clock)
            ledger_command_uc = LedgerCommandUseCases(
                context=context,
                ledger_repos=context.ledger_engine,
                factory=ledger_factory,
                posting=PostingService(),
                periods=ledger_periods,
                balances=ledger_balances,
                reversals=ReversalService(),
                adjustments=AdjustmentService(),
                credits=ledger_credits,
                credit_lifecycle=ledger_credit_lifecycle,
                charge_postings=ChargePostingService(),
                validator=ledger_validator,
                authz=authz,
                uow_factory=uow_factory,
                ids=ids,
                clock=clock,
            )
            ledger_query_uc = LedgerQueryUseCases(
                context=context,
                ledger_repos=context.ledger_engine,
                balances=ledger_balances,
                periods=ledger_periods,
                credits=ledger_credits,
                credit_lifecycle=ledger_credit_lifecycle,
                trial_balances=TrialBalanceService(),
                replay=LedgerReplayService(ledger_periods),
                reconciliation=LedgerReconciliationService(),
                inspector=LedgerInspector(),
                search=LedgerSearchService(),
                validator=ledger_validator,
                authz=authz,
                clock=clock,
            )
            register_ledger_command_handlers(command_bus, ledger_command_uc)
            register_ledger_query_handlers(query_bus, ledger_query_uc)

        # M9.14 — the Invoice Engine registers onto the *same* buses when its repositories are
        # wired. Absent them the domain is simply not there: additive, never a precondition. It
        # reads the ledger, account, and tax seams through the same context and writes only to its
        # own six, so a build with invoicing unwired documents nothing rather than half-documenting.
        invoice_command_uc = invoice_query_uc = None
        if context.invoice_engine is not None:
            from billing.foundation import (
                CorrectionService,
                DocumentAssembler,
                DocumentLifecycleService,
                InvoiceFactory,
                InvoiceInspector,
                InvoiceReconciliationService,
                InvoiceSearchService,
                InvoiceValidator,
                LineBuilder,
                NumberingService,
                NumberSeriesAuditor,
                SnapshotIntegrityService,
            )

            invoice_lifecycle = DocumentLifecycleService()
            invoice_lines = LineBuilder()
            invoice_assembler = DocumentAssembler()
            invoice_numbering = NumberingService()
            invoice_integrity = SnapshotIntegrityService()
            invoice_validator = InvoiceValidator()
            invoice_factory = invoice_factory or InvoiceFactory(ids=ids, clock=clock)
            invoice_command_uc = InvoiceCommandUseCases(
                context=context,
                invoice_repos=context.invoice_engine,
                factory=invoice_factory,
                lifecycle=invoice_lifecycle,
                lines=invoice_lines,
                assembler=invoice_assembler,
                numbering=invoice_numbering,
                corrections=CorrectionService(),
                integrity=invoice_integrity,
                validator=invoice_validator,
                authz=authz,
                uow_factory=uow_factory,
                ids=ids,
                clock=clock,
            )
            invoice_query_uc = InvoiceQueryUseCases(
                context=context,
                invoice_repos=context.invoice_engine,
                factory=invoice_factory,
                lifecycle=invoice_lifecycle,
                lines=invoice_lines,
                assembler=invoice_assembler,
                numbering=invoice_numbering,
                integrity=invoice_integrity,
                inspector=InvoiceInspector(invoice_integrity),
                reconciliation=InvoiceReconciliationService(),
                auditor=NumberSeriesAuditor(),
                search=InvoiceSearchService(),
                validator=invoice_validator,
                authz=authz,
                clock=clock,
            )
            register_invoice_command_handlers(command_bus, invoice_command_uc)
            register_invoice_query_handlers(query_bus, invoice_query_uc)

        # M9.15 — the Tax Determination Engine registers onto the *same* buses when its repositories
        # are wired. Absent them the domain is simply not there: additive, never a precondition. It
        # reads the document, account, and tax-profile seams through the same context and writes
        # only to its own eight — a build with tax unwired determines nothing rather than half.
        tax_command_uc = tax_query_uc = None
        if context.tax_engine is not None:
            from billing.foundation import (
                AssessmentLifecycleService,
                TaxDeterminationService,
                TaxExplainer,
                TaxFactory,
                TaxInspector,
                TaxReconciliationService,
                TaxRoundingService,
                TaxSearchService,
                TaxValidator,
            )

            tax_determination = TaxDeterminationService()
            tax_lifecycle = AssessmentLifecycleService()
            tax_rounding = TaxRoundingService()
            tax_validator = TaxValidator()
            tax_factory = tax_factory or TaxFactory(ids=ids, clock=clock)
            tax_command_uc = TaxCommandUseCases(
                context=context,
                tax_repos=context.tax_engine,
                factory=tax_factory,
                determination=tax_determination,
                lifecycle=tax_lifecycle,
                rounding=tax_rounding,
                validator=tax_validator,
                authz=authz,
                uow_factory=uow_factory,
                ids=ids,
                clock=clock,
            )
            tax_query_uc = TaxQueryUseCases(
                context=context,
                tax_repos=context.tax_engine,
                determination=tax_determination,
                lifecycle=tax_lifecycle,
                rounding=tax_rounding,
                explainer=TaxExplainer(),
                inspector=TaxInspector(),
                reconciliation=TaxReconciliationService(),
                search=TaxSearchService(),
                validator=tax_validator,
                authz=authz,
                clock=clock,
            )
            register_tax_command_handlers(command_bus, tax_command_uc)
            register_tax_query_handlers(query_bus, tax_query_uc)

        # M9.16 — the Payment Engine registers onto the *same* buses when its repositories are
        # wired. It is the first domain that also needs a **provider registry** and a **ledger
        # posting seam**, and both default to nothing: with no provider registered it can collect
        # nothing at all, and with no ledger seam it records evidence and posts no financial fact.
        # Unwired means absent, never half — the same rule every engine since M9.6 has followed.
        payment_command_uc = payment_query_uc = None
        if context.payment_engine is not None:
            from billing.foundation import (
                AttemptGuardService,
                AttemptLifecycleService,
                IntentLifecycleService,
                LedgerPostingPlanner,
                OperationService,
                OutcomeNormalizer,
                PaymentExplainer,
                PaymentFactory,
                PaymentInspector,
                PaymentPositionService,
                PaymentReconciliationService,
                PaymentRouter,
                PaymentSearchService,
                PaymentValidator,
                ProviderRegistry,
                RefundService,
            )

            payment_services = PaymentServices(
                intent_lifecycle=IntentLifecycleService(),
                attempt_lifecycle=AttemptLifecycleService(),
                guards=AttemptGuardService(),
                router=PaymentRouter(),
                operation_rules=OperationService(),
                normalizer=OutcomeNormalizer(),
                positions=PaymentPositionService(),
                refund_rules=RefundService(),
                reconciliation=PaymentReconciliationService(),
                planner=LedgerPostingPlanner(),
                explainer=PaymentExplainer(),
                inspector=PaymentInspector(),
                validator=PaymentValidator(),
                search=PaymentSearchService(),
                registry=payment_providers
                if payment_providers is not None
                else ProviderRegistry(),
            )
            payment_factory = payment_factory or PaymentFactory(ids=ids, clock=clock)
            payment_command_uc = PaymentUseCases(
                context=context,
                repos=context.payment_engine,
                factory=payment_factory,
                services=payment_services,
                authz=authz,
                uow_factory=uow_factory,
                ids=ids,
                clock=clock,
                # The Ledger boundary: M9.13's own command, dispatched into M9.13's own use cases.
                # Payments writes nothing into ledger persistence, and when the ledger is unwired a
                # capture is still recorded as evidence — it simply produces no financial fact.
                ledger_post=(
                    ledger_command_uc.post_entry if ledger_command_uc is not None else None
                ),
            )
            payment_query_uc = PaymentQueries(
                context=context,
                repos=context.payment_engine,
                services=payment_services,
                authz=authz,
                clock=clock,
            )
            register_payment_use_cases(
                command_bus, query_bus, payment_command_uc, payment_query_uc
            )

        # M9.17 — Dunning & Collections registers onto the *same* buses when its repositories are
        # wired. It is the first domain whose only outward write is a **command into another
        # engine**: `payment_retry` is M9.16's own `start_attempt`, injected, so Collections can
        # ask for money without owning a provider, an adapter, or a payment row. With no payment
        # engine wired it still plans retries and records that it could not send them, which is
        # the honest behaviour rather than a silent no-op. Unwired means absent, never half.
        collection_command_uc = collection_query_uc = None
        if context.collections_engine is not None:
            from billing.foundation import (
                ActionLifecycleService,
                CaseLifecycleService,
                CollectionDecisionEngine,
                CollectionExplainer,
                CollectionFactory,
                CollectionGuardService,
                CollectionInspector,
                CollectionPolicyService,
                CollectionScheduleService,
                CollectionSearchService,
                CollectionValidator,
                NotifierRegistry,
                SignalReader,
            )

            collection_services = CollectionServices(
                case_lifecycle=CaseLifecycleService(),
                action_lifecycle=ActionLifecycleService(),
                guards=CollectionGuardService(),
                decisions=CollectionDecisionEngine(),
                policy_rules=CollectionPolicyService(),
                signals=SignalReader(),
                schedule=CollectionScheduleService(),
                explainer=CollectionExplainer(),
                inspector=CollectionInspector(),
                validator=CollectionValidator(),
                search=CollectionSearchService(),
                notifiers=collection_notifiers
                if collection_notifiers is not None
                else NotifierRegistry(),
            )
            collection_factory = collection_factory or CollectionFactory(ids=ids, clock=clock)
            collection_command_uc = CollectionUseCases(
                context=context,
                repos=context.collections_engine,
                factory=collection_factory,
                services=collection_services,
                authz=authz,
                uow_factory=uow_factory,
                ids=ids,
                clock=clock,
                # **The payment boundary.** M9.16's own command, dispatched into M9.16's own use
                # cases. Collections writes nothing into payment persistence and reaches no
                # provider; when Payments is unwired a retry is recorded as undeliverable.
                payment_retry=(
                    payment_command_uc.start_attempt if payment_command_uc is not None else None
                ),
                payment_retry_command=(
                    StartPaymentAttempt if payment_command_uc is not None else None
                ),
            )
            collection_query_uc = CollectionQueries(
                context=context,
                repos=context.collections_engine,
                services=collection_services,
                authz=authz,
                clock=clock,
            )
            register_collection_use_cases(
                command_bus, query_bus, collection_command_uc, collection_query_uc
            )

        # M9.18 — Revenue Recognition registers onto the *same* buses when its repositories are
        # wired. Its only outward write is a **command into another engine**: `ledger_post` is
        # M9.13's own `post_entry`, injected, so a matured recognition becomes a financial fact
        # without this domain gaining a poster, a balance, or a second set of books (ADR-0023 RS6).
        # With no ledger wired it still records the recognition *decision* and marks it unposted,
        # which is the honest behaviour rather than a silent no-op. Unwired means absent, never
        # half.
        revenue_command_uc = revenue_query_uc = None
        if context.revenue_engine is not None:
            from billing.foundation import (
                EntryLifecycleService,
                RecognitionExplainer,
                RecognitionGuardService,
                RecognitionInspector,
                RecognitionPositionService,
                RecognitionScheduler,
                RecognitionScheduleService,
                RecognitionValidator,
                RevenueFactory,
                RevenueSearchService,
                ScheduleLifecycleService,
            )

            revenue_services = RevenueServices(
                schedule_lifecycle=ScheduleLifecycleService(),
                entry_lifecycle=EntryLifecycleService(),
                guards=RecognitionGuardService(),
                scheduler=RecognitionScheduler(),
                positions=RecognitionPositionService(),
                schedules=RecognitionScheduleService(),
                explainer=RecognitionExplainer(),
                inspector=RecognitionInspector(),
                validator=RecognitionValidator(),
                search=RevenueSearchService(),
            )
            revenue_factory = revenue_factory or RevenueFactory(ids=ids, clock=clock)
            revenue_command_uc = RevenueUseCases(
                context=context,
                repos=context.revenue_engine,
                factory=revenue_factory,
                services=revenue_services,
                authz=authz,
                uow_factory=uow_factory,
                ids=ids,
                clock=clock,
                ledger_post=(
                    ledger_command_uc.post_entry if ledger_command_uc is not None else None
                ),
            )
            revenue_query_uc = RevenueQueries(
                context=context,
                repos=context.revenue_engine,
                services=revenue_services,
                authz=authz,
                clock=clock,
            )
            register_revenue_use_cases(
                command_bus, query_bus, revenue_command_uc, revenue_query_uc
            )

        # M9.19 — Reconciliation & Financial Integrity registers onto the *same* buses when its
        # repositories are wired. It **reads** the five financial domains through the context and
        # writes to none of them; its only outward write is `dispatch`, the shared command bus,
        # through which the two whitelisted repairs (`PostRecognitionToLedger`,
        # `ReconcilePaymentAttempt`) run inside their *owning* engines. `PostJournalEntry` is not
        # reachable from here — a reconciliation engine that could post a journal entry would
        # create the financial fact it claims to have observed.
        reconciliation_command_uc = reconciliation_query_uc = None
        if context.reconciliation_engine is not None:
            from billing.foundation import (
                DiscrepancyAdapter,
                FindingExplainer,
                FindingLifecycleService,
                IntegrityFactory,
                IntegrityPostureService,
                IntegritySearchService,
                MatchingService,
                ReconciliationGuardService,
                ReconciliationValidator,
                RunInspector,
            )

            reconciliation_services = ReconciliationServices(
                matcher=MatchingService(),
                lifecycle=FindingLifecycleService(),
                guards=ReconciliationGuardService(),
                posture=IntegrityPostureService(),
                inspector=RunInspector(),
                explainer=FindingExplainer(),
                search=IntegritySearchService(),
                validator=ReconciliationValidator(),
                adapter=DiscrepancyAdapter(),
            )
            integrity_factory = integrity_factory or IntegrityFactory(ids=ids, clock=clock)
            reconciliation_command_uc = ReconciliationUseCases(
                context=context,
                repos=context.reconciliation_engine,
                factory=integrity_factory,
                services=reconciliation_services,
                authz=authz,
                uow_factory=uow_factory,
                ids=ids,
                clock=clock,
                dispatch=command_bus.dispatch,
            )
            reconciliation_query_uc = ReconciliationQueries(
                context=context,
                repos=context.reconciliation_engine,
                services=reconciliation_services,
                authz=authz,
            )
            register_reconciliation_use_cases(
                command_bus, query_bus, reconciliation_command_uc, reconciliation_query_uc
            )

        # M9.20 — Financial Integrity Correlation registers onto the *same* buses when its
        # repositories are wired. It **reads** M9.19's findings and the owning domains' immutable
        # facts and writes to none of them; its only outward write is `dispatch`, the shared
        # command bus, through which M9.19's own `RequestFindingRepair` runs — which itself only
        # asks an owning domain. M9.20 is therefore two removes from any financial mutation, and
        # no domain repository is reachable from it.
        incident_command_uc = incident_query_uc = None
        if context.incident_engine is not None:
            from billing.foundation import (
                CorrelationService,
                EvidenceGraphService,
                IncidentExplainer,
                IncidentFactory,
                IncidentGuardService,
                IncidentInspector,
                IncidentLifecycleService,
                IncidentPostureService,
                IncidentSearchService,
                IncidentValidator,
                ResolutionGuardService,
                RootCauseService,
                SeverityService,
            )

            incident_services = IncidentServices(
                correlation=CorrelationService(),
                lifecycle=IncidentLifecycleService(),
                guards=IncidentGuardService(),
                resolution=ResolutionGuardService(),
                severity=SeverityService(),
                root_cause=RootCauseService(),
                graph=EvidenceGraphService(),
                explainer=IncidentExplainer(),
                inspector=IncidentInspector(),
                posture=IncidentPostureService(),
                search=IncidentSearchService(),
                validator=IncidentValidator(),
            )
            incident_factory = incident_factory or IncidentFactory(ids=ids, clock=clock)
            incident_command_uc = IncidentUseCases(
                context=context,
                repos=context.incident_engine,
                factory=incident_factory,
                services=incident_services,
                authz=authz,
                uow_factory=uow_factory,
                ids=ids,
                clock=clock,
                dispatch=command_bus.dispatch,
            )
            incident_query_uc = IncidentQueries(
                context=context,
                repos=context.incident_engine,
                services=incident_services,
                authz=authz,
            )
            register_incident_use_cases(
                command_bus, query_bus, incident_command_uc, incident_query_uc
            )

        # M9.21 (additive) — the Financial Investigation & Audit Evidence Engine, wired only when
        # its seams are present. It reads M9.20 incidents and M9.19 findings as immutable facts and
        # writes to none of them. **It has no outward command at all** — not even M9.19's repair
        # request, which it references rather than issues. No domain repository is reachable from
        # it, and `dispatch` is deliberately not passed: there is nothing here to dispatch.
        investigation_command_uc = investigation_query_uc = None
        if context.investigation_engine is not None:
            from billing.foundation import (
                CanonicalizationService,
                ClosureService,
                CompletenessService,
                ConclusionGuardService,
                CustodyService,
                EvidenceComparisonService,
                EvidenceGuardService,
                InvestigationFactory,
                InvestigationGuardService,
                InvestigationLifecycleService,
                InvestigationValidator,
                ManifestBuilder,
                ObservationGuardService,
                PackageGuardService,
            )

            investigation_services = InvestigationServices(
                lifecycle=InvestigationLifecycleService(),
                guards=InvestigationGuardService(),
                evidence_guards=EvidenceGuardService(),
                observations=ObservationGuardService(),
                conclusions=ConclusionGuardService(),
                custody=CustodyService(),
                comparison=EvidenceComparisonService(),
                closure=ClosureService(),
                manifest=ManifestBuilder(),
                completeness=CompletenessService(),
                canonical=CanonicalizationService(),
                packages=PackageGuardService(),
                validator=InvestigationValidator(),
            )
            investigation_factory = investigation_factory or InvestigationFactory(
                ids=ids, clock=clock
            )
            investigation_command_uc = InvestigationUseCases(
                context=context,
                repos=context.investigation_engine,
                factory=investigation_factory,
                services=investigation_services,
                authz=authz,
                uow_factory=uow_factory,
                ids=ids,
                clock=clock,
            )
            investigation_query_uc = InvestigationQueries(
                context=context,
                repos=context.investigation_engine,
                services=investigation_services,
                authz=authz,
            )
            register_investigation_use_cases(
                command_bus, query_bus, investigation_command_uc, investigation_query_uc
            )

        # M9.22 (additive) — the Compliance & Regulatory Reporting Engine, wired only when its
        # seams are present. It reads every financial domain as immutable facts and writes to none
        # of them. **It has no outward command at all**, and `dispatch` is deliberately not
        # passed: a report is derived evidence and cannot ask a domain to change so the filing
        # comes out better.
        reporting_command_uc = reporting_query_uc = None
        if context.reporting_engine is not None:
            from billing.foundation import (
                ExceptionGuardService,
                ProvenanceService,
                ReportCompletenessService,
                ReportDiffService,
                ReportExceptionService,
                ReportGuardService,
                ReportingFactory,
                ReportingTransformationService,
                ReportingValidator,
                ReportLifecycleService,
                ReportValidationService,
            )

            reporting_services = ReportingServices(
                lifecycle=ReportLifecycleService(),
                guards=ReportGuardService(),
                exception_guards=ExceptionGuardService(),
                transformations=ReportingTransformationService(),
                completeness=ReportCompletenessService(),
                exceptions=ReportExceptionService(),
                validation=ReportValidationService(),
                provenance=ProvenanceService(),
                diff=ReportDiffService(),
                validator=ReportingValidator(),
            )
            reporting_factory = reporting_factory or ReportingFactory(ids=ids, clock=clock)
            reporting_command_uc = ReportingUseCases(
                context=context,
                repos=context.reporting_engine,
                factory=reporting_factory,
                services=reporting_services,
                authz=authz,
                uow_factory=uow_factory,
                ids=ids,
                clock=clock,
            )
            reporting_query_uc = ReportingQueries(
                context=context,
                repos=context.reporting_engine,
                services=reporting_services,
                authz=authz,
            )
            register_reporting_use_cases(
                command_bus, query_bus, reporting_command_uc, reporting_query_uc
            )

        # M9.23 (additive) — the Period Scope & Consolidation Engine, wired only when its seams
        # are present. It reads M9.8's hierarchy and every financial domain as immutable facts and
        # writes to none of them. **It has no outward command at all**, and `dispatch` is
        # deliberately not passed: a consolidation is a derived view and cannot ask a domain to
        # change so the total comes out rounder.
        consolidation_command_uc = consolidation_query_uc = None
        if context.consolidation_engine is not None:
            from billing.foundation import (
                ConsolidationAggregationService,
                ConsolidationCompletenessService,
                ConsolidationExceptionService,
                ConsolidationFactory,
                ConsolidationGuardService,
                ConsolidationLifecycleService,
                ConsolidationProvenanceService,
                ConsolidationValidationService,
                ScopeAttributionService,
                ScopePeriodService,
                ScopeResolutionService,
                ScopeValidator,
            )

            consolidation_services = ConsolidationServices(
                resolution=ScopeResolutionService(),
                periods=ScopePeriodService(),
                attribution=ScopeAttributionService(),
                aggregation=ConsolidationAggregationService(),
                completeness=ConsolidationCompletenessService(),
                exceptions=ConsolidationExceptionService(),
                validation=ConsolidationValidationService(),
                lifecycle=ConsolidationLifecycleService(),
                guards=ConsolidationGuardService(),
                provenance=ConsolidationProvenanceService(),
                validator=ScopeValidator(),
            )
            consolidation_factory = consolidation_factory or ConsolidationFactory(clock=clock)
            consolidation_command_uc = ConsolidationUseCases(
                context=context,
                repos=context.consolidation_engine,
                factory=consolidation_factory,
                services=consolidation_services,
                authz=authz,
                uow_factory=uow_factory,
                ids=ids,
                clock=clock,
            )
            consolidation_query_uc = ConsolidationQueries(
                context=context,
                repos=context.consolidation_engine,
                services=consolidation_services,
                authz=authz,
            )
            register_consolidation_use_cases(
                command_bus, query_bus, consolidation_command_uc, consolidation_query_uc
            )

        return BillingApplication(
            commands=command_bus,
            queries=query_bus,
            command_use_cases=command_uc,
            query_use_cases=query_uc,
            authorization=authz,
            uow_factory=uow_factory,
            emitter=emitter,
            context=context,
            catalog_command_use_cases=catalog_command_uc,
            catalog_query_use_cases=catalog_query_uc,
            pricing_command_use_cases=pricing_command_uc,
            pricing_query_use_cases=pricing_query_uc,
            account_command_use_cases=account_command_uc,
            account_query_use_cases=account_query_uc,
            subscription_command_use_cases=subscription_command_uc,
            subscription_query_use_cases=subscription_query_uc,
            entitlement_command_use_cases=entitlement_command_uc,
            entitlement_query_use_cases=entitlement_query_uc,
            metering_command_use_cases=metering_command_uc,
            metering_query_use_cases=metering_query_uc,
            rating_command_use_cases=rating_command_uc,
            rating_query_use_cases=rating_query_uc,
            ledger_command_use_cases=ledger_command_uc,
            ledger_query_use_cases=ledger_query_uc,
            invoice_command_use_cases=invoice_command_uc,
            invoice_query_use_cases=invoice_query_uc,
            payment_command_use_cases=payment_command_uc,
            payment_query_use_cases=payment_query_uc,
            collection_command_use_cases=collection_command_uc,
            collection_query_use_cases=collection_query_uc,
            revenue_command_use_cases=revenue_command_uc,
            revenue_query_use_cases=revenue_query_uc,
            reconciliation_command_use_cases=reconciliation_command_uc,
            reconciliation_query_use_cases=reconciliation_query_uc,
            incident_command_use_cases=incident_command_uc,
            incident_query_use_cases=incident_query_uc,
            investigation_command_use_cases=investigation_command_uc,
            investigation_query_use_cases=investigation_query_uc,
            reporting_command_use_cases=reporting_command_uc,
            reporting_query_use_cases=reporting_query_uc,
            consolidation_command_use_cases=consolidation_command_uc,
            consolidation_query_use_cases=consolidation_query_uc,
            tax_command_use_cases=tax_command_uc,
            tax_query_use_cases=tax_query_uc,
        )

    @staticmethod
    def context(
        *,
        repositories: BillingRepositories,
        factory: BillingFactory,
        services: BillingServices | None = None,
    ) -> BillingContext:
        return BillingContext(
            factory=factory,
            repositories=repositories,
            services=services if services is not None else BillingServices(),
            catalog=repositories.catalog,
            pricing=repositories.pricing,
            commercial_accounts=repositories.commercial_accounts,
            subscriptions_engine=repositories.subscriptions_engine,
            entitlements_engine=repositories.entitlements_engine,
            metering_engine=repositories.metering_engine,
            rating_engine=repositories.rating_engine,
            ledger_engine=repositories.ledger_engine,
            invoice_engine=repositories.invoice_engine,
            tax_engine=repositories.tax_engine,
            payment_engine=repositories.payment_engine,
            collections_engine=repositories.collections_engine,
            revenue_engine=repositories.revenue_engine,
            reconciliation_engine=repositories.reconciliation_engine,
            incident_engine=repositories.incident_engine,
            investigation_engine=repositories.investigation_engine,
            reporting_engine=repositories.reporting_engine,
            consolidation_engine=repositories.consolidation_engine,
        )
