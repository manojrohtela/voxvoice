"""Billing Accounts application layer (M9.8) — commands, queries, use cases, mapping.

Additive to M9.3/M9.6/M9.7: the buses, Unit of Work, authorization orchestrator,
application-event vocabulary, and dispatch discipline are reused exactly as they are. This
module adds 13 commands, 5 queries, their use cases, and the view DTOs — and nothing else.

The four beats are unchanged: **authorize → open a transaction → coordinate the Foundation →
stage an event.** Every account rule — number and identifier uniqueness, hierarchy legality,
cycle and payer-loop detection, address and contact consistency, credit limits, commercial
status, lifecycle legality, search filtering, profile composition — is Foundation behaviour
these methods *call*. There is no rule here, and a test greps for the ones that would be
tempting to inline.

One name differs from the milestone brief on purpose, because M9.3 already owns it:
`CloseCommercialAccount` (M9.3 has a `CloseBillingAccount` command that closes the M9.2
`BillingAccount`). Two clear names beat one name that quietly changed meaning — the same call
M9.6 made for `CreateCatalogPlan` and M9.7 for `PublishPricingBook`.

As in M9.6 and M9.7, **every command also appends an `AccountActivity`**, inside the same
transaction as the change it describes. An account history that could disagree with the account
would be worse than no history at all.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from billing.foundation import (
    AccountAction,
    AccountAddress,
    AccountContact,
    AccountKind,
    AccountNode,
    AccountSearchQuery,
    AccountTrigger,
    BillingPreferences,
    CommercialAccount,
    CommercialProfileView,
    CommercialStatus,
    CreditProfile,
    Currency,
    LegalEntity,
    Money,
)
from billing.foundation import BillingNotFound as _NotFound

from .authorization import AuthorizationOrchestrator
from .authorization import BillingPermission as P
from .commands import _Command
from .events import PREFIX_APP_EVENT, ApplicationEvent, ApplicationEventType
from .mapping import MoneyView
from .queries import _Query
from .unit_of_work import UnitOfWorkFactory

# ── commands ────────────────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class CreateBillingAccount(_Command):
    display_name: str = ""
    currency: Currency = Currency.INR
    account_number: str = ""  # "" ⇒ allocated
    kind: AccountKind = AccountKind.DIRECT
    legal_entity: LegalEntity = field(default_factory=LegalEntity)
    parent_account_id: str = ""
    payer_account_id: str = ""
    contacts: tuple[AccountContact, ...] = ()
    addresses: tuple[AccountAddress, ...] = ()
    preferences: BillingPreferences = field(default_factory=BillingPreferences)
    credit: CreditProfile = field(default_factory=CreditProfile)
    tax_profile_id: str = ""


@dataclass(frozen=True, slots=True)
class ActivateBillingAccount(_Command):
    """Make an account active. A draft is *activated*, a suspended account is *resumed*; the
    Foundation picks the trigger so the history records which one actually happened."""

    account_id: str = ""


@dataclass(frozen=True, slots=True)
class SuspendBillingAccount(_Command):
    account_id: str = ""
    reason: str = ""


@dataclass(frozen=True, slots=True)
class CloseCommercialAccount(_Command):
    """Named distinctly from M9.3's `CloseBillingAccount`, which closes the M9.2 aggregate."""

    account_id: str = ""
    reason: str = ""


@dataclass(frozen=True, slots=True)
class ArchiveBillingAccount(_Command):
    account_id: str = ""


@dataclass(frozen=True, slots=True)
class RestoreBillingAccount(_Command):
    """Un-file an archived account. It returns to `closed`, not to `active` — reopening a
    commercial relationship is a separate decision."""

    account_id: str = ""


@dataclass(frozen=True, slots=True)
class DeleteDraftAccount(_Command):
    """The only deletion this domain offers, and only from `draft`: everything past it has
    revenue attached, and revenue outlives the customer record (ADR-0023 RS4)."""

    account_id: str = ""


