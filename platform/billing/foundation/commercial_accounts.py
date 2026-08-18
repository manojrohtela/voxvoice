"""Billing Accounts domain (M9.8) — who has the commercial relationship with VoxVoice.

M9.6 said what is sold. M9.7 said what it costs. This module says **who we sell to**, and
deliberately nothing else:

**Billing Accounts own** commercial identity, billing hierarchy, payer relationships,
contacts, addresses, legal entity, billing and invoice preferences, a tax-profile
*reference*, credit profile and limit, commercial status, notes, activity, and history.
**They own no** subscription, price, catalog entry, usage record, payment, runtime object,
or deployment — not one type here holds any of them, and a test asserts it.

Two boundaries are load-bearing and both are ADR obligations rather than taste:

* **The billing hierarchy is separate from organization tenancy** (ADR-0023 §5.2,
  Blueprint §17). `org_id` is a *reference* Billing reads for scoping; a partner parent with
  child accounts is a **billing** relationship, not an identity one. That separation is what
  makes reseller / OEM / white-label a configuration exercise instead of a change to Epic 8
  tenancy — a test asserts no Epic 8 module is imported or written here.
* **Revenue outlives the customer record** (ADR-0023 RS4). Closing an account ends commercial
  activity; it never removes it. The lifecycle below has exactly one edge into `DELETED`, out
  of `DRAFT`, because a draft has billed nobody. Everything else is retained, forever.

The payer relationship is modelled *separately from the parent* on purpose. "Who consolidates
this account's invoices" and "who actually pays" are different questions in every reseller and
enterprise arrangement, and collapsing them is the modelling error that forces a redesign the
first time a customer pays for a subsidiary they do not own.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum

from core.fsm import StateMachineDefinition, define

from .accounts import BillingAddress, BillingContact
from .domain import AccountKind
from .money import Currency, Money

# ── lifecycle ───────────────────────────────────────────────────────────────────


class AccountState(StrEnum):
    """The commercial lifecycle of a billing account.

    `CLOSED` is terminal for commercial activity and permanent for the record: the account
    stays readable for financial retention (RS4). `ARCHIVED` is a filing state on top of that
    — out of the working set, still fully readable."""

    DRAFT = "draft"
    ACTIVE = "active"
    SUSPENDED = "suspended"
    CLOSED = "closed"
    ARCHIVED = "archived"
    DELETED = "deleted"


class AccountTrigger(StrEnum):
    ACTIVATE = "activate"
    SUSPEND = "suspend"
    RESUME = "resume"
    CLOSE = "close"
    ARCHIVE = "archive"
    RESTORE = "restore"
    DELETE = "delete"


# The billing-account lifecycle. Four absences, each a decision:
#
#   * **there is exactly one edge into DELETED, and it starts at DRAFT.** A draft account has
#     billed nobody; everything past it has revenue attached, and ADR-0023 RS4 says revenue
#     outlives the customer record. There is deliberately no way to delete an account that has
#     ever been active — not even after closing and archiving it;
#   * there is no CLOSED → ACTIVE edge. Closing is terminal for commercial activity; resuming
#     a closed relationship is a new account, not a state change on an old one;
#   * RESTORE returns an ARCHIVED account to CLOSED rather than to ACTIVE — un-filing a record
#     and reopening a commercial relationship are separate decisions, and conflating them lets
#     the second happen by accident;
#   * SUSPENDED may be closed directly, because a customer who stops paying should not have to
#     be reactivated first in order to be let go.
ACCOUNT_LIFECYCLE: StateMachineDefinition = define(
    [
        (AccountState.DRAFT, AccountTrigger.ACTIVATE, AccountState.ACTIVE),
        (AccountState.DRAFT, AccountTrigger.DELETE, AccountState.DELETED),
        (AccountState.ACTIVE, AccountTrigger.SUSPEND, AccountState.SUSPENDED),
        (AccountState.ACTIVE, AccountTrigger.CLOSE, AccountState.CLOSED),
        (AccountState.SUSPENDED, AccountTrigger.RESUME, AccountState.ACTIVE),
        (AccountState.SUSPENDED, AccountTrigger.CLOSE, AccountState.CLOSED),
        (AccountState.CLOSED, AccountTrigger.ARCHIVE, AccountState.ARCHIVED),
        (AccountState.ARCHIVED, AccountTrigger.RESTORE, AccountState.CLOSED),
    ]
)


# ── commercial identity ─────────────────────────────────────────────────────────


class LegalEntityKind(StrEnum):
    COMPANY = "company"
    PARTNERSHIP = "partnership"
    SOLE_PROPRIETOR = "sole_proprietor"
    INDIVIDUAL = "individual"
    GOVERNMENT = "government"
    NON_PROFIT = "non_profit"


@dataclass(frozen=True, slots=True)
class LegalEntity:
    """Who the contract is actually with.

    Distinct from the display name because they routinely differ: a customer known as "Acme"
    contracts as "Acme Technologies Private Limited", and an invoice, a contract, and a tax
    filing all need the second. `tax_identifier` (GSTIN, VAT number, EIN) is held here as
    commercial identity; the *tax determination* that uses it belongs to the Tax domain, and
    this module computes none."""

    legal_name: str = ""
    kind: LegalEntityKind = LegalEntityKind.COMPANY
    registration_number: str = ""
    tax_identifier: str = ""
    incorporation_country: str = ""  # ISO-3166 alpha-2

    @property
    def is_identified(self) -> bool:
        return bool(self.legal_name)

    @property
    def identifiers(self) -> tuple[str, ...]:
        """The identifiers that must be unique across an organization's accounts — the same
        company registered twice is a reconciliation problem, not a second customer."""
        return tuple(v for v in (self.registration_number, self.tax_identifier) if v)


# ── contacts and addresses ──────────────────────────────────────────────────────


class ContactRole(StrEnum):
    BILLING = "billing"
    SHIPPING = "shipping"
    TECHNICAL = "technical"
    LEGAL = "legal"
    PROCUREMENT = "procurement"


@dataclass(frozen=True, slots=True)
class AccountContact:
    """One addressable person in a commercial role. Reuses the M9.2 `BillingContact` value
    rather than redefining it — a contact is a contact, whichever role it fills."""

    role: ContactRole = ContactRole.BILLING
    contact: BillingContact = field(default_factory=BillingContact)
    is_primary: bool = True
    title: str = ""

    @property
    def is_reachable(self) -> bool:
        return bool(self.contact.email or self.contact.phone)


class AddressKind(StrEnum):
    BILLING = "billing"
    SHIPPING = "shipping"
    LEGAL = "legal"
    REMIT_TO = "remit_to"


@dataclass(frozen=True, slots=True)
class AccountAddress:
    """One address in a commercial role, wrapping the M9.2 `BillingAddress`."""

    kind: AddressKind = AddressKind.BILLING
    address: BillingAddress = field(default_factory=BillingAddress)
    is_primary: bool = True
    label: str = ""

    @property
    def is_complete(self) -> bool:
        """Enough to invoice against: a line, a city, and a country. Postal code and region
        vary too much by jurisdiction to require globally."""
        return bool(self.address.line1 and self.address.city and self.address.country)


@dataclass(frozen=True, slots=True)
class AddressBook:
    """Every address an account holds, addressable by role.

    A collection rather than four fields because accounts legitimately carry several of one
    kind — two shipping sites, a registered office that is not the billing address — and the
    "one primary per kind" rule is then a validation, not a data-shape accident."""

    entries: tuple[AccountAddress, ...] = ()

    def of_kind(self, kind: AddressKind) -> tuple[AccountAddress, ...]:
        return tuple(e for e in self.entries if e.kind is kind)

    def primary(self, kind: AddressKind) -> AccountAddress | None:
        found = [e for e in self.entries if e.kind is kind]
        if not found:
            return None
        return next((e for e in found if e.is_primary), found[0])

    @property
    def billing_address(self) -> BillingAddress | None:
        primary = self.primary(AddressKind.BILLING)
        return primary.address if primary else None

    @property
    def kinds(self) -> tuple[AddressKind, ...]:
        return tuple(sorted({e.kind for e in self.entries}))


# ── preferences ─────────────────────────────────────────────────────────────────


class InvoiceDelivery(StrEnum):
    EMAIL = "email"
    PORTAL = "portal"
    POST = "post"
    EDI = "edi"


class StatementFrequency(StrEnum):
    NONE = "none"
    MONTHLY = "monthly"
    QUARTERLY = "quarterly"


@dataclass(frozen=True, slots=True)
class BillingPreferences:
    """How this customer wants to be billed. Preferences, never rules: nothing here decides
    what is owed, only how the document that says so is produced and delivered.

    `billing_day` is capped at 28 so every month has one — a 31st that silently becomes the
    30th, then the 28th, is the kind of drift that makes a customer's invoice date look
    arbitrary."""

    invoice_delivery: InvoiceDelivery = InvoiceDelivery.EMAIL
    invoice_language: str = "en"
    invoice_prefix: str = ""
    purchase_order_required: bool = False
    purchase_order_number: str = ""
    consolidated_invoicing: bool = False
    billing_day: int = 1  # 1..28
    payment_terms_days: int = 30
    statement_frequency: StatementFrequency = StatementFrequency.NONE
    timezone: str = "UTC"

    @property
    def requires_po(self) -> bool:
        return self.purchase_order_required


# ── credit ──────────────────────────────────────────────────────────────────────


class RiskRating(StrEnum):
    UNRATED = "unrated"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class CommercialStatus(StrEnum):
    """The commercial standing of the relationship, separate from its lifecycle state.

    An account can be `ACTIVE` and `DELINQUENT` at the same time, and that combination is the
    entire point: the lifecycle says whether the relationship exists, the commercial status
    says how it is going."""

    GOOD_STANDING = "good_standing"
    WATCH = "watch"
    CREDIT_HOLD = "credit_hold"
    DELINQUENT = "delinquent"
    WRITE_OFF = "write_off"


@dataclass(frozen=True, slots=True)
class CreditProfile:
    """What we are willing to be owed, and how comfortable we are about it.

    `credit_limit` is a **policy figure**, not a balance. Nothing here knows what the customer
    currently owes — that is the Ledger's — so `permits` takes the exposure as an argument and
    compares two `Money` values in the same currency. Deciding whether to extend credit is a
    commercial rule; computing the exposure is not this domain's."""

    credit_limit: Money | None = None  # None ⇒ no limit set
    credit_hold: bool = False
    risk_rating: RiskRating = RiskRating.UNRATED
    payment_terms_days: int = 30
    dunning_exempt: bool = False
    assessed_at: float = 0.0
    assessed_by: str = ""
    notes: str = ""

    @property
    def has_limit(self) -> bool:
        return self.credit_limit is not None

    @property
    def is_unlimited(self) -> bool:
        return self.credit_limit is None and not self.credit_hold

    def permits(self, exposure: Money) -> bool:
        """Whether `exposure` sits within the limit. Comparison only — no arithmetic on a
        balance this domain does not own, and a currency mismatch is a definite no rather
        than a silent conversion."""
        if self.credit_hold:
            return False
        if self.credit_limit is None:
            return True
        if exposure.currency is not self.credit_limit.currency:
            return False
        return exposure.minor <= self.credit_limit.minor


