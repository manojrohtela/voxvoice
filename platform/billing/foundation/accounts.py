"""Billing Accounts (M9.5 domain) — the commercial identity of a customer.

ADR-0023 §5.1: the billing account is a **revenue fact Billing owns**; it *references* an
organization the Customer Platform owns and never copies it as truth (§5.2). Two
consequences are ratified here and encoded below:

* **The account hierarchy is separate from organization tenancy** (Blueprint §17) — a
  partner parent with child accounts is a *billing* relationship, not an identity one.
  This is what makes reseller / OEM / white-label configuration rather than redesign.
* **Deletion asymmetry** (ADR-0023 RS4): deleting an organization does not delete its
  billing account. Revenue records outlive the customer record for legal retention; the
  account is CLOSED and retained, never removed.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from .domain import AccountKind
from .money import Currency


class AccountStatus(StrEnum):
    ACTIVE = "active"
    DELINQUENT = "delinquent"  # payment overdue; entitlements contracted, data intact
    SUSPENDED = "suspended"
    CLOSED = "closed"  # terminal for commercial activity; records retained (RS4)


@dataclass(frozen=True, slots=True)
class BillingContact:
    """Who receives billing documents. Held by Billing because it is a *billing* profile,
    not a platform identity — the Customer Platform's user records stay untouched."""

    email: str = ""
    name: str = ""
    phone: str = ""


@dataclass(frozen=True, slots=True)
class BillingAddress:
    """The address a tax determination and an invoice are made against. Owned by Billing
    (it is tax evidence input, ADR-0023 TX3); country is ISO-3166 alpha-2."""

    line1: str = ""
    line2: str = ""
    city: str = ""
    region: str = ""
    postal_code: str = ""
    country: str = ""


@dataclass(frozen=True, slots=True)
class BillingAccount:
    """The account every revenue fact attributes to. One per organization.

    `org_id` is a **reference**: Billing reads it for scoping and attribution and never
    writes an organization fact. `currency` is fixed at creation and never changes —
    conversion is presentation, never a ledger concern (ADR-0023 RS5)."""

    id: str
    org_id: str
    display_name: str
    currency: Currency
    kind: AccountKind = AccountKind.DIRECT
    status: AccountStatus = AccountStatus.ACTIVE
    parent_account_id: str = ""  # set for CHILD accounts (consolidated billing)
    contact: BillingContact = BillingContact()
    address: BillingAddress = BillingAddress()
    created_at: float = 0.0
    closed_at: float | None = None

    @property
    def is_child(self) -> bool:
        return bool(self.parent_account_id)

    @property
    def is_billable(self) -> bool:
        """A child account accrues usage but is invoiced at its parent (Blueprint §16)."""
        return not self.is_child and self.status is not AccountStatus.CLOSED

    @property
    def is_retained_only(self) -> bool:
        """CLOSED accounts remain readable for financial retention and are never deleted."""
        return self.status is AccountStatus.CLOSED


@dataclass(frozen=True, slots=True)
class AccountHierarchy:
    """An immutable resolved view of a parent and its children.

    Attribution flows **down** (usage attaches to the child that produced it); invoicing
    rolls **up** (the parent is billed). Both directions are explicit so neither is
    inferred at rating time."""

    parent: BillingAccount
    children: tuple[BillingAccount, ...] = ()

    @property
    def account_ids(self) -> tuple[str, ...]:
        return (self.parent.id, *(c.id for c in self.children))

    @property
    def invoiced_account(self) -> BillingAccount:
        return self.parent

    def contains(self, account_id: str) -> bool:
        return account_id in self.account_ids