@dataclass(frozen=True, slots=True)
class UpdateBillingPreferences(_Command):
    account_id: str = ""
    preferences: BillingPreferences = field(default_factory=BillingPreferences)


@dataclass(frozen=True, slots=True)
class UpdateContacts(_Command):
    account_id: str = ""
    contacts: tuple[AccountContact, ...] = ()


@dataclass(frozen=True, slots=True)
class UpdateAddresses(_Command):
    account_id: str = ""
    addresses: tuple[AccountAddress, ...] = ()


@dataclass(frozen=True, slots=True)
class UpdateCreditProfile(_Command):
    account_id: str = ""
    credit: CreditProfile = field(default_factory=CreditProfile)
    commercial_status: CommercialStatus | None = None


@dataclass(frozen=True, slots=True)
class AssignParent(_Command):
    """A *billing* relationship. Nothing about organization, workspace, or project hierarchy
    changes — which is what lets a reseller structure be configured rather than architected."""

    account_id: str = ""
    parent_account_id: str = ""  # "" ⇒ detach to the root


@dataclass(frozen=True, slots=True)
class AssignPayer(_Command):
    account_id: str = ""
    payer_account_id: str = ""  # "" ⇒ the account pays for itself


@dataclass(frozen=True, slots=True)
class AddAccountNote(_Command):
    account_id: str = ""
    body: str = ""
    pinned: bool = False


ALL_ACCOUNT_COMMANDS = (
    CreateBillingAccount,
    ActivateBillingAccount,
    SuspendBillingAccount,
    CloseCommercialAccount,
    ArchiveBillingAccount,
    RestoreBillingAccount,
    DeleteDraftAccount,
    UpdateBillingPreferences,
    UpdateContacts,
    UpdateAddresses,
    UpdateCreditProfile,
    AssignParent,
    AssignPayer,
    AddAccountNote,
)


# ── queries ─────────────────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class GetBillingAccount(_Query):
    account_id: str = ""
    account_number: str = ""  # either identifier resolves


@dataclass(frozen=True, slots=True)
class SearchBillingAccounts(_Query):
    query: AccountSearchQuery = field(default_factory=AccountSearchQuery)


@dataclass(frozen=True, slots=True)
class ListHierarchy(_Query):
    """The billing tree this account belongs to, from its root down."""

    account_id: str = ""


@dataclass(frozen=True, slots=True)
class AccountHistory(_Query):
    account_id: str = ""  # "" ⇒ the whole organization
    limit: int = 100


@dataclass(frozen=True, slots=True)
class CommercialProfile(_Query):
    account_id: str = ""


ALL_ACCOUNT_QUERIES = (
    GetBillingAccount,
    SearchBillingAccounts,
    ListHierarchy,
    AccountHistory,
    CommercialProfile,
)


# ── view DTOs ───────────────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class LegalEntityView:
    legal_name: str
    kind: str
    registration_number: str
    tax_identifier: str
    incorporation_country: str


@dataclass(frozen=True, slots=True)
class ContactView:
    role: str
    name: str
    email: str
    phone: str
    title: str
    is_primary: bool


@dataclass(frozen=True, slots=True)
class AddressView:
    kind: str
    line1: str
    line2: str
    city: str
    region: str
    postal_code: str
    country: str
    label: str
    is_primary: bool
    is_complete: bool


@dataclass(frozen=True, slots=True)
class PreferencesView:
    invoice_delivery: str
    invoice_language: str
    invoice_prefix: str
    purchase_order_required: bool
    purchase_order_number: str
    consolidated_invoicing: bool
    billing_day: int
    payment_terms_days: int
    statement_frequency: str
    timezone: str


@dataclass(frozen=True, slots=True)
class CreditView:
    credit_limit: MoneyView | None
    credit_hold: bool
    risk_rating: str
    payment_terms_days: int
    dunning_exempt: bool
    assessed_at: float
    assessed_by: str
    has_limit: bool
    notes: str