# ── notes ───────────────────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class AccountNote:
    """A dated, attributed note on the commercial relationship. Append-only in practice: the
    aggregate offers no way to edit or remove one, because "what did we tell this customer,
    and when" is evidence."""

    id: str
    body: str
    author_id: str = ""
    at: float = 0.0
    pinned: bool = False


# ── the account ─────────────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class CommercialAccount:
    """The commercial relationship: who we bill, how, and under what terms.

    Named distinctly from M9.2's `BillingAccount`, which is the minimal account the revenue
    spine already attributes to and which stays exactly as it was. This is the full commercial
    record built around the same idea — the same call M9.6 made for `Product` beside `Plan`,
    and M9.7 for `PricingBook` beside `PriceBook`.

    `account_number` is the human commercial identifier — it appears on invoices, in support
    conversations, and in a customer's own accounts-payable system, so it is stable, unique
    per organization, and never reused.

    `currency` is fixed at creation and never changes: conversion is presentation, never a
    ledger concern (ADR-0023 RS5).

    `tax_profile_id` is a **reference**. This domain holds the pointer and computes no tax."""

    id: str
    org_id: str
    account_number: str
    display_name: str
    currency: Currency
    kind: AccountKind = AccountKind.DIRECT
    state: AccountState = AccountState.DRAFT
    commercial_status: CommercialStatus = CommercialStatus.GOOD_STANDING
    legal_entity: LegalEntity = field(default_factory=LegalEntity)
    # Hierarchy and payment are two different relationships — see the module docstring.
    parent_account_id: str = ""
    payer_account_id: str = ""  # "" ⇒ this account pays for itself
    contacts: tuple[AccountContact, ...] = ()
    addresses: AddressBook = field(default_factory=AddressBook)
    preferences: BillingPreferences = field(default_factory=BillingPreferences)
    credit: CreditProfile = field(default_factory=CreditProfile)
    tax_profile_id: str = ""  # reference only; the Tax domain owns the profile
    notes: tuple[AccountNote, ...] = ()
    created_at: float = 0.0
    updated_at: float = 0.0
    activated_at: float | None = None
    closed_at: float | None = None
    close_reason: str = ""

    # ── state ───────────────────────────────────────────────────────────────────

    @property
    def is_active(self) -> bool:
        return self.state is AccountState.ACTIVE

    @property
    def is_child(self) -> bool:
        return bool(self.parent_account_id)

    @property
    def is_retained_only(self) -> bool:
        """Closed and archived accounts stay readable for financial retention (RS4) and can
        never be deleted."""
        return self.state in (AccountState.CLOSED, AccountState.ARCHIVED)

    @property
    def accrues_usage(self) -> bool:
        """A child account accrues its own usage even though its parent is invoiced —
        attribution flows down, invoicing rolls up, and both directions stay explicit."""
        return self.state in (AccountState.ACTIVE, AccountState.SUSPENDED)

    @property
    def is_billable(self) -> bool:
        """Whether an invoice may be raised *against this account*. A child is billed at its
        parent and a payer redirects payment; either makes this account not the one billed."""
        return self.is_active and not self.is_child and not self.payer_account_id

    @property
    def is_on_hold(self) -> bool:
        return self.credit.credit_hold or self.commercial_status in (
            CommercialStatus.CREDIT_HOLD,
            CommercialStatus.WRITE_OFF,
        )

    @property
    def effective_payer_id(self) -> str:
        """The account that pays. Defaults to itself; `PayerResolver` walks a chain."""
        return self.payer_account_id or self.id

    # ── convenience reads ───────────────────────────────────────────────────────

    def contact_for(self, role: ContactRole) -> AccountContact | None:
        found = [c for c in self.contacts if c.role is role]
        if not found:
            return None
        return next((c for c in found if c.is_primary), found[0])

    @property
    def billing_contact(self) -> BillingContact | None:
        contact = self.contact_for(ContactRole.BILLING)
        return contact.contact if contact else None

    @property
    def shipping_contact(self) -> BillingContact | None:
        contact = self.contact_for(ContactRole.SHIPPING)
        return contact.contact if contact else None

    @property
    def billing_address(self) -> BillingAddress | None:
        return self.addresses.billing_address

    @property
    def legal_identifiers(self) -> tuple[str, ...]:
        return self.legal_entity.identifiers


