"""Billing persistence migrations (M9.4) — declarative schema only.

This package **continues the platform's single migration chain**, it does not start a second
one: the Customer Platform's chain ends at `0012` (`api/persistence/migrations`), so Billing
begins at `0013`. One global sequence, ordered and contiguous, is what makes "which schema
version is this database at?" a question with one answer.

Each module is idempotent (create-if-absent), touches only `cp_billing_*` tables, and
contains **no application logic**. No Epic 5–8 table is read, altered, or reused.

On a fresh database `BillingDatabase.create_all()` builds the whole set from the model
definitions, so these migrations are no-ops there; they exist for databases that already
carry `0001–0012`. A migration tool (e.g. Alembic — still not a dependency) can adopt this
same ordered structure later without reshaping it.
"""

from __future__ import annotations

from . import (
    v0013_billing_commercial,
    v0014_billing_revenue,
    v0015_billing_settlement,
    v0016_billing_catalog,
    v0017_billing_pricing,
    v0018_billing_accounts,
    v0019_billing_subscriptions,
    v0020_billing_entitlements,
    v0021_billing_metering,
    v0022_billing_rating,
    v0023_billing_ledger,
    v0024_billing_invoicing,
    v0025_billing_tax,
    v0026_billing_payments,
    v0027_billing_collections,
    v0028_billing_revenue,
    v0029_billing_reconciliation,
    v0030_billing_incidents,
    v0031_billing_investigations,
    v0032_billing_reporting,
    v0033_billing_consolidation,
)

# Ordered migration registry. Append future migrations; never reorder or mutate an applied
# entry — append-only, like the ledger it creates.
MIGRATIONS = (
    v0013_billing_commercial,
    v0014_billing_revenue,
    v0015_billing_settlement,
    v0016_billing_catalog,
    v0017_billing_pricing,
    v0018_billing_accounts,
    v0019_billing_subscriptions,
    v0020_billing_entitlements,
    v0021_billing_metering,
    v0022_billing_rating,
    v0023_billing_ledger,
    v0024_billing_invoicing,
    v0025_billing_tax,
    v0026_billing_payments,
    v0027_billing_collections,
    v0028_billing_revenue,
    v0029_billing_reconciliation,
    v0030_billing_incidents,
    v0031_billing_investigations,
    v0032_billing_reporting,
    v0033_billing_consolidation,
)

# The chain this one continues (`api/persistence/migrations` 0001–0012), asserted by the
# migration-chain test rather than assumed.
CHAIN_STARTS_AFTER = "0012"


def upgrade_all(database) -> tuple[str, ...]:
    """Apply every Billing migration in order. Returns the versions applied."""
    for module in MIGRATIONS:
        module.upgrade(database)
    return tuple(module.VERSION for module in MIGRATIONS)