@dataclass(frozen=True, slots=True)
class NoteView:
    id: str
    body: str
    author_id: str
    at: float
    pinned: bool


@dataclass(frozen=True, slots=True)
class BillingAccountView:
    id: str
    account_number: str
    display_name: str
    currency: str
    kind: str
    state: str
    commercial_status: str
    legal_entity: LegalEntityView
    parent_account_id: str
    payer_account_id: str
    contacts: tuple[ContactView, ...]
    addresses: tuple[AddressView, ...]
    preferences: PreferencesView
    credit: CreditView
    tax_profile_id: str
    notes: tuple[NoteView, ...]
    is_active: bool
    is_child: bool
    is_billable: bool
    is_on_hold: bool
    is_retained_only: bool
    allowed_triggers: tuple[str, ...]
    created_at: float
    updated_at: float
    activated_at: float | None
    closed_at: float | None
    close_reason: str


@dataclass(frozen=True, slots=True)
class HierarchyNodeView:
    account_id: str
    account_number: str
    display_name: str
    state: str
    kind: str
    depth: int
    payer_account_id: str
    children: tuple[HierarchyNodeView, ...]


@dataclass(frozen=True, slots=True)
class HierarchyView:
    root: HierarchyNodeView
    invoiced_account_id: str
    account_ids: tuple[str, ...]
    depth: int
    size: int


@dataclass(frozen=True, slots=True)
class AccountActivityView:
    id: str
    account_id: str
    action: str
    actor_id: str
    at: float
    from_state: str
    to_state: str
    detail: str


@dataclass(frozen=True, slots=True)
class CommercialProfileResultView:
    account: BillingAccountView
    parent_account_id: str
    parent_display_name: str
    payer_account_id: str
    payer_display_name: str
    child_count: int
    descendant_count: int
    hierarchy_depth: int
    payer_chain: tuple[str, ...]
    invoiced_account_id: str
    is_self_paying: bool
    is_root: bool


@dataclass(frozen=True, slots=True)
class AccountSearchView:
    accounts: tuple[BillingAccountView, ...]
    total: int


def _money_view(amount: Money | None) -> MoneyView | None:
    return None if amount is None else MoneyView(minor=amount.minor, currency=amount.currency.value)


def _legal_entity_view(entity: LegalEntity) -> LegalEntityView:
    return LegalEntityView(
        legal_name=entity.legal_name,
        kind=entity.kind.value,
        registration_number=entity.registration_number,
        tax_identifier=entity.tax_identifier,
        incorporation_country=entity.incorporation_country,
    )


def _contact_view(contact: AccountContact) -> ContactView:
    return ContactView(
        role=contact.role.value,
        name=contact.contact.name,
        email=contact.contact.email,
        phone=contact.contact.phone,
        title=contact.title,
        is_primary=contact.is_primary,
    )


def _address_view(entry: AccountAddress) -> AddressView:
    return AddressView(
        kind=entry.kind.value,
        line1=entry.address.line1,
        line2=entry.address.line2,
        city=entry.address.city,
        region=entry.address.region,
        postal_code=entry.address.postal_code,
        country=entry.address.country,
        label=entry.label,
        is_primary=entry.is_primary,
        is_complete=entry.is_complete,
    )


def _preferences_view(preferences: BillingPreferences) -> PreferencesView:
    return PreferencesView(
        invoice_delivery=preferences.invoice_delivery.value,
        invoice_language=preferences.invoice_language,
        invoice_prefix=preferences.invoice_prefix,
        purchase_order_required=preferences.purchase_order_required,
        purchase_order_number=preferences.purchase_order_number,
        consolidated_invoicing=preferences.consolidated_invoicing,
        billing_day=preferences.billing_day,
        payment_terms_days=preferences.payment_terms_days,
        statement_frequency=preferences.statement_frequency.value,
        timezone=preferences.timezone,
    )


