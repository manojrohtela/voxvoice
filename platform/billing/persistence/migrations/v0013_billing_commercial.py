"""Migration 0013 — Billing commercial structure (M9.4).

Declarative schema only. Creates the tables that describe *what is sold and to whom*:
accounts, catalog (plans, features, meters, published catalog versions), price books,
subscriptions, and entitlements. Nothing here records money moving — that is 0014.

Additive: creates `cp_billing_*` tables only, `ALTER`s nothing, and touches no Epic 5–8
table. Idempotent: each table is created only if absent.
"""

from __future__ import annotations

from sqlalchemy import inspect

from ..database import BillingDatabase
from ..models import (
    BillingAccountRow,
    BillingCatalogSnapshotRow,
    BillingEntitlementGrantRow,
    BillingEntitlementSnapshotRow,
    BillingFeatureRow,
    BillingMeterRow,
    BillingPlanRow,
    BillingPriceBookRow,
    BillingSubscriptionRow,
)

VERSION = "0013"
DESCRIPTION = "cp_billing_* commercial structure (accounts, catalog, pricing, subscriptions)"

TABLES = (
    BillingAccountRow,
    BillingPlanRow,
    BillingFeatureRow,
    BillingMeterRow,
    BillingCatalogSnapshotRow,
    BillingPriceBookRow,
    BillingSubscriptionRow,
    BillingEntitlementGrantRow,
    BillingEntitlementSnapshotRow,
)


def upgrade(database: BillingDatabase) -> None:
    existing = set(inspect(database.engine).get_table_names())
    for table in TABLES:
        if table.__tablename__ not in existing:
            table.__table__.create(database.engine, checkfirst=True)


def downgrade(database: BillingDatabase) -> None:
    """No-op. Commercial structure carries live subscriptions and entitlement history;
    dropping it to undo a deployment would destroy revenue evidence (ADR-0023 RS4)."""
    return None
