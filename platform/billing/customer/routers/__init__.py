"""Billing transport routers (M9.5) — one per bounded context.

`ALL_ROUTERS` is the ordered list the app factory includes: `system` first (probes must work
even when a domain router does not), then the commercial lifecycle in the order revenue
actually flows — account → catalog → pricing → subscription → entitlement → usage → ledger →
credit → invoice → payment → refund → tax → contract → license.

Every router is thin: it maps HTTP ↔ Application command/query and nothing else.
"""

from __future__ import annotations

from . import (
    accounts,
    billing_accounts,
    catalog,
    catalog_admin,
    collections_engine,
    consolidation_engine,
    contracts,
    credits,
    entitlement_engine,
    entitlements,
    incident_engine,
    investigation_engine,
    invoice_engine,
    invoices,
    ledger,
    ledger_engine,
    licenses,
    metering,
    metering_engine,
    payment_engine,
    payments,
    pricing,
    pricing_engine,
    rating_engine,
    reconciliation_engine,
    refunds,
    reporting_engine,
    revenue_engine,
    subscription_engine,
    subscriptions,
    system,
    tax_engine,
    taxes,
)

# System routes are unprefixed (probes live at the root); domain routers are mounted under
# the configured API prefix by the app factory.
SYSTEM_ROUTER = system.router

DOMAIN_ROUTERS = [
    accounts.router,
    billing_accounts.router,  # M9.8 — billing accounts (own prefix; no path collides)
    catalog.router,
    catalog_admin.router,  # M9.6 — commercial catalog (additive; no path collides)
    pricing.router,
    pricing_engine.router,  # M9.7 — pricing engine (additive; no path collides)
    subscriptions.router,
    subscription_engine.router,  # M9.9 — subscription engine (own prefix)
    entitlements.router,
    entitlement_engine.router,  # M9.10 — entitlement engine (own prefix)
    metering.router,
    metering_engine.router,  # M9.11 — metering engine (own prefix)
    rating_engine.router,  # M9.12 — rating engine (own prefix)
    ledger_engine.router,  # M9.13 — credit & revenue ledger (own prefix)
    ledger.router,
    credits.router,
    invoice_engine.router,  # M9.14 — invoice engine (own prefix)
    invoices.router,
    payment_engine.router,  # M9.16 — payment engine (own prefix)
    collections_engine.router,  # M9.17 — dunning & collections (own prefix)
    revenue_engine.router,  # M9.18 — revenue recognition (own prefix)
    reconciliation_engine.router,  # M9.19 — financial integrity (own prefix)
    incident_engine.router,  # M9.20 — correlation (same prefix, disjoint paths)
    investigation_engine.router,  # M9.21 — investigation (own prefix)
    reporting_engine.router,  # M9.22 — regulatory reporting (own prefix)
    consolidation_engine.router,  # M9.23 — period scope & consolidation (own prefix)
    payments.router,
    refunds.router,
    tax_engine.router,  # M9.15 — tax determination engine (own prefix)
    taxes.router,
    contracts.router,
    licenses.router,
]

ALL_ROUTERS = [SYSTEM_ROUTER, *DOMAIN_ROUTERS]
