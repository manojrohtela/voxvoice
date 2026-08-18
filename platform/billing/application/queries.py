"""Queries (M9.3) — immutable read intent. No transaction, no audit, no mutation.

Each query names an org (isolation is structural, §A11.2) and is answered by a read use
case that fetches through repository interfaces and maps to a view DTO. Reads derive
nothing new: where a number is computed (a ledger balance, a usage total, a quota verdict),
the computation is a **Foundation** service or classmethod, called — never re-implemented.
"""

from __future__ import annotations

from dataclasses import dataclass

from billing.foundation.domain import BillingPeriod


@dataclass(frozen=True, slots=True)
class _Query:
    actor_id: str
    org_id: str


# ── accounts ────────────────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class GetAccount(_Query):
    account_id: str


@dataclass(frozen=True, slots=True)
class GetAccountHierarchy(_Query):
    account_id: str


# ── catalog + pricing ───────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class ListPublishedPlans(_Query):
    pass


@dataclass(frozen=True, slots=True)
class GetPriceBook(_Query):
    price_book_id: str
    version: int


# ── subscriptions ───────────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class GetSubscription(_Query):
    subscription_id: str


@dataclass(frozen=True, slots=True)
class ListSubscriptions(_Query):
    account_id: str


# ── entitlements ────────────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class GetEntitlementSnapshot(_Query):
    account_id: str


@dataclass(frozen=True, slots=True)
class GetQuotaVerdict(_Query):
    """Reports allowance/consumption/classification/staleness. **Not** an authorization
    answer — the plane that owns the write decides (ADR-0023 §11.1)."""

    account_id: str
    entitlement_code: str
    consumed: int
    at: float


# ── usage ───────────────────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class GetUsageSummary(_Query):
    account_id: str
    meter_code: str
    period: BillingPeriod


# ── ledger + credits ────────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class GetLedgerBalance(_Query):
    account_id: str


@dataclass(frozen=True, slots=True)
class GetCreditBalance(_Query):
    account_id: str


# ── invoices ────────────────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class GetInvoice(_Query):
    invoice_id: str


@dataclass(frozen=True, slots=True)
class ListInvoices(_Query):
    account_id: str


# ── payments + refunds ──────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class ListPayments(_Query):
    invoice_id: str


@dataclass(frozen=True, slots=True)
class ListRefunds(_Query):
    payment_id: str


# ── tax ─────────────────────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class GetTaxProfile(_Query):
    account_id: str


@dataclass(frozen=True, slots=True)
class GetTaxDetermination(_Query):
    invoice_id: str


# ── contracts + licenses ────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class ListContracts(_Query):
    account_id: str


@dataclass(frozen=True, slots=True)
class ListLicenses(_Query):
    account_id: str


#: Every query type — wiring and the coverage test both read this.
ALL_QUERIES: tuple[type, ...] = (
    GetAccount,
    GetAccountHierarchy,
    ListPublishedPlans,
    GetPriceBook,
    GetSubscription,
    ListSubscriptions,
    GetEntitlementSnapshot,
    GetQuotaVerdict,
    GetUsageSummary,
    GetLedgerBalance,
    GetCreditBalance,
    GetInvoice,
    ListInvoices,
    ListPayments,
    ListRefunds,
    GetTaxProfile,
    GetTaxDetermination,
    ListContracts,
    ListLicenses,
)
