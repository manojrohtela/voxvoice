"""Billing Persistence (M9.4) — durable storage for the revenue plane.

The **only** production implementation of the Foundation's 29 repository contracts and the
Application's `UnitOfWork` abstraction. It owns storage and nothing else: no business rule,
no orchestration, no transport, no provider call.

    persistence → application → foundation → core

and never the other way. Neither the Foundation nor the Application imports this package,
mentions SQLModel, or knows a database exists; both remain byte-for-byte unchanged from
M9.2/M9.3. Swapping the seams is the whole mechanism — `build_persistent_billing_application`
is the one place it happens.

Public surface (ADR-0031 §12): the composition root, the database handle, the Unit of Work,
the durable fact stream, and the repository implementations. `models`, `mapping`, and `codec`
are internal — no SQLModel row type crosses this boundary, in either direction.
"""

from __future__ import annotations

from .account_repositories import (
    ACCOUNT_SQL_REPOSITORIES,
    SqlAccountActivityRepository,
    SqlAccountNumberAllocator,
    SqlCommercialAccountRepository,
)
from .catalog_repositories import (
    CATALOG_SQL_REPOSITORIES,
    SqlBundleRepository,
    SqlCatalogActivityRepository,
    SqlCatalogVersionRepository,
    SqlEditionRepository,
    SqlFeatureGroupRepository,
    SqlProductRepository,
)
from .composition import (
    PersistentBillingApplication,
    # `build_account_repositories` and `build_subscription_repositories` were declared in
    # `__all__` at M9.8/M9.9 but never imported here, so importing either by name failed. Found
    # while wiring M9.10 and fixed additively — the two names now resolve, as they always claimed
    # to. See the M9.10 spec, "Defects found and fixed".
    build_account_repositories,
    build_auxiliary_repositories,
    build_catalog_repositories,
    build_entitlement_repositories,
    build_invoice_repositories,
    build_ledger_repositories,
    build_metering_repositories,
    build_persistent_billing_application,
    build_pricing_repositories,
    build_rating_repositories,
    build_repositories,
    build_subscription_repositories,
    build_tax_repositories,
    migrate,
)
from .database import BillingDatabase
from .entitlement_repositories import (
    ENTITLEMENT_SQL_REPOSITORIES,
    SqlEntitlementActivityRepository,
    SqlEntitlementBundleRepository,
    SqlEntitlementDefinitionRepository,
    SqlEntitlementProfileRepository,
    SqlEntitlementVersionRepository,
)
from .event_store import SqlBillingEventStore
from .metering_repositories import (
    METERING_SQL_REPOSITORIES,
    SqlCommercialMeterRepository,
    SqlCommercialUsageRepository,
    SqlMeteringActivityRepository,
    SqlQuarantineEntryRepository,
    SqlUsageAggregateSnapshotRepository,
    SqlUsageCorrectionRepository,
    SqlUsagePeriodRepository,
)
from .migrations import MIGRATIONS
from .pricing_repositories import (
    PRICING_SQL_REPOSITORIES,
    SqlCouponRepository,
    SqlDiscountPolicyRepository,
    SqlPriceBookVersionRepository,
    SqlPricingActivityRepository,
    SqlPricingBookRepository,
    SqlPricingVersionRepository,
    SqlPromotionRepository,
)
from .repositories import (
    SQL_REPOSITORIES,
    SqlBillingAccountRepository,
    SqlBillingEventRepository,
    SqlBillingRunRepository,
    SqlCatalogSnapshotRepository,
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
from .subscription_repositories import (
    SUBSCRIPTION_SQL_REPOSITORIES,
    SqlCommercialSubscriptionRepository,
    SqlSubscriptionActivityRepository,
    SqlSubscriptionNumberAllocator,
    SqlSubscriptionVersionRepository,
)
from .unit_of_work import BillingUnitOfWorkFactory, SqlBillingUnitOfWork

__all__ = [
    "ACCOUNT_SQL_REPOSITORIES",
    "ENTITLEMENT_SQL_REPOSITORIES",
    "METERING_SQL_REPOSITORIES",
    "SqlCommercialMeterRepository",
    "SqlCommercialUsageRepository",
    "SqlMeteringActivityRepository",
    "SqlQuarantineEntryRepository",
    "SqlUsageAggregateSnapshotRepository",
    "SqlUsageCorrectionRepository",
    "SqlUsagePeriodRepository",
    "build_metering_repositories",
    "build_invoice_repositories",
    "build_tax_repositories",
    "build_ledger_repositories",
    "build_rating_repositories",
    "SqlEntitlementActivityRepository",
    "SqlEntitlementBundleRepository",
    "SqlEntitlementDefinitionRepository",
    "SqlEntitlementProfileRepository",
    "SqlEntitlementVersionRepository",
    "build_entitlement_repositories",
    "CATALOG_SQL_REPOSITORIES",
    "MIGRATIONS",
    "PRICING_SQL_REPOSITORIES",
    "SUBSCRIPTION_SQL_REPOSITORIES",
    "SQL_REPOSITORIES",
    "BillingDatabase",
    "BillingUnitOfWorkFactory",
    "PersistentBillingApplication",
    "SqlBundleRepository",
    "SqlCatalogActivityRepository",
    "SqlCatalogVersionRepository",
    "SqlEditionRepository",
    "SqlFeatureGroupRepository",
    "SqlProductRepository",
    "build_catalog_repositories",
    "build_pricing_repositories",
    "build_subscription_repositories",
    "SqlCommercialSubscriptionRepository",
    "SqlSubscriptionActivityRepository",
    "SqlSubscriptionNumberAllocator",
    "SqlSubscriptionVersionRepository",
    "build_account_repositories",
    "SqlAccountActivityRepository",
    "SqlAccountNumberAllocator",
    "SqlCommercialAccountRepository",
    "SqlCouponRepository",
    "SqlDiscountPolicyRepository",
    "SqlPriceBookVersionRepository",
    "SqlPricingActivityRepository",
    "SqlPricingBookRepository",
    "SqlPricingVersionRepository",
    "SqlPromotionRepository",
    "SqlBillingAccountRepository",
    "SqlBillingEventRepository",
    "SqlBillingEventStore",
    "SqlBillingRunRepository",
    "SqlBillingUnitOfWork",
    "SqlCatalogSnapshotRepository",
    "SqlContractRepository",
    "SqlCreditNoteRepository",
    "SqlCreditRepository",
    "SqlDisputeRepository",
    "SqlDunningRepository",
    "SqlEntitlementGrantRepository",
    "SqlEntitlementSnapshotRepository",
    "SqlFeatureRepository",
    "SqlInvoiceNumberAllocator",
    "SqlInvoiceRepository",
    "SqlLedgerRepository",
    "SqlLicenseRepository",
    "SqlMeterRepository",
    "SqlPaymentInstrumentRepository",
    "SqlPaymentRepository",
    "SqlPlanRepository",
    "SqlPriceBookRepository",
    "SqlReconciliationRepository",
    "SqlRefundRepository",
    "SqlSubscriptionRepository",
    "SqlTaxDeterminationRepository",
    "SqlTaxProfileRepository",
    "SqlUsageAggregateRepository",
    "SqlUsageQuarantineRepository",
    "SqlUsageRecordRepository",
    "build_auxiliary_repositories",
    "build_persistent_billing_application",
    "build_repositories",
    "migrate",
]
