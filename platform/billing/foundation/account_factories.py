"""Billing Account factories + repository contracts (M9.8).

`AccountFactory` is the one place a commercial account aggregate is constructed, following the
M9.2 factory discipline exactly: validate structurally, **fail fast** on error-severity
findings (`BillingValidationError`, §A2.6), compose an immutable aggregate from the injected id
and clock seams, and emit the corresponding `billing.*` fact through the injected emitter.

No I/O, no persistence, no networking. Emission is the only effect and it is injected.

The repository interfaces below extend the M9.2 seam additively — same shape, same rules:
org-scoped reads by construction (§A11.2), and the account-activity store deliberately declares
**no update and no delete**. An account history that could be edited would not be a history.

`AccountNumberAllocator` is a seam rather than a method because account numbers are a
*sequence*, not a random id: they appear on invoices and in a customer's own accounts-payable
system, so they must be allocated once, never reused, and never guessed. Same shape as M9.4's
`InvoiceNumberAllocator`, for the same reason.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Sequence
from dataclasses import replace

from .account_rules import AccountValidator
from .accounts import BillingAddress, BillingContact
from .commercial_accounts import (
    AccountAction,
    AccountActivity,
    AccountAddress,
    AccountContact,
    AccountNote,
    AccountState,
    AddressBook,
    AddressKind,
    BillingPreferences,
    CommercialAccount,
    CommercialStatus,
    ContactRole,
    CreditProfile,
    LegalEntity,
)
from .domain import AccountKind
from .errors import BillingValidationError
from .events import BillingEvent, BillingEventType, EventEmitter, NullEventEmitter
from .money import Currency
from .seams import Clock, IdGenerator, SequentialIdGenerator, SystemClock

PREFIX_COMMERCIAL_ACCOUNT = "cma"
PREFIX_ACCOUNT_ACTIVITY = "aac"
PREFIX_ACCOUNT_NOTE = "ant"

#: The default account-number shape: a stable prefix plus a zero-padded sequence. Chosen to be
#: short enough to read aloud on a support call and wide enough not to need reformatting.
ACCOUNT_NUMBER_PREFIX = "BA"
ACCOUNT_NUMBER_WIDTH = 6


def format_account_number(sequence: int, *, prefix: str = ACCOUNT_NUMBER_PREFIX) -> str:
    """`BA-000042`. Pure and deterministic, so the same sequence always formats identically —
    the property that makes a number stable enough to print on a contract."""
    return f"{prefix}-{sequence:0{ACCOUNT_NUMBER_WIDTH}d}"


class AccountNumberAllocator(ABC):
    """Allocates the next account number for an organization. Monotonic, never reused."""

    @abstractmethod
    def next_number(self, org_id: str) -> str: ...


class AccountFactory:
    """Composes validated billing-account aggregates. Stateless apart from its seams."""

    def __init__(
        self,
        *,
        ids: IdGenerator | None = None,
        clock: Clock | None = None,
        emitter: EventEmitter | None = None,
        validator: AccountValidator | None = None,
        numbers: AccountNumberAllocator | None = None,
    ) -> None:
        self._ids = ids or SequentialIdGenerator()
        self._clock = clock or SystemClock()
        self._emitter = emitter or NullEventEmitter()
        self._validator = validator or AccountValidator()
        self._numbers = numbers

    # ── the account ─────────────────────────────────────────────────────────────

    def create_account(
        self,
        *,
        org_id: str,
        display_name: str,
        currency: Currency,
        account_number: str = "",
        kind: AccountKind = AccountKind.DIRECT,
        legal_entity: LegalEntity | None = None,
        parent_account_id: str = "",
        payer_account_id: str = "",
        contacts: Sequence[AccountContact] = (),
        addresses: Sequence[AccountAddress] = (),
        preferences: BillingPreferences | None = None,
        credit: CreditProfile | None = None,
        tax_profile_id: str = "",
        known: Sequence[CommercialAccount] = (),
        actor_id: str = "",
    ) -> CommercialAccount:
        """Compose a **draft** account.

        Everything starts as a draft — an account nobody has traded with cannot go live by
        accident, and the draft state is the only one from which the record can still be
        deleted outright."""
        now = self._clock.now()
        number = account_number or self._allocate(org_id)
        account = CommercialAccount(
            id=self._ids.new(PREFIX_COMMERCIAL_ACCOUNT),
            org_id=org_id,
            account_number=number,
            display_name=display_name,
            currency=currency,
            kind=kind,
            state=AccountState.DRAFT,
            legal_entity=legal_entity or LegalEntity(),
            parent_account_id=parent_account_id,
            payer_account_id=payer_account_id,
            contacts=tuple(contacts),
            addresses=AddressBook(tuple(addresses)),
            preferences=preferences or BillingPreferences(),
            credit=credit or CreditProfile(),
            tax_profile_id=tax_profile_id,
            created_at=now,
            updated_at=now,
        )
        self._fail_fast(self._validator.validate_account(account, known=known))
        self._emit(
            BillingEventType.ACCOUNT_CREATED,
            org_id,
            account.id,
            actor_id,
            account_number=number,
            currency=currency.value,
            kind=kind.value,
        )
        return account

    # ── relationships ───────────────────────────────────────────────────────────

    def assign_parent(
        self,
        account: CommercialAccount,
        *,
        parent_account_id: str,
        known: Sequence[CommercialAccount] = (),
        actor_id: str = "",
    ) -> CommercialAccount:
        """Move an account under a parent — or, with an empty id, out to the root.

        This is a *billing* relationship only. Nothing about the organization, workspace, or
        project hierarchy changes, which is what lets a reseller structure be configured
        rather than architected (ADR-0023 §5.2)."""
        moved = replace(
            account,
            parent_account_id=parent_account_id,
            kind=AccountKind.CHILD if parent_account_id else account.kind,
            updated_at=self._clock.now(),
        )
        self._fail_fast(self._validator.validate_hierarchy_edge(moved, known))
        self._emit(
            BillingEventType.ACCOUNT_PARENT_CHANGED,
            account.org_id,
            account.id,
            actor_id,
            parent_account_id=parent_account_id,
            previous_parent_id=account.parent_account_id,
        )
        return moved

    def assign_payer(
        self,
        account: CommercialAccount,
        *,
        payer_account_id: str,
        known: Sequence[CommercialAccount] = (),
        actor_id: str = "",
    ) -> CommercialAccount:
        """Point an account's payment at another account — or, with an empty id, back at
        itself. Deliberately independent of the parent edge."""
        moved = replace(account, payer_account_id=payer_account_id, updated_at=self._clock.now())
        self._fail_fast(self._validator.validate_payer_edge(moved, known))
        self._emit(
            BillingEventType.ACCOUNT_PAYER_CHANGED,
            account.org_id,
            account.id,
            actor_id,
            payer_account_id=payer_account_id,
            previous_payer_id=account.payer_account_id,
        )
        return moved

    # ── commercial detail ───────────────────────────────────────────────────────

    def update_preferences(
        self,
        account: CommercialAccount,
        preferences: BillingPreferences,
        *,
        actor_id: str = "",
    ) -> CommercialAccount:
        self._fail_fast(self._validator.validate_preferences(preferences))
        updated = replace(account, preferences=preferences, updated_at=self._clock.now())
        self._emit(
            BillingEventType.ACCOUNT_PREFERENCES_UPDATED,
            account.org_id,
            account.id,
            actor_id,
            delivery=preferences.invoice_delivery.value,
        )
        return updated

    def update_contacts(
        self,
        account: CommercialAccount,
        contacts: Sequence[AccountContact],
        *,
        actor_id: str = "",
    ) -> CommercialAccount:
        self._fail_fast(self._validator.validate_contacts(contacts))
        updated = replace(account, contacts=tuple(contacts), updated_at=self._clock.now())
        self._emit(
            BillingEventType.ACCOUNT_CONTACTS_UPDATED,
            account.org_id,
            account.id,
            actor_id,
            roles=sorted({c.role.value for c in contacts}),
        )
        return updated

    def update_addresses(
        self,
        account: CommercialAccount,
        addresses: Sequence[AccountAddress],
        *,
        actor_id: str = "",
    ) -> CommercialAccount:
        self._fail_fast(self._validator.validate_addresses(addresses))
        updated = replace(
            account, addresses=AddressBook(tuple(addresses)), updated_at=self._clock.now()
        )
        self._emit(
            BillingEventType.ACCOUNT_ADDRESSES_UPDATED,
            account.org_id,
            account.id,
            actor_id,
            kinds=sorted({a.kind.value for a in addresses}),
        )
        return updated

    def update_credit(
        self,
        account: CommercialAccount,
        credit: CreditProfile,
        *,
        commercial_status: CommercialStatus | None = None,
        actor_id: str = "",
    ) -> CommercialAccount:
        """Set the credit profile, and optionally the commercial status alongside it.

        The two travel together because they usually change together — putting a customer on
        credit hold and marking them on hold commercially is one decision, and letting the
        pair drift apart is how a downstream check reads the stale half."""
        self._fail_fast(self._validator.validate_credit(credit, currency=account.currency))
        updated = replace(
            account,
            credit=replace(credit, assessed_at=self._clock.now(), assessed_by=actor_id),
            commercial_status=(
                commercial_status if commercial_status is not None else account.commercial_status
            ),
            updated_at=self._clock.now(),
        )
        self._fail_fast(self._validator.validate_account(updated))
        self._emit(
            BillingEventType.ACCOUNT_CREDIT_UPDATED,
            account.org_id,
            account.id,
            actor_id,
            credit_hold=credit.credit_hold,
            risk=credit.risk_rating.value,
            status=updated.commercial_status.value,
        )
        return updated

    def add_note(
        self, account: CommercialAccount, *, body: str, pinned: bool = False, actor_id: str = ""
    ) -> CommercialAccount:
        """Append a note. There is no edit and no delete: what we told a customer, and when,
        is evidence."""
        note = AccountNote(
            id=self._ids.new(PREFIX_ACCOUNT_NOTE),
            body=body,
            author_id=actor_id,
            at=self._clock.now(),
            pinned=pinned,
        )
        updated = replace(account, notes=account.notes + (note,), updated_at=self._clock.now())
        self._emit(
            BillingEventType.ACCOUNT_NOTE_ADDED, account.org_id, account.id, actor_id, note=note.id
        )
        return updated

    # ── lifecycle facts ─────────────────────────────────────────────────────────

    def emit_transition(
        self,
        account: CommercialAccount,
        *,
        event_type: BillingEventType,
        actor_id: str = "",
        **data,
    ) -> None:
        """Emit the `billing.*` fact for a lifecycle move the Foundation's lifecycle service
        already applied. Kept here so every emission in this domain goes through one place."""
        self._emit(event_type, account.org_id, account.id, actor_id, **data)

    def activity(
        self,
        *,
        org_id: str,
        account_id: str,
        action: AccountAction,
        actor_id: str = "",
        from_state: str = "",
        to_state: str = "",
        detail: str = "",
    ) -> AccountActivity:
        return AccountActivity(
            id=self._ids.new(PREFIX_ACCOUNT_ACTIVITY),
            org_id=org_id,
            account_id=account_id,
            action=action,
            actor_id=actor_id,
            at=self._clock.now(),
            from_state=from_state,
            to_state=to_state,
            detail=detail,
        )

    # ── convenience constructors ────────────────────────────────────────────────

    @staticmethod
    def contact(
        role: ContactRole,
        *,
        email: str = "",
        name: str = "",
        phone: str = "",
        title: str = "",
        is_primary: bool = True,
    ) -> AccountContact:
        return AccountContact(
            role=role,
            contact=BillingContact(email=email, name=name, phone=phone),
            is_primary=is_primary,
            title=title,
        )

    @staticmethod
    def address(
        kind: AddressKind,
        *,
        line1: str = "",
        line2: str = "",
        city: str = "",
        region: str = "",
        postal_code: str = "",
        country: str = "",
        label: str = "",
        is_primary: bool = True,
    ) -> AccountAddress:
        return AccountAddress(
            kind=kind,
            address=BillingAddress(
                line1=line1,
                line2=line2,
                city=city,
                region=region,
                postal_code=postal_code,
                country=country,
            ),
            is_primary=is_primary,
            label=label,
        )

    # ── internals ───────────────────────────────────────────────────────────────

    def _allocate(self, org_id: str) -> str:
        if self._numbers is not None:
            return self._numbers.next_number(org_id)
        # No allocator wired (pure-domain exercises): derive a unique number from the id seam
        # so the aggregate is still well-formed. Production always injects an allocator.
        minted = self._ids.new("seq").rsplit("_", 1)[-1]
        return format_account_number(int(minted) if minted.isdigit() else 1)

    def _fail_fast(self, diagnostics) -> None:
        if diagnostics.has_errors:
            raise BillingValidationError(diagnostics)

    def _emit(
        self,
        event_type: BillingEventType,
        org_id: str,
        subject_id: str,
        actor_id: str,
        **data,
    ) -> None:
        self._emitter.emit(
            BillingEvent(
                id=self._ids.new("bev"),
                type=event_type,
                ts=self._clock.now(),
                org_id=org_id,
                subject_id=subject_id,
                actor_id=actor_id,
                data=dict(data),
            )
        )


# ── repository contracts (additive to the M9.2 seam) ────────────────────────────


class CommercialAccountRepository(ABC):
    """The commercial account book. Org-scoped by construction: every read filters on
    `org_id`, so a cross-tenant query is not expressible (§A11.2)."""

    @abstractmethod
    def add(self, account: CommercialAccount) -> CommercialAccount: ...
    @abstractmethod
    def get(self, org_id: str, account_id: str) -> CommercialAccount | None: ...
    @abstractmethod
    def by_number(self, org_id: str, account_number: str) -> CommercialAccount | None: ...
    @abstractmethod
    def all(self, org_id: str) -> tuple[CommercialAccount, ...]: ...
    @abstractmethod
    def children_of(self, org_id: str, parent_account_id: str) -> tuple[CommercialAccount, ...]: ...
    @abstractmethod
    def replace(self, account: CommercialAccount) -> CommercialAccount:
        """Whole-aggregate replacement. Accounts are mutable while the relationship is live;
        closing and archiving are what make the record read-only, and that is a lifecycle
        decision, not a repository one."""


class AccountActivityRepository(ABC):
    """Append-only account history. No update, no delete — there is no method to call."""

    @abstractmethod
    def append(self, activity: AccountActivity) -> AccountActivity: ...
    @abstractmethod
    def history(
        self, org_id: str, account_id: str = "", limit: int = 100
    ) -> tuple[AccountActivity, ...]:
        """Newest first. `account_id=""` spans the whole organization."""


__all__ = [
    "ACCOUNT_NUMBER_PREFIX",
    "ACCOUNT_NUMBER_WIDTH",
    "PREFIX_ACCOUNT_ACTIVITY",
    "PREFIX_ACCOUNT_NOTE",
    "PREFIX_COMMERCIAL_ACCOUNT",
    "AccountActivityRepository",
    "AccountFactory",
    "AccountNumberAllocator",
    "CommercialAccountRepository",
    "format_account_number",
]