def _credit_view(credit: CreditProfile) -> CreditView:
    return CreditView(
        credit_limit=_money_view(credit.credit_limit),
        credit_hold=credit.credit_hold,
        risk_rating=credit.risk_rating.value,
        payment_terms_days=credit.payment_terms_days,
        dunning_exempt=credit.dunning_exempt,
        assessed_at=credit.assessed_at,
        assessed_by=credit.assessed_by,
        has_limit=credit.has_limit,
        notes=credit.notes,
    )


def account_view(
    account: CommercialAccount, allowed: tuple[AccountTrigger, ...] = ()
) -> BillingAccountView:
    return BillingAccountView(
        id=account.id,
        account_number=account.account_number,
        display_name=account.display_name,
        currency=account.currency.value,
        kind=account.kind.value,
        state=account.state.value,
        commercial_status=account.commercial_status.value,
        legal_entity=_legal_entity_view(account.legal_entity),
        parent_account_id=account.parent_account_id,
        payer_account_id=account.payer_account_id,
        contacts=tuple(_contact_view(c) for c in account.contacts),
        addresses=tuple(_address_view(a) for a in account.addresses.entries),
        preferences=_preferences_view(account.preferences),
        credit=_credit_view(account.credit),
        tax_profile_id=account.tax_profile_id,
        notes=tuple(
            NoteView(id=n.id, body=n.body, author_id=n.author_id, at=n.at, pinned=n.pinned)
            for n in account.notes
        ),
        is_active=account.is_active,
        is_child=account.is_child,
        is_billable=account.is_billable,
        is_on_hold=account.is_on_hold,
        is_retained_only=account.is_retained_only,
        allowed_triggers=tuple(t.value for t in allowed),
        created_at=account.created_at,
        updated_at=account.updated_at,
        activated_at=account.activated_at,
        closed_at=account.closed_at,
        close_reason=account.close_reason,
    )


def _node_view(node: AccountNode) -> HierarchyNodeView:
    return HierarchyNodeView(
        account_id=node.account.id,
        account_number=node.account.account_number,
        display_name=node.account.display_name,
        state=node.account.state.value,
        kind=node.account.kind.value,
        depth=node.depth,
        payer_account_id=node.account.payer_account_id,
        children=tuple(_node_view(child) for child in node.children),
    )


def hierarchy_view(tree) -> HierarchyView:
    return HierarchyView(
        root=_node_view(tree.root),
        invoiced_account_id=tree.invoiced_account_id,
        account_ids=tree.account_ids,
        depth=tree.depth,
        size=tree.size,
    )


def activity_view(activity) -> AccountActivityView:
    return AccountActivityView(
        id=activity.id,
        account_id=activity.account_id,
        action=activity.action.value,
        actor_id=activity.actor_id,
        at=activity.at,
        from_state=activity.from_state,
        to_state=activity.to_state,
        detail=activity.detail,
    )


def commercial_profile_view(
    profile: CommercialProfileView, allowed: tuple[AccountTrigger, ...] = ()
) -> CommercialProfileResultView:
    return CommercialProfileResultView(
        account=account_view(profile.account, allowed),
        parent_account_id=profile.parent.id if profile.parent else "",
        parent_display_name=profile.parent.display_name if profile.parent else "",
        payer_account_id=profile.payer.id if profile.payer else "",
        payer_display_name=profile.payer.display_name if profile.payer else "",
        child_count=profile.child_count,
        descendant_count=profile.descendant_count,
        hierarchy_depth=profile.hierarchy_depth,
        payer_chain=profile.payer_chain,
        invoiced_account_id=profile.invoiced_account_id,
        is_self_paying=profile.is_self_paying,
        is_root=profile.is_root,
    )


# ── use cases ───────────────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class AccountRepositories:
    """The billing-account seams, injected. Interfaces only — implementations in persistence."""

    accounts: object
    activity: object
    numbers: object