# ── hierarchy ───────────────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class AccountNode:
    """One account in a resolved billing tree, with its depth and its children.

    Recursive rather than flat because a reseller hierarchy is genuinely a tree: a distributor
    with resellers with end customers is three levels, and flattening it to parent/child would
    lose exactly the structure the model exists to carry."""

    account: CommercialAccount
    depth: int = 0
    children: tuple[AccountNode, ...] = ()

    @property
    def account_id(self) -> str:
        return self.account.id

    @property
    def size(self) -> int:
        return 1 + sum(child.size for child in self.children)

    @property
    def descendant_ids(self) -> tuple[str, ...]:
        out: list[str] = []
        for child in self.children:
            out.append(child.account_id)
            out.extend(child.descendant_ids)
        return tuple(out)

    def flatten(self) -> tuple[AccountNode, ...]:
        return (self, *[node for child in self.children for node in child.flatten()])


@dataclass(frozen=True, slots=True)
class BillingTree:
    """A whole resolved hierarchy: the root, the tree, and the account invoiced for it."""

    root: AccountNode
    invoiced_account_id: str = ""

    @property
    def account_ids(self) -> tuple[str, ...]:
        return tuple(node.account_id for node in self.root.flatten())

    @property
    def depth(self) -> int:
        return max(node.depth for node in self.root.flatten())

    @property
    def size(self) -> int:
        return self.root.size

    def contains(self, account_id: str) -> bool:
        return account_id in self.account_ids


