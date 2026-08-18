"""Billing Account rules (M9.8) — validation, hierarchy, payers, lifecycle, search, profile.

Every account *decision* lives here: what makes a commercial record valid, which hierarchies
are legal, how a payer chain resolves, which lifecycle moves are permitted, and how a
commercial profile is composed. Layers above coordinate these; they never re-decide.

Validation follows the Foundation discipline exactly — it **returns diagnostics and never
raises**, reports *every* finding rather than the first, and orders them deterministically, so
an account screen can show a complete checklist instead of a whack-a-mole sequence.

Two structural rules are worth stating before the code:

* **Hierarchy and payment are resolved independently.** `AccountHierarchyResolver` walks
  `parent_account_id`; `PayerResolver` walks `payer_account_id`. Each detects its own cycles,
  and a cycle in one is not a cycle in the other — a subsidiary can pay for its parent without
  either graph being malformed.
* **Nothing here reads Epic 8.** `org_id` is a string this module scopes on and never
  dereferences. A reseller hierarchy therefore requires no change to organization tenancy,
  which is exactly the property ADR-0023 §5.2 exists to protect.

Pure: no I/O, no clock read, no persistence.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import replace

from core.fsm import StateMachineDefinition

from .commercial_accounts import (
    ACCOUNT_LIFECYCLE,
    AccountAddress,
    AccountContact,
    AccountNode,
    AccountSearchQuery,
    AccountSearchResult,
    AccountState,
    AccountTrigger,
    AddressKind,
    BillingPreferences,
    BillingTree,
    CommercialAccount,
    CommercialProfileView,
    CommercialStatus,
    ContactRole,
    CreditProfile,
    LegalEntity,
)
from .diagnostics import BillingDiagnosticCode as C
from .diagnostics import BillingDiagnostics, Diagnostic, Severity
from .errors import BillingConflict, InvalidBillingTransition

#: How deep a billing hierarchy may go. Distributor → reseller → sub-reseller → customer is
#: four levels and is a real arrangement; beyond that the structure is almost always a
#: modelling mistake, and an unbounded tree makes every traversal a latent outage.
MAX_HIERARCHY_DEPTH = 6


def _err(path: str, code: C, message: str, remediation: str = "") -> Diagnostic:
    return Diagnostic(path, code, Severity.ERROR, message, remediation)


def _warn(path: str, code: C, message: str, remediation: str = "") -> Diagnostic:
    return Diagnostic(path, code, Severity.WARNING, message, remediation)


def _duplicates(values: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    dupes: set[str] = set()
    for value in values:
        if value in seen:
            dupes.add(value)
        seen.add(value)
    return sorted(dupes)


# ── hierarchy ───────────────────────────────────────────────────────────────────


class AccountHierarchyResolver:
    """Walks the parent graph: ancestors, descendants, trees, and cycles. Stateless.

    The billing hierarchy is its own graph, entirely separate from organization, workspace,
    and project hierarchy. Nothing in this class reads an org, a workspace, or a project."""

    def find_cycle(
        self, account: CommercialAccount, known: Sequence[CommercialAccount]
    ) -> tuple[str, ...]:
        """The cycle through `account`, as a path, or `()` if there is none.

        Returning the *path* rather than a boolean is deliberate: "the hierarchy has a cycle"
        is unactionable; "a → b → c → a" names the edge to cut."""
        if account.parent_account_id == account.id:
            return (account.id, account.id)
        by_id = {a.id: a for a in known}
        by_id.setdefault(account.id, account)
        path: list[str] = []
        seen: set[str] = set()
        current: CommercialAccount | None = account
        while current is not None:
            if current.id in seen:
                start = path.index(current.id)
                return tuple(path[start:]) + (current.id,)
            seen.add(current.id)
            path.append(current.id)
            if not current.parent_account_id:
                return ()
            current = by_id.get(current.parent_account_id)
        return ()

    def ancestors(
        self, account: CommercialAccount, known: Sequence[CommercialAccount]
    ) -> tuple[CommercialAccount, ...]:
        """Nearest parent first. Stops at a cycle rather than looping, because reaching one
        here means a malformed hierarchy was stored and the traversal must still terminate."""
        by_id = {a.id: a for a in known}
        out: list[CommercialAccount] = []
        seen = {account.id}
        current = by_id.get(account.parent_account_id) if account.parent_account_id else None
        while current is not None and current.id not in seen:
            out.append(current)
            seen.add(current.id)
            current = by_id.get(current.parent_account_id) if current.parent_account_id else None
        return tuple(out)

    def root(
        self, account: CommercialAccount, known: Sequence[CommercialAccount]
    ) -> CommercialAccount:
        chain = self.ancestors(account, known)
        return chain[-1] if chain else account

    def children(
        self, account: CommercialAccount, known: Sequence[CommercialAccount]
    ) -> tuple[CommercialAccount, ...]:
        return tuple(
            sorted(
                (a for a in known if a.parent_account_id == account.id),
                key=lambda a: a.account_number,
            )
        )

    def descendants(
        self, account: CommercialAccount, known: Sequence[CommercialAccount]
    ) -> tuple[CommercialAccount, ...]:
        out: list[CommercialAccount] = []
        for child in self.children(account, known):
            out.append(child)
            out.extend(self.descendants(child, known))
        return tuple(out)

    def depth(self, account: CommercialAccount, known: Sequence[CommercialAccount]) -> int:
        return len(self.ancestors(account, known))

    def tree(
        self,
        account: CommercialAccount,
        known: Sequence[CommercialAccount],
        *,
        depth: int = 0,
        _guard: frozenset[str] = frozenset(),
    ) -> AccountNode:
        """The resolved subtree rooted at `account`.

        `_guard` makes the recursion terminate on a malformed (cyclic) hierarchy rather than
        overflowing the stack: validation rejects cycles, but a tree renderer must not be the
        thing that discovers one the hard way."""
        if account.id in _guard or depth >= MAX_HIERARCHY_DEPTH:
            return AccountNode(account=account, depth=depth, children=())
        guard = _guard | {account.id}
        return AccountNode(
            account=account,
            depth=depth,
            children=tuple(
                self.tree(child, known, depth=depth + 1, _guard=guard)
                for child in self.children(account, known)
            ),
        )

    def billing_tree(
        self, account: CommercialAccount, known: Sequence[CommercialAccount]
    ) -> BillingTree:
        """The whole hierarchy this account belongs to, from its root down, plus the account
        the group is invoiced at. Attribution flows down; invoicing rolls up."""
        root = self.root(account, known)
        return BillingTree(root=self.tree(root, known), invoiced_account_id=root.id)

    def expand(
        self, account: CommercialAccount, known: Sequence[CommercialAccount]
    ) -> tuple[str, ...]:
        """Every account id in this account's subtree, itself first.

        Raises `BillingConflict` on a cycle rather than looping: expansion is called after
        validation, so reaching a cycle here means a malformed hierarchy was persisted — a
        condition that must be loud."""
        if self.find_cycle(account, known):
            raise BillingConflict(f"billing hierarchy through {account.id!r} forms a cycle")
        return (account.id, *(a.id for a in self.descendants(account, known)))


class PayerResolver:
    """Walks the payer graph — deliberately a different graph from the hierarchy.

    "Who consolidates this account's invoices" (the parent) and "who actually pays" (the
    payer) are different questions in every reseller and enterprise arrangement, and a model
    that collapses them breaks the first time a customer pays for a subsidiary it does not
    own."""

    def find_loop(
        self, account: CommercialAccount, known: Sequence[CommercialAccount]
    ) -> tuple[str, ...]:
        if account.payer_account_id == account.id:
            return (account.id, account.id)
        by_id = {a.id: a for a in known}
        by_id.setdefault(account.id, account)
        path: list[str] = []
        seen: set[str] = set()
        current: CommercialAccount | None = account
        while current is not None:
            if current.id in seen:
                start = path.index(current.id)
                return tuple(path[start:]) + (current.id,)
            seen.add(current.id)
            path.append(current.id)
            if not current.payer_account_id:
                return ()
            current = by_id.get(current.payer_account_id)
        return ()

    def chain(
        self, account: CommercialAccount, known: Sequence[CommercialAccount]
    ) -> tuple[str, ...]:
        """The payer chain from this account to the ultimate payer, inclusive."""
        by_id = {a.id: a for a in known}
        out = [account.id]
        seen = {account.id}
        current = account
        while current.payer_account_id and current.payer_account_id not in seen:
            nxt = by_id.get(current.payer_account_id)
            if nxt is None:
                out.append(current.payer_account_id)
                break
            out.append(nxt.id)
            seen.add(nxt.id)
            current = nxt
        return tuple(out)

    def ultimate_payer(
        self, account: CommercialAccount, known: Sequence[CommercialAccount]
    ) -> CommercialAccount:
        """The account at the end of the payer chain. An account with no payer pays for
        itself, which is why this always returns an account rather than an optional."""
        if self.find_loop(account, known):
            raise BillingConflict(f"payer chain through {account.id!r} forms a loop")
        by_id = {a.id: a for a in known}
        current = account
        while current.payer_account_id:
            nxt = by_id.get(current.payer_account_id)
            if nxt is None:
                return current
            current = nxt
        return current


# ── validation ──────────────────────────────────────────────────────────────────

_NUMBER_ALLOWED = set("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_")


class AccountValidator:
    """Structural, referential, and relational validation for billing accounts. Never
    raises."""

    def __init__(self, hierarchy: AccountHierarchyResolver | None = None) -> None:
        self._hierarchy = hierarchy or AccountHierarchyResolver()
        self._payers = PayerResolver()

    # ── one account ─────────────────────────────────────────────────────────────

    def validate_account(
        self, account: CommercialAccount, *, known: Sequence[CommercialAccount] = ()
    ) -> BillingDiagnostics:
        out: list[Diagnostic] = []
        if not account.org_id:
            out.append(_err("account.org_id", C.EMPTY_IDENTITY, "org_id is required"))
        if not account.display_name:
            out.append(_err("account.display_name", C.INVALID_NAME, "a display name is required"))
        self._check_number(out, account.account_number)
        out.extend(self.validate_legal_entity(account.legal_entity).findings)
        out.extend(self.validate_contacts(account.contacts).findings)
        out.extend(self.validate_addresses(account.addresses.entries).findings)
        out.extend(self.validate_preferences(account.preferences).findings)
        out.extend(self.validate_credit(account.credit, currency=account.currency).findings)
        out.extend(self._commercial_status(account))

        # An account that has gone live must be invoiceable: a billing address is tax
        # evidence input and a billing contact is who receives the document.
        if account.state in (AccountState.ACTIVE, AccountState.SUSPENDED):
            if account.addresses.primary(AddressKind.BILLING) is None:
                out.append(
                    _err(
                        "account.addresses",
                        C.MISSING_BILLING_ADDRESS,
                        "an active account needs a primary billing address",
                        "it is the address a tax determination and an invoice are made against",
                    )
                )
            if account.contact_for(ContactRole.BILLING) is None:
                out.append(
                    _warn(
                        "account.contacts",
                        C.INVALID_CONTACT,
                        "an active account should name a billing contact",
                        "otherwise nobody receives the invoice",
                    )
                )
            if not account.legal_entity.is_identified:
                out.append(
                    _warn(
                        "account.legal_entity",
                        C.INVALID_LEGAL_ENTITY,
                        "an active account should name the legal entity it contracts with",
                    )
                )

        if account.parent_account_id == account.id:
            out.append(
                _err(
                    "account.parent_account_id",
                    C.ACCOUNT_CYCLE,
                    "an account cannot be its own parent",
                )
            )
        if account.payer_account_id == account.id:
            out.append(
                _err(
                    "account.payer_account_id",
                    C.PAYER_LOOP,
                    "an account cannot be its own payer",
                    'leave the payer empty — "" already means "pays for itself"',
                )
            )
        if known:
            out.extend(self.validate_hierarchy_edge(account, known).findings)
            out.extend(self.validate_payer_edge(account, known).findings)
        return BillingDiagnostics.of(out)

    def _check_number(self, out: list[Diagnostic], number: str) -> None:
        if not number:
            out.append(
                _err("account.account_number", C.EMPTY_IDENTITY, "an account number is required")
            )
            return
        if set(number) - _NUMBER_ALLOWED:
            out.append(
                _err(
                    "account.account_number",
                    C.INVALID_CODE,
                    f"account number {number!r} may contain only letters, digits, '-' and '_'",
                    "it appears on invoices and in the customer's own AP system — keep it typeable",
                )
            )

    def validate_legal_entity(self, entity: LegalEntity) -> BillingDiagnostics:
        out: list[Diagnostic] = []
        if entity.incorporation_country and len(entity.incorporation_country) != 2:
            out.append(
                _err(
                    "legal_entity.incorporation_country",
                    C.INVALID_LEGAL_ENTITY,
                    f"{entity.incorporation_country!r} is not an ISO-3166 alpha-2 country code",
                )
            )
        if entity.registration_number and not entity.legal_name:
            out.append(
                _err(
                    "legal_entity.legal_name",
                    C.INVALID_LEGAL_ENTITY,
                    "a registration number without a legal name identifies nobody",
                )
            )
        return BillingDiagnostics.of(out)

    def validate_contacts(self, contacts: Sequence[AccountContact]) -> BillingDiagnostics:
        out: list[Diagnostic] = []
        for role in ContactRole:
            primaries = [c for c in contacts if c.role is role and c.is_primary]
            if len(primaries) > 1:
                out.append(
                    _err(
                        "account.contacts",
                        C.INVALID_CONTACT,
                        f"{len(primaries)} primary {role.value} contacts; at most one is allowed",
                        "'primary' is how a document picks one recipient; two makes it a coin toss",
                    )
                )
        for i, contact in enumerate(contacts):
            if not contact.is_reachable:
                out.append(
                    _err(
                        f"account.contacts[{i}]",
                        C.INVALID_CONTACT,
                        f"the {contact.role.value} contact has neither an email nor a phone",
                    )
                )
            elif contact.contact.email and "@" not in contact.contact.email:
                out.append(
                    _err(
                        f"account.contacts[{i}].email",
                        C.INVALID_CONTACT,
                        f"{contact.contact.email!r} is not an email address",
                    )
                )
        return BillingDiagnostics.of(out)

    def validate_addresses(self, addresses: Sequence[AccountAddress]) -> BillingDiagnostics:
        out: list[Diagnostic] = []
        for kind in AddressKind:
            primaries = [a for a in addresses if a.kind is kind and a.is_primary]
            if len(primaries) > 1:
                out.append(
                    _err(
                        "account.addresses",
                        C.INVALID_ADDRESS,
                        f"{len(primaries)} primary {kind.value} addresses; at most one is allowed",
                    )
                )
        for i, entry in enumerate(addresses):
            if not entry.is_complete:
                out.append(
                    _err(
                        f"account.addresses[{i}]",
                        C.INVALID_ADDRESS,
                        f"the {entry.kind.value} address needs at least a line, a city, "
                        "and a country",
                    )
                )
            if entry.address.country and len(entry.address.country) != 2:
                out.append(
                    _err(
                        f"account.addresses[{i}].country",
                        C.INVALID_ADDRESS,
                        f"{entry.address.country!r} is not an ISO-3166 alpha-2 country code",
                        "tax determination keys on it; a free-text country makes that a guess",
                    )
                )
        return BillingDiagnostics.of(out)

    def validate_preferences(self, preferences: BillingPreferences) -> BillingDiagnostics:
        out: list[Diagnostic] = []
        if not 1 <= preferences.billing_day <= 28:
            out.append(
                _err(
                    "preferences.billing_day",
                    C.INVALID_BILLING_PREFERENCE,
                    f"billing day {preferences.billing_day} is outside 1..28",
                    "28 is the last day every month has; beyond it the date silently drifts",
                )
            )
        if preferences.payment_terms_days < 0:
            out.append(
                _err(
                    "preferences.payment_terms_days",
                    C.INVALID_BILLING_PREFERENCE,
                    "payment terms cannot be negative",
                )
            )
        if preferences.purchase_order_required and not preferences.purchase_order_number:
            out.append(
                _warn(
                    "preferences.purchase_order_number",
                    C.INVALID_BILLING_PREFERENCE,
                    "a PO is required but none is recorded",
                    "an invoice without the PO number will be rejected by the customer's AP",
                )
            )
        return BillingDiagnostics.of(out)

    def validate_credit(self, credit: CreditProfile, *, currency=None) -> BillingDiagnostics:
        out: list[Diagnostic] = []
        if credit.credit_limit is not None:
            if credit.credit_limit.is_negative:
                out.append(
                    _err(
                        "credit.credit_limit",
                        C.INVALID_CREDIT_LIMIT,
                        "a credit limit cannot be negative",
                        "zero means 'no credit'; negative means nothing",
                    )
                )
            if currency is not None and credit.credit_limit.currency is not currency:
                out.append(
                    _err(
                        "credit.credit_limit",
                        C.CURRENCY_MISMATCH,
                        f"limit is {credit.credit_limit.currency}, the account bills in {currency}",
                        "an account's currency is fixed at creation and never converts",
                    )
                )
        if credit.payment_terms_days < 0:
            out.append(
                _err(
                    "credit.payment_terms_days",
                    C.INVALID_CREDIT_LIMIT,
                    "payment terms cannot be negative",
                )
            )
        return BillingDiagnostics.of(out)

    def _commercial_status(self, account: CommercialAccount) -> list[Diagnostic]:
        """Commercial status and lifecycle state are independent, but not every pairing is
        coherent — a draft that has never traded cannot be delinquent."""
        out: list[Diagnostic] = []
        if account.state is AccountState.DRAFT and account.commercial_status not in (
            CommercialStatus.GOOD_STANDING,
            CommercialStatus.WATCH,
        ):
            out.append(
                _err(
                    "account.commercial_status",
                    C.INVALID_COMMERCIAL_STATUS,
                    f"a draft account cannot be {account.commercial_status.value}; "
                    "it has never traded",
                )
            )
        if (
            account.commercial_status is CommercialStatus.CREDIT_HOLD
            and not account.credit.credit_hold
        ):
            out.append(
                _warn(
                    "account.credit.credit_hold",
                    C.INVALID_COMMERCIAL_STATUS,
                    "commercial status says credit hold but the credit profile does not",
                    "one of the two will be read by a downstream decision; make them agree",
                )
            )
        return out

    # ── relationships ───────────────────────────────────────────────────────────

    def validate_hierarchy_edge(
        self, account: CommercialAccount, known: Sequence[CommercialAccount]
    ) -> BillingDiagnostics:
        """The parent edge this account is about to hold."""
        out: list[Diagnostic] = []
        if not account.parent_account_id:
            return BillingDiagnostics.of(out)
        by_id = {a.id: a for a in known}
        parent = by_id.get(account.parent_account_id)
        if parent is None:
            out.append(
                _err(
                    "account.parent_account_id",
                    C.UNKNOWN_ACCOUNT,
                    f"no account {account.parent_account_id!r} in this organization",
                )
            )
            return BillingDiagnostics.of(out)
        if parent.org_id != account.org_id:
            out.append(
                _err(
                    "account.parent_account_id",
                    C.CROSS_ORG_REFERENCE,
                    "a parent account must belong to the same organization",
                )
            )
        if parent.currency is not account.currency:
            out.append(
                _err(
                    "account.parent_account_id",
                    C.CROSS_CURRENCY_HIERARCHY,
                    f"parent bills in {parent.currency}, this account in {account.currency}",
                    "a consolidated invoice cannot mix currencies; use a separate hierarchy",
                )
            )
        if parent.state in (AccountState.CLOSED, AccountState.ARCHIVED, AccountState.DELETED):
            out.append(
                _err(
                    "account.parent_account_id",
                    C.INVALID_HIERARCHY,
                    f"parent {parent.account_number!r} is {parent.state.value} and cannot take "
                    "on children",
                )
            )
        cycle = self._hierarchy.find_cycle(account, known)
        if cycle:
            out.append(
                _err(
                    "account.parent_account_id",
                    C.ACCOUNT_CYCLE,
                    f"billing hierarchy forms a cycle: {' → '.join(cycle)}",
                    "an account may not be its own ancestor, directly or transitively",
                )
            )
        elif self._hierarchy.depth(account, known) >= MAX_HIERARCHY_DEPTH:
            out.append(
                _err(
                    "account.parent_account_id",
                    C.HIERARCHY_TOO_DEEP,
                    f"billing hierarchy would be {self._hierarchy.depth(account, known) + 1} "
                    f"levels deep; the maximum is {MAX_HIERARCHY_DEPTH}",
                )
            )
        return BillingDiagnostics.of(out)

    def validate_payer_edge(
        self, account: CommercialAccount, known: Sequence[CommercialAccount]
    ) -> BillingDiagnostics:
        """The payer edge this account is about to hold. Independent of the parent edge."""
        out: list[Diagnostic] = []
        if not account.payer_account_id:
            return BillingDiagnostics.of(out)
        by_id = {a.id: a for a in known}
        payer = by_id.get(account.payer_account_id)
        if payer is None:
            out.append(
                _err(
                    "account.payer_account_id",
                    C.UNKNOWN_ACCOUNT,
                    f"no account {account.payer_account_id!r} in this organization",
                )
            )
            return BillingDiagnostics.of(out)
        if payer.org_id != account.org_id:
            out.append(
                _err(
                    "account.payer_account_id",
                    C.CROSS_ORG_REFERENCE,
                    "a payer must belong to the same organization",
                )
            )
        if payer.currency is not account.currency:
            out.append(
                _err(
                    "account.payer_account_id",
                    C.CURRENCY_MISMATCH,
                    f"payer bills in {payer.currency}, this account in {account.currency}",
                )
            )
        if payer.state is not AccountState.ACTIVE:
            out.append(
                _err(
                    "account.payer_account_id",
                    C.INVALID_HIERARCHY,
                    f"payer {payer.account_number!r} is {payer.state.value}; only an active "
                    "account can be made to pay",
                )
            )
        loop = self._payers.find_loop(account, known)
        if loop:
            out.append(
                _err(
                    "account.payer_account_id",
                    C.PAYER_LOOP,
                    f"payer relationships form a loop: {' → '.join(loop)}",
                    "somebody in the chain must ultimately pay for themselves",
                )
            )
        return BillingDiagnostics.of(out)

    # ── whole population ────────────────────────────────────────────────────────

    def validate_population(self, accounts: Sequence[CommercialAccount]) -> BillingDiagnostics:
        """Cross-account consistency across one organization: uniqueness, cycles, loops.

        The check that answers "is this customer book coherent?" — the one a migration, an
        import, or a periodic audit needs, and the one no single-account validation can do."""
        out: list[Diagnostic] = []
        for account in accounts:
            out.extend(self.validate_account(account).findings)

        for dupe in _duplicates(a.account_number for a in accounts):
            out.append(
                _err(
                    "accounts.account_number",
                    C.DUPLICATE_ACCOUNT_NUMBER,
                    f"account number {dupe!r} is used by more than one account",
                    "it appears on invoices; two accounts sharing one is a reconciliation defect",
                )
            )
        identifiers = [
            identifier for account in accounts for identifier in account.legal_identifiers
        ]
        for dupe in _duplicates(identifiers):
            out.append(
                _err(
                    "accounts.legal_entity",
                    C.DUPLICATE_LEGAL_IDENTIFIER,
                    f"legal identifier {dupe!r} appears on more than one account",
                    "the same registered company twice is usually a duplicate customer",
                )
            )
        for account in accounts:
            out.extend(self.validate_hierarchy_edge(account, accounts).findings)
            out.extend(self.validate_payer_edge(account, accounts).findings)
        return BillingDiagnostics.of(out)


# ── lifecycle ───────────────────────────────────────────────────────────────────


class AccountLifecycleService:
    """Applies account lifecycle triggers through the shared FSM. Illegal edges are rejected
    with a typed error, never coerced into an adjacent state."""

    def __init__(self, definition: StateMachineDefinition = ACCOUNT_LIFECYCLE) -> None:
        self._definition = definition

    def can(self, current: AccountState, trigger: AccountTrigger) -> bool:
        return self._definition.get(current, trigger) is not None

    def next_state(self, current: AccountState, trigger: AccountTrigger) -> AccountState:
        edge = self._definition.get(current, trigger)
        if edge is None:
            legal = sorted(t.value for t in self._definition.triggers_from(current))
            raise InvalidBillingTransition(
                f"billing account cannot {trigger.value} from {current.value}; "
                f"legal triggers: {legal}"
            )
        return edge.target

    def allowed(self, current: AccountState) -> tuple[AccountTrigger, ...]:
        """Which moves are legal from here — what a UI needs to render buttons that cannot
        produce a 409."""
        return tuple(t for t in AccountTrigger if self.can(current, t))

    def activation_trigger(self, account: CommercialAccount) -> AccountTrigger:
        """Which trigger "make this account active" means from where it stands.

        A draft is *activated*; a suspended account is *resumed*. Two different facts about
        the relationship, and callers should not have to know which one applies — but the
        distinction belongs in the history, so the FSM keeps both edges."""
        if account.state is AccountState.SUSPENDED:
            return AccountTrigger.RESUME
        return AccountTrigger.ACTIVATE

    def apply(
        self,
        account: CommercialAccount,
        trigger: AccountTrigger,
        *,
        at: float,
        reason: str = "",
    ) -> CommercialAccount:
        state = self.next_state(account.state, trigger)
        changes: dict = {"state": state, "updated_at": at}
        if state is AccountState.ACTIVE and account.activated_at is None:
            changes["activated_at"] = at
        if state is AccountState.CLOSED:
            changes["closed_at"] = at
            changes["close_reason"] = reason or account.close_reason
        return replace(account, **changes)

    def guard_mutable(self, account: CommercialAccount) -> None:
        """Closed, archived, and deleted accounts are retained records, not working ones.

        Their commercial details are evidence of what was agreed; editing them after the
        relationship ended would change the record of a relationship that is over."""
        if account.state in (
            AccountState.CLOSED,
            AccountState.ARCHIVED,
            AccountState.DELETED,
        ):
            raise BillingConflict(
                f"billing account {account.account_number!r} is {account.state.value}; "
                "its commercial record is retained and no longer editable"
            )


# ── search ──────────────────────────────────────────────────────────────────────


class AccountSearchService:
    """Deterministic account filtering. No scoring, no ranking, no index — the same accounts
    and the same query always produce the same rows in the same order.

    Ordering is by account number, always. Arbitrary but *stable*, which is the property a
    paged admin screen and a reproducible test both actually need."""

    def search(
        self, accounts: Sequence[CommercialAccount], query: AccountSearchQuery
    ) -> AccountSearchResult:
        matched = [a for a in accounts if self._matches(a, query)]
        matched.sort(key=lambda a: a.account_number)
        start = max(query.offset, 0)
        page = tuple(matched[start : start + max(query.limit, 0)])
        return AccountSearchResult(accounts=page, total=len(matched))

    def _matches(self, account: CommercialAccount, query: AccountSearchQuery) -> bool:
        return (
            self._text_matches(
                query.text,
                account.account_number,
                account.display_name,
                account.legal_entity.legal_name,
                account.legal_entity.registration_number,
                account.legal_entity.tax_identifier,
            )
            and (not query.states or account.state in query.states)
            and (not query.kinds or account.kind in query.kinds)
            and (not query.statuses or account.commercial_status in query.statuses)
            and (not query.currencies or account.currency in query.currencies)
            and (not query.countries or self._country(account) in query.countries)
            and (not query.risk_ratings or account.credit.risk_rating in query.risk_ratings)
            and (
                not query.parent_account_id or account.parent_account_id == query.parent_account_id
            )
            and (not query.payer_account_id or account.payer_account_id == query.payer_account_id)
            and (not query.roots_only or not account.parent_account_id)
            and (not query.on_hold_only or account.is_on_hold)
        )

    @staticmethod
    def _country(account: CommercialAccount) -> str:
        address = account.addresses.billing_address
        return address.country if address else account.legal_entity.incorporation_country

    @staticmethod
    def _text_matches(text: str, *fields: str) -> bool:
        if not text:
            return True
        needle = text.casefold()
        return any(needle in (value or "").casefold() for value in fields)


# ── commercial profile ──────────────────────────────────────────────────────────


class CommercialProfileService:
    """Composes the commercial profile — a derived read model, never a stored one.

    Everything it returns is computed from the account and its neighbours, so there is no
    second copy of a commercial fact that can fall out of date with the first (§A2.5)."""

    def __init__(
        self,
        hierarchy: AccountHierarchyResolver | None = None,
        payers: PayerResolver | None = None,
    ) -> None:
        self._hierarchy = hierarchy or AccountHierarchyResolver()
        self._payers = payers or PayerResolver()

    def compose(
        self, account: CommercialAccount, known: Sequence[CommercialAccount]
    ) -> CommercialProfileView:
        by_id = {a.id: a for a in known}
        parent = by_id.get(account.parent_account_id) if account.parent_account_id else None
        payer = by_id.get(account.payer_account_id) if account.payer_account_id else None
        children = self._hierarchy.children(account, known)
        descendants = self._hierarchy.descendants(account, known)
        return CommercialProfileView(
            account=account,
            parent=parent,
            payer=payer,
            child_count=len(children),
            descendant_count=len(descendants),
            hierarchy_depth=self._hierarchy.depth(account, known),
            payer_chain=self._payers.chain(account, known),
        )