class AccountCommandUseCases:
    """Orchestration for billing accounts. Coordinates; decides nothing."""

    def __init__(
        self,
        *,
        context,
        account_repos: AccountRepositories,
        factory,
        lifecycle,
        hierarchy,
        authz: AuthorizationOrchestrator,
        uow_factory: UnitOfWorkFactory,
        ids,
        clock,
    ) -> None:
        self._ctx = context
        self._repos = account_repos
        self._factory = factory
        self._lifecycle = lifecycle
        self._hierarchy = hierarchy
        self._authz = authz
        self._uow = uow_factory
        self._ids = ids
        self._clock = clock

    # ── creation ────────────────────────────────────────────────────────────────

    def create_account(self, command: CreateBillingAccount) -> CommercialAccount:
        self._authz.require(command.actor_id, command.org_id, P.BILLING_MANAGE)
        with self._uow.new() as uow:
            account = self._factory.create_account(
                org_id=command.org_id,
                display_name=command.display_name,
                currency=command.currency,
                account_number=command.account_number,
                kind=command.kind,
                legal_entity=command.legal_entity,
                parent_account_id=command.parent_account_id,
                payer_account_id=command.payer_account_id,
                contacts=command.contacts,
                addresses=command.addresses,
                preferences=command.preferences,
                credit=command.credit,
                tax_profile_id=command.tax_profile_id,
                known=self._repos.accounts.all(command.org_id),
                actor_id=command.actor_id,
            )
            stored = self._repos.accounts.add(account)
            self._record(command, stored.id, AccountAction.CREATED, to_state=stored.state.value)
            uow.stage(self._event(command, stored.id, account_number=stored.account_number))
            return stored

    # ── lifecycle ───────────────────────────────────────────────────────────────

    def activate(self, command: ActivateBillingAccount) -> CommercialAccount:
        """Which trigger "activate" means from here is the Foundation's decision — a draft is
        activated, a suspended account is resumed, and the history records which."""
        self._authz.require(command.actor_id, command.org_id, P.BILLING_MANAGE)
        with self._uow.new() as uow:
            account = self._account(command.org_id, command.account_id)
            trigger = self._lifecycle.activation_trigger(account)
            action = (
                AccountAction.RESUMED
                if trigger is AccountTrigger.RESUME
                else AccountAction.ACTIVATED
            )
            return self._transition(command, account, trigger, action, uow)

    def suspend(self, command: SuspendBillingAccount) -> CommercialAccount:
        self._authz.require(command.actor_id, command.org_id, P.BILLING_MANAGE)
        with self._uow.new() as uow:
            account = self._account(command.org_id, command.account_id)
            return self._transition(
                command,
                account,
                AccountTrigger.SUSPEND,
                AccountAction.SUSPENDED,
                uow,
                detail=command.reason,
            )

    def close(self, command: CloseCommercialAccount) -> CommercialAccount:
        """Closing ends commercial activity and never removes anything (ADR-0023 RS4)."""
        self._authz.require(command.actor_id, command.org_id, P.BILLING_MANAGE)
        with self._uow.new() as uow:
            account = self._account(command.org_id, command.account_id)
            return self._transition(
                command,
                account,
                AccountTrigger.CLOSE,
                AccountAction.CLOSED,
                uow,
                reason=command.reason,
                detail=command.reason,
            )

    def archive(self, command: ArchiveBillingAccount) -> CommercialAccount:
        self._authz.require(command.actor_id, command.org_id, P.BILLING_MANAGE)
        with self._uow.new() as uow:
            account = self._account(command.org_id, command.account_id)
            return self._transition(
                command, account, AccountTrigger.ARCHIVE, AccountAction.ARCHIVED, uow
            )

    def restore(self, command: RestoreBillingAccount) -> CommercialAccount:
        self._authz.require(command.actor_id, command.org_id, P.BILLING_MANAGE)
        with self._uow.new() as uow:
            account = self._account(command.org_id, command.account_id)
            return self._transition(
                command, account, AccountTrigger.RESTORE, AccountAction.RESTORED, uow
            )

    def delete_draft(self, command: DeleteDraftAccount) -> CommercialAccount:
        """The FSM permits this only from `draft`; from anywhere else it is rejected, because
        everything past draft has revenue attached."""
        self._authz.require(command.actor_id, command.org_id, P.BILLING_MANAGE)
        with self._uow.new() as uow:
            account = self._account(command.org_id, command.account_id)
            return self._transition(
                command, account, AccountTrigger.DELETE, AccountAction.DELETED, uow
            )

    # ── commercial detail ───────────────────────────────────────────────────────

    def update_preferences(self, command: UpdateBillingPreferences) -> CommercialAccount:
        return self._amend(
            command,
            AccountAction.PREFERENCES_UPDATED,
            lambda account: self._factory.update_preferences(
                account, command.preferences, actor_id=command.actor_id
            ),
        )

    def update_contacts(self, command: UpdateContacts) -> CommercialAccount:
        return self._amend(
            command,
            AccountAction.CONTACTS_UPDATED,
            lambda account: self._factory.update_contacts(
                account, command.contacts, actor_id=command.actor_id
            ),
        )

    def update_addresses(self, command: UpdateAddresses) -> CommercialAccount:
        return self._amend(
            command,
            AccountAction.ADDRESSES_UPDATED,
            lambda account: self._factory.update_addresses(
                account, command.addresses, actor_id=command.actor_id
            ),
        )

    def update_credit(self, command: UpdateCreditProfile) -> CommercialAccount:
        return self._amend(
            command,
            AccountAction.CREDIT_UPDATED,
            lambda account: self._factory.update_credit(
                account,
                command.credit,
                commercial_status=command.commercial_status,
                actor_id=command.actor_id,
            ),
        )

    def add_note(self, command: AddAccountNote) -> CommercialAccount:
        return self._amend(
            command,
            AccountAction.NOTE_ADDED,
            lambda account: self._factory.add_note(
                account, body=command.body, pinned=command.pinned, actor_id=command.actor_id
            ),
            guard=False,  # a note may be added to a closed account: it is a record *about* it
        )

    # ── relationships ───────────────────────────────────────────────────────────

    def assign_parent(self, command: AssignParent) -> CommercialAccount:
        self._authz.require(command.actor_id, command.org_id, P.BILLING_MANAGE)
        with self._uow.new() as uow:
            account = self._account(command.org_id, command.account_id)
            self._lifecycle.guard_mutable(account)
            moved = self._factory.assign_parent(
                account,
                parent_account_id=command.parent_account_id,
                known=self._repos.accounts.all(command.org_id),
                actor_id=command.actor_id,
            )
            stored = self._repos.accounts.replace(moved)
            self._record(
                command,
                stored.id,
                AccountAction.PARENT_ASSIGNED,
                detail=command.parent_account_id or "detached to root",
            )
            uow.stage(self._event(command, stored.id, parent=command.parent_account_id))
            return stored

    def assign_payer(self, command: AssignPayer) -> CommercialAccount:
        self._authz.require(command.actor_id, command.org_id, P.BILLING_MANAGE)
        with self._uow.new() as uow:
            account = self._account(command.org_id, command.account_id)
            self._lifecycle.guard_mutable(account)
            moved = self._factory.assign_payer(
                account,
                payer_account_id=command.payer_account_id,
                known=self._repos.accounts.all(command.org_id),
                actor_id=command.actor_id,
            )
            stored = self._repos.accounts.replace(moved)
            self._record(
                command,
                stored.id,
                AccountAction.PAYER_ASSIGNED,
                detail=command.payer_account_id or "pays for itself",
            )
            uow.stage(self._event(command, stored.id, payer=command.payer_account_id))
            return stored

    # ── internals ───────────────────────────────────────────────────────────────

    def _transition(
        self,
        command,
        account: CommercialAccount,
        trigger: AccountTrigger,
        action: AccountAction,
        uow,
        *,
        reason: str = "",
        detail: str = "",
    ) -> CommercialAccount:
        # Legality is the Foundation's FSM decision, not this method's.
        moved = self._lifecycle.apply(account, trigger, at=self._clock.now(), reason=reason)
        stored = self._repos.accounts.replace(moved)
        self._record(
            command,
            stored.id,
            action,
            from_state=account.state.value,
            to_state=stored.state.value,
            detail=detail,
        )
        self._factory.emit_transition(
            stored,
            event_type=_TRANSITION_EVENTS[action],
            actor_id=command.actor_id,
            state=stored.state.value,
        )
        uow.stage(self._event(command, stored.id, state=stored.state.value))
        return stored

    def _amend(self, command, action: AccountAction, apply, *, guard: bool = True):
        self._authz.require(command.actor_id, command.org_id, P.BILLING_MANAGE)
        with self._uow.new() as uow:
            account = self._account(command.org_id, command.account_id)
            if guard:
                self._lifecycle.guard_mutable(account)
            stored = self._repos.accounts.replace(apply(account))
            self._record(command, stored.id, action)
            uow.stage(self._event(command, stored.id))
            return stored

    def _account(self, org_id: str, account_id: str) -> CommercialAccount:
        account = self._repos.accounts.get(org_id, account_id) or self._repos.accounts.by_number(
            org_id, account_id
        )
        if account is None:
            raise _NotFound(f"billing account {account_id!r} not found")
        return account

    def _record(
        self,
        command,
        account_id: str,
        action: AccountAction,
        *,
        from_state: str = "",
        to_state: str = "",
        detail: str = "",
    ) -> None:
        """Append account history **inside the caller's transaction**, so the history and the
        change it describes commit or vanish together."""
        self._repos.activity.append(
            self._factory.activity(
                org_id=command.org_id,
                account_id=account_id,
                action=action,
                actor_id=command.actor_id,
                from_state=from_state,
                to_state=to_state,
                detail=detail,
            )
        )

    def _event(self, command, subject_id: str, **data) -> ApplicationEvent:
        return ApplicationEvent(
            id=self._ids.new(PREFIX_APP_EVENT),
            type=ApplicationEventType.ACCOUNT_PROVISIONED,
            ts=self._clock.now(),
            use_case=type(command).__name__,
            outcome="completed",
            org_id=command.org_id,
            actor=command.actor_id,
            subject_id=subject_id,
            data=dict(data),
        )