# ── activity ────────────────────────────────────────────────────────────────────


class AccountAction(StrEnum):
    """What happened to a billing account. The activity log's closed vocabulary."""

    CREATED = "account.created"
    ACTIVATED = "account.activated"
    SUSPENDED = "account.suspended"
    RESUMED = "account.resumed"
    CLOSED = "account.closed"
    DELETED = "account.deleted"
    ARCHIVED = "account.archived"
    RESTORED = "account.restored"
    PARENT_ASSIGNED = "account.parent_assigned"
    PAYER_ASSIGNED = "account.payer_assigned"
    PREFERENCES_UPDATED = "account.preferences_updated"
    CONTACTS_UPDATED = "account.contacts_updated"
    ADDRESSES_UPDATED = "account.addresses_updated"
    CREDIT_UPDATED = "account.credit_updated"
    NOTE_ADDED = "account.note_added"


@dataclass(frozen=True, slots=True)
class AccountActivity:
    """One append-only entry in an account's own history: who changed what, when.

    Kept alongside the `billing.*` fact stream rather than instead of it — the stream is the
    platform-wide log; this is the account's ordered, queryable history, and an account screen
    should not have to filter the whole revenue stream to render "who put this customer on
    credit hold?"."""

    id: str
    org_id: str
    account_id: str
    action: AccountAction
    actor_id: str = ""
    at: float = 0.0
    from_state: str = ""
    to_state: str = ""
    detail: str = ""