def _transition_events() -> dict:
    from billing.foundation import BillingEventType as E

    return {
        AccountAction.ACTIVATED: E.ACCOUNT_ACTIVATED,
        AccountAction.RESUMED: E.ACCOUNT_RESUMED,
        AccountAction.SUSPENDED: E.ACCOUNT_SUSPENDED,
        AccountAction.CLOSED: E.ACCOUNT_CLOSED,
        # A draft deletion is not a revenue fact — nothing was ever billed under it — so it
        # reuses the closure fact rather than inventing one nobody consumes.
        AccountAction.DELETED: E.ACCOUNT_CLOSED,
        AccountAction.ARCHIVED: E.ACCOUNT_ARCHIVED,
        AccountAction.RESTORED: E.ACCOUNT_RESTORED,
    }


_TRANSITION_EVENTS = _transition_events()


class AccountQueryUseCases:
    """Read-only. No transaction, no audit, no mutation."""

    def __init__(
        self, *, context, account_repos: AccountRepositories, search, hierarchy, profiles, authz
    ) -> None:
        self._ctx = context
        self._repos = account_repos
        self._search = search
        self._hierarchy = hierarchy
        self._profiles = profiles
        self._authz = authz
        from billing.foundation import AccountLifecycleService

        self._lifecycle = AccountLifecycleService()

    def get_account(self, query: GetBillingAccount) -> BillingAccountView | None:
        self._authz.require(query.actor_id, query.org_id, P.BILLING_READ)
        account = None
        if query.account_id:
            account = self._repos.accounts.get(query.org_id, query.account_id)
        if account is None and query.account_number:
            account = self._repos.accounts.by_number(query.org_id, query.account_number)
        if account is None and query.account_id:
            account = self._repos.accounts.by_number(query.org_id, query.account_id)
        if account is None:
            return None
        return account_view(account, self._lifecycle.allowed(account.state))

    def search(self, query: SearchBillingAccounts) -> AccountSearchView:
        self._authz.require(query.actor_id, query.org_id, P.BILLING_READ)
        result = self._search.search(self._repos.accounts.all(query.org_id), query.query)
        return AccountSearchView(
            accounts=tuple(
                account_view(a, self._lifecycle.allowed(a.state)) for a in result.accounts
            ),
            total=result.total,
        )

    def hierarchy(self, query: ListHierarchy) -> HierarchyView | None:
        self._authz.require(query.actor_id, query.org_id, P.BILLING_READ)
        account = self._resolve(query.org_id, query.account_id)
        if account is None:
            return None
        known = self._repos.accounts.all(query.org_id)
        return hierarchy_view(self._hierarchy.billing_tree(account, known))

    def history(self, query: AccountHistory) -> tuple[AccountActivityView, ...]:
        self._authz.require(query.actor_id, query.org_id, P.BILLING_READ)
        account_id = query.account_id
        if account_id:
            account = self._resolve(query.org_id, account_id)
            if account is None:
                return ()
            account_id = account.id
        return tuple(
            activity_view(a)
            for a in self._repos.activity.history(query.org_id, account_id, query.limit)
        )

    def profile(self, query: CommercialProfile) -> CommercialProfileResultView | None:
        self._authz.require(query.actor_id, query.org_id, P.BILLING_READ)
        account = self._resolve(query.org_id, query.account_id)
        if account is None:
            return None
        known = self._repos.accounts.all(query.org_id)
        return commercial_profile_view(
            self._profiles.compose(account, known), self._lifecycle.allowed(account.state)
        )

    def _resolve(self, org_id: str, identifier: str) -> CommercialAccount | None:
        return self._repos.accounts.get(org_id, identifier) or self._repos.accounts.by_number(
            org_id, identifier
        )


# ── wiring table ────────────────────────────────────────────────────────────────


def register_account_command_handlers(bus, uc: AccountCommandUseCases) -> None:
    bus.register(CreateBillingAccount, uc.create_account)
    bus.register(ActivateBillingAccount, uc.activate)
    bus.register(SuspendBillingAccount, uc.suspend)
    bus.register(CloseCommercialAccount, uc.close)
    bus.register(ArchiveBillingAccount, uc.archive)
    bus.register(RestoreBillingAccount, uc.restore)
    bus.register(DeleteDraftAccount, uc.delete_draft)
    bus.register(UpdateBillingPreferences, uc.update_preferences)
    bus.register(UpdateContacts, uc.update_contacts)
    bus.register(UpdateAddresses, uc.update_addresses)
    bus.register(UpdateCreditProfile, uc.update_credit)
    bus.register(AssignParent, uc.assign_parent)
    bus.register(AssignPayer, uc.assign_payer)
    bus.register(AddAccountNote, uc.add_note)


def register_account_query_handlers(bus, uc: AccountQueryUseCases) -> None:
    bus.register(GetBillingAccount, uc.get_account)
    bus.register(SearchBillingAccounts, uc.search)
    bus.register(ListHierarchy, uc.hierarchy)
    bus.register(AccountHistory, uc.history)
    bus.register(CommercialProfile, uc.profile)