# ── commercial profile ──────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class CommercialProfileView:
    """The composed answer to "tell me about this customer commercially".

    A read model rather than a stored aggregate: everything in it is derived from the account
    and its neighbours, so there is no second copy of a fact to fall out of date (§A2.5)."""

    account: CommercialAccount
    parent: CommercialAccount | None = None
    payer: CommercialAccount | None = None
    child_count: int = 0
    descendant_count: int = 0
    hierarchy_depth: int = 0
    payer_chain: tuple[str, ...] = ()

    @property
    def is_self_paying(self) -> bool:
        return self.payer is None

    @property
    def is_root(self) -> bool:
        return self.parent is None

    @property
    def invoiced_account_id(self) -> str:
        """Who actually receives the invoice: the ultimate payer if one is set, otherwise the
        root of the billing hierarchy, otherwise this account."""
        if self.payer is not None:
            return self.payer.id
        if self.parent is not None:
            return self.parent.id
        return self.account.id


# ── search ──────────────────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class AccountSearchQuery:
    """A deterministic filter. No relevance scoring and no index — the same accounts and the
    same query always come back in the same order, which is what a paged admin screen and a
    reproducible test both need."""

    text: str = ""  # case-insensitive substring over number, name, legal name, identifiers
    states: tuple[AccountState, ...] = ()
    kinds: tuple[AccountKind, ...] = ()
    statuses: tuple[CommercialStatus, ...] = ()
    currencies: tuple[Currency, ...] = ()
    countries: tuple[str, ...] = ()
    risk_ratings: tuple[RiskRating, ...] = ()
    parent_account_id: str = ""
    payer_account_id: str = ""
    roots_only: bool = False
    on_hold_only: bool = False
    limit: int = 100
    offset: int = 0


@dataclass(frozen=True, slots=True)
class AccountSearchResult:
    """A page of matches plus the unpaged total, so a caller can render "20 of 137"."""

    accounts: tuple[CommercialAccount, ...] = ()
    total: int = 0
