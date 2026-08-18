"""M9.8 — Billing Accounts: tests.

Four layers, one domain: pure Foundation rules, then the Application over a real database, then
the HTTP surface, then architecture compliance. The heaviest coverage sits on the three
properties this domain cannot be retrofitted with — **a billing hierarchy separate from
organization tenancy**, **payer relationships separate from the hierarchy**, and **records that
outlive the customer** — and on the boundary that keeps an account an account: it says *who* has
the commercial relationship, never what they bought or what they owe.
"""

from __future__ import annotations

import ast
from dataclasses import replace
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from api.customer.jwt import encode as epic8_encode
from billing.application import (
    AccountHistory,
    ActivateBillingAccount,
    AddAccountNote,
    AllowAllAuthorizer,
    ArchiveBillingAccount,
    AssignParent,
    AssignPayer,
    BillingAuthorizationError,
    CloseCommercialAccount,
    CommercialProfile,
    CreateBillingAccount,
    DeleteDraftAccount,
    DenyAllAuthorizer,
    GetBillingAccount,
    ListHierarchy,
    RestoreBillingAccount,
    SearchBillingAccounts,
    SuspendBillingAccount,
    UpdateAddresses,
    UpdateBillingPreferences,
    UpdateContacts,
    UpdateCreditProfile,
)
from billing.customer import BillingApiSettings, BillingContainer, create_billing_app
from billing.foundation import (
    ACCOUNT_LIFECYCLE,
    MAX_HIERARCHY_DEPTH,
    AccountAddress,
    AccountFactory,
    AccountHierarchyResolver,
    AccountLifecycleService,
    AccountSearchQuery,
    AccountSearchService,
    AccountState,
    AccountTrigger,
    AccountValidator,
    AddressBook,
    AddressKind,
    BillingAddress,
    BillingConflict,
    BillingPreferences,
    BillingValidationError,
    CommercialAccount,
    CommercialProfileService,
    CommercialStatus,
    ContactRole,
    CreditProfile,
    Currency,
    DefaultIdGenerator,
    FixedClock,
    InvalidBillingTransition,
    LegalEntity,
    Money,
    PayerResolver,
    RecordingEventEmitter,
    RiskRating,
    SequentialIdGenerator,
)
from billing.persistence import BillingDatabase, build_persistent_billing_application

PLATFORM = Path(__file__).resolve().parents[1]
BILLING = PLATFORM / "billing"
PREFIX = "/v1/billing/billing-accounts"
SECRET = "test-billing-secret"
ORG = dict(actor_id="usr_1", org_id="org_1")
INR = Currency.INR


# ── fixtures ────────────────────────────────────────────────────────────────────


def _factory() -> tuple[AccountFactory, RecordingEventEmitter]:
    emitter = RecordingEventEmitter()
    return (
        AccountFactory(ids=SequentialIdGenerator(), clock=FixedClock(), emitter=emitter),
        emitter,
    )


def _contact(role=ContactRole.BILLING, email="ap@acme.test", **kw):
    return AccountFactory.contact(role, email=email, **kw)


def _address(kind=AddressKind.BILLING, **kw):
    defaults = dict(line1="1 MG Road", city="Bengaluru", country="IN")
    return AccountFactory.address(kind, **{**defaults, **kw})


def _account(
    number="BA-000001",
    *,
    account_id=None,
    state=AccountState.ACTIVE,
    parent="",
    payer="",
    currency=INR,
    **kw,
) -> CommercialAccount:
    return CommercialAccount(
        id=account_id or f"cma_{number}",
        org_id="org_1",
        account_number=number,
        display_name=kw.pop("display_name", number),
        currency=currency,
        state=state,
        parent_account_id=parent,
        payer_account_id=payer,
        contacts=kw.pop("contacts", (_contact(),)),
        addresses=kw.pop("addresses", AddressBook((_address(),))),
        **kw,
    )


@pytest.fixture()
def app():
    wired = build_persistent_billing_application(
        ids=SequentialIdGenerator(), clock=FixedClock(), authorizer=AllowAllAuthorizer()
    )
    yield wired.application
    wired.database.close()


def _client(authorizer=None, **overrides) -> TestClient:
    settings = BillingApiSettings(environment="testing", jwt_secret=SECRET, **overrides)
    container = BillingContainer.build(
        settings,
        authorizer=authorizer if authorizer is not None else AllowAllAuthorizer(),
        ids=SequentialIdGenerator(),
        clock=FixedClock(),
    )
    return TestClient(
        create_billing_app(settings=settings, container=container), raise_server_exceptions=False
    )


def _auth(user_id="usr_1", org_id="org_1") -> dict[str, str]:
    claims: dict = {"sub": user_id}
    if org_id:
        claims["org"] = org_id
    return {"Authorization": "Bearer " + epic8_encode(claims, SECRET, ttl_seconds=3600)}


@pytest.fixture()
def client():
    with _client() as c:
        yield c


# ── 1. Foundation: lifecycle FSM ────────────────────────────────────────────────


def test_every_account_starts_as_a_draft():
    factory, _ = _factory()
    account = factory.create_account(org_id="org_1", display_name="Acme", currency=INR)
    assert account.state is AccountState.DRAFT
    assert account.activated_at is None and account.closed_at is None


@pytest.mark.parametrize(
    ("state", "trigger", "expected"),
    [
        (AccountState.DRAFT, AccountTrigger.ACTIVATE, AccountState.ACTIVE),
        (AccountState.DRAFT, AccountTrigger.DELETE, AccountState.DELETED),
        (AccountState.ACTIVE, AccountTrigger.SUSPEND, AccountState.SUSPENDED),
        (AccountState.ACTIVE, AccountTrigger.CLOSE, AccountState.CLOSED),
        (AccountState.SUSPENDED, AccountTrigger.RESUME, AccountState.ACTIVE),
        (AccountState.SUSPENDED, AccountTrigger.CLOSE, AccountState.CLOSED),
        (AccountState.CLOSED, AccountTrigger.ARCHIVE, AccountState.ARCHIVED),
        (AccountState.ARCHIVED, AccountTrigger.RESTORE, AccountState.CLOSED),
    ],
)
def test_legal_lifecycle_edges(state, trigger, expected):
    assert AccountLifecycleService().next_state(state, trigger) is expected


@pytest.mark.parametrize(
    ("state", "trigger"),
    [
        (AccountState.DRAFT, AccountTrigger.SUSPEND),
        (AccountState.DRAFT, AccountTrigger.CLOSE),
        (AccountState.ACTIVE, AccountTrigger.ACTIVATE),
        (AccountState.ACTIVE, AccountTrigger.ARCHIVE),
        (AccountState.SUSPENDED, AccountTrigger.ACTIVATE),
        (AccountState.CLOSED, AccountTrigger.RESUME),
        (AccountState.ARCHIVED, AccountTrigger.ACTIVATE),
    ],
)
def test_illegal_lifecycle_edges_are_rejected_never_coerced(state, trigger):
    with pytest.raises(InvalidBillingTransition):
        AccountLifecycleService().next_state(state, trigger)


def test_only_a_draft_may_ever_be_deleted():
    """ADR-0023 RS4 in one assertion: exactly one edge reaches DELETED, and it starts at
    DRAFT. Everything past draft has revenue attached and is retained forever."""
    into_deleted = [
        (state, trigger)
        for state in AccountState
        for trigger in AccountTrigger
        if (edge := ACCOUNT_LIFECYCLE.get(state, trigger)) and edge.target is AccountState.DELETED
    ]
    assert into_deleted == [(AccountState.DRAFT, AccountTrigger.DELETE)]


def test_a_closed_account_can_never_be_reopened_or_deleted():
    lifecycle = AccountLifecycleService()
    assert set(lifecycle.allowed(AccountState.CLOSED)) == {AccountTrigger.ARCHIVE}
    assert set(lifecycle.allowed(AccountState.ARCHIVED)) == {AccountTrigger.RESTORE}


def test_restore_returns_to_closed_not_active():
    """Un-filing a record and reopening a commercial relationship are separate decisions."""
    assert (
        AccountLifecycleService().next_state(AccountState.ARCHIVED, AccountTrigger.RESTORE)
        is AccountState.CLOSED
    )


def test_deleted_is_terminal():
    assert AccountLifecycleService().allowed(AccountState.DELETED) == ()


def test_activation_trigger_distinguishes_a_draft_from_a_suspension():
    lifecycle = AccountLifecycleService()
    assert (
        lifecycle.activation_trigger(_account(state=AccountState.DRAFT)) is AccountTrigger.ACTIVATE
    )
    assert (
        lifecycle.activation_trigger(_account(state=AccountState.SUSPENDED))
        is AccountTrigger.RESUME
    )


def test_lifecycle_transitions_never_mutate_the_input():
    account = _account(state=AccountState.DRAFT)
    moved = AccountLifecycleService().apply(account, AccountTrigger.ACTIVATE, at=5.0)
    assert account.state is AccountState.DRAFT and account.activated_at is None
    assert moved.state is AccountState.ACTIVE and moved.activated_at == 5.0


def test_closing_records_when_and_why():
    closed = AccountLifecycleService().apply(
        _account(), AccountTrigger.CLOSE, at=99.0, reason="migrated to reseller"
    )
    assert closed.closed_at == 99.0 and closed.close_reason == "migrated to reseller"
    assert closed.is_retained_only


def test_a_retained_account_is_no_longer_editable():
    for state in (AccountState.CLOSED, AccountState.ARCHIVED):
        with pytest.raises(BillingConflict):
            AccountLifecycleService().guard_mutable(_account(state=state))
    AccountLifecycleService().guard_mutable(_account(state=AccountState.ACTIVE))  # no raise


# ── 2. Foundation: hierarchy ────────────────────────────────────────────────────


def _tree_accounts() -> list[CommercialAccount]:
    root = _account("BA-001", account_id="a")
    mid = _account("BA-002", account_id="b", parent="a")
    leaf = _account("BA-003", account_id="c", parent="b")
    sibling = _account("BA-004", account_id="d", parent="a")
    return [root, mid, leaf, sibling]


def test_ancestors_and_root_walk_the_parent_graph():
    accounts = _tree_accounts()
    resolver = AccountHierarchyResolver()
    leaf = accounts[2]
    assert [a.id for a in resolver.ancestors(leaf, accounts)] == ["b", "a"]
    assert resolver.root(leaf, accounts).id == "a"
    assert resolver.depth(leaf, accounts) == 2


def test_descendants_and_tree_shape():
    accounts = _tree_accounts()
    resolver = AccountHierarchyResolver()
    root = accounts[0]
    assert sorted(a.id for a in resolver.descendants(root, accounts)) == ["b", "c", "d"]
    tree = resolver.tree(root, accounts)
    assert tree.size == 4 and tree.depth == 0
    assert sorted(tree.descendant_ids) == ["b", "c", "d"]


def test_the_billing_tree_names_the_invoiced_account():
    """Attribution flows down; invoicing rolls up."""
    accounts = _tree_accounts()
    tree = AccountHierarchyResolver().billing_tree(accounts[2], accounts)
    assert tree.invoiced_account_id == "a"
    assert tree.size == 4 and tree.depth == 2
    assert tree.contains("c")


def test_self_parenting_is_a_cycle():
    account = _account("BA-001", account_id="a", parent="a")
    assert AccountHierarchyResolver().find_cycle(account, [account]) == ("a", "a")


def test_transitive_hierarchy_cycle_is_detected_with_its_path():
    a = _account("BA-001", account_id="a", parent="c")
    b = _account("BA-002", account_id="b", parent="a")
    c = _account("BA-003", account_id="c", parent="b")
    cycle = AccountHierarchyResolver().find_cycle(a, [a, b, c])
    assert cycle[0] == cycle[-1] and set(cycle) == {"a", "b", "c"}


def test_expanding_a_cyclic_hierarchy_is_loud_not_infinite():
    a = _account("BA-001", account_id="a", parent="b")
    b = _account("BA-002", account_id="b", parent="a")
    with pytest.raises(BillingConflict):
        AccountHierarchyResolver().expand(a, [a, b])


def test_a_tree_renderer_terminates_even_on_a_malformed_hierarchy():
    """Validation rejects cycles, but a renderer must not be what discovers one the hard way."""
    a = _account("BA-001", account_id="a", parent="b")
    b = _account("BA-002", account_id="b", parent="a")
    tree = AccountHierarchyResolver().tree(a, [a, b])
    assert tree.size <= MAX_HIERARCHY_DEPTH + 1


def test_hierarchy_depth_is_bounded():
    accounts = [
        _account(f"BA-{i:03d}", account_id=str(i), parent=str(i - 1) if i else "")
        for i in range(MAX_HIERARCHY_DEPTH + 2)
    ]
    deepest = accounts[-1]
    diagnostics = AccountValidator().validate_hierarchy_edge(deepest, accounts)
    assert any(f.code.value == "hierarchy_too_deep" for f in diagnostics.errors)


def test_a_parent_in_another_currency_is_rejected():
    parent = _account("BA-001", account_id="a", currency=Currency.USD)
    child = _account("BA-002", account_id="b", parent="a", currency=INR)
    diagnostics = AccountValidator().validate_hierarchy_edge(child, [parent, child])
    assert any(f.code.value == "cross_currency_hierarchy" for f in diagnostics.errors)


def test_a_closed_parent_cannot_take_on_children():
    parent = _account("BA-001", account_id="a", state=AccountState.CLOSED)
    child = _account("BA-002", account_id="b", parent="a")
    diagnostics = AccountValidator().validate_hierarchy_edge(child, [parent, child])
    assert any(f.code.value == "invalid_hierarchy" for f in diagnostics.errors)


def test_an_unknown_parent_is_rejected():
    child = _account("BA-002", account_id="b", parent="ghost")
    diagnostics = AccountValidator().validate_hierarchy_edge(child, [child])
    assert any(f.code.value == "unknown_account" for f in diagnostics.errors)


# ── 3. Foundation: payers, separate from the hierarchy ──────────────────────────


def test_the_payer_graph_is_not_the_hierarchy():
    """A subsidiary paying for its parent is a legitimate arrangement, and neither graph is
    malformed because of it — the modelling error this domain exists to avoid."""
    parent = _account("BA-001", account_id="a", payer="b")
    child = _account("BA-002", account_id="b", parent="a")
    accounts = [parent, child]
    assert AccountHierarchyResolver().find_cycle(child, accounts) == ()
    assert PayerResolver().find_loop(parent, accounts) == ()
    assert PayerResolver().ultimate_payer(parent, accounts).id == "b"


def test_self_payment_is_a_loop():
    account = _account("BA-001", account_id="a", payer="a")
    assert PayerResolver().find_loop(account, [account]) == ("a", "a")


def test_a_payer_loop_is_detected_with_its_path():
    a = _account("BA-001", account_id="a", payer="b")
    b = _account("BA-002", account_id="b", payer="a")
    loop = PayerResolver().find_loop(a, [a, b])
    assert loop[0] == loop[-1] and set(loop) == {"a", "b"}


def test_a_payer_loop_is_rejected_by_validation():
    a = _account("BA-001", account_id="a", payer="b")
    b = _account("BA-002", account_id="b", payer="a")
    diagnostics = AccountValidator().validate_payer_edge(a, [a, b])
    assert any(f.code.value == "payer_loop" for f in diagnostics.errors)


def test_the_payer_chain_resolves_to_an_ultimate_payer():
    a = _account("BA-001", account_id="a", payer="b")
    b = _account("BA-002", account_id="b", payer="c")
    c = _account("BA-003", account_id="c")
    resolver = PayerResolver()
    assert resolver.chain(a, [a, b, c]) == ("a", "b", "c")
    assert resolver.ultimate_payer(a, [a, b, c]).id == "c"


def test_an_account_with_no_payer_pays_for_itself():
    account = _account("BA-001", account_id="a")
    assert account.effective_payer_id == "a"
    assert PayerResolver().ultimate_payer(account, [account]).id == "a"


def test_only_an_active_account_can_be_made_to_pay():
    payer = _account("BA-001", account_id="a", state=AccountState.SUSPENDED)
    account = _account("BA-002", account_id="b", payer="a")
    diagnostics = AccountValidator().validate_payer_edge(account, [payer, account])
    assert any(f.code.value == "invalid_hierarchy" for f in diagnostics.errors)


def test_a_child_or_a_redirected_payer_is_not_billed_here():
    assert not _account(parent="a").is_billable
    assert not _account(payer="b").is_billable
    assert _account().is_billable


# ── 4. Foundation: validation ───────────────────────────────────────────────────


def test_validation_never_raises_and_reports_every_finding():
    broken = CommercialAccount(
        id="cma_x", org_id="", account_number="", display_name="", currency=INR
    )
    diagnostics = AccountValidator().validate_account(broken)
    assert len(diagnostics.errors) >= 3
    assert diagnostics.findings == tuple(sorted(diagnostics.findings))


def test_duplicate_account_numbers_are_detected():
    accounts = [_account("BA-001", account_id="a"), _account("BA-001", account_id="b")]
    diagnostics = AccountValidator().validate_population(accounts)
    assert any(f.code.value == "duplicate_account_number" for f in diagnostics.errors)


def test_duplicate_legal_identifiers_are_detected():
    entity = LegalEntity(legal_name="Acme Pvt Ltd", registration_number="U72900KA")
    accounts = [
        _account("BA-001", account_id="a", legal_entity=entity),
        _account("BA-002", account_id="b", legal_entity=entity),
    ]
    diagnostics = AccountValidator().validate_population(accounts)
    assert any(f.code.value == "duplicate_legal_identifier" for f in diagnostics.errors)


def test_an_account_number_must_be_typeable():
    diagnostics = AccountValidator().validate_account(_account("BA 001 / ½"))
    assert any(f.code.value == "invalid_code" for f in diagnostics.errors)


def test_an_active_account_needs_a_billing_address():
    account = _account(addresses=AddressBook(()))
    diagnostics = AccountValidator().validate_account(account)
    assert any(f.code.value == "missing_billing_address" for f in diagnostics.errors)


def test_a_draft_needs_no_billing_address_yet():
    account = _account(state=AccountState.DRAFT, addresses=AddressBook(()), contacts=())
    assert not AccountValidator().validate_account(account).has_errors


def test_two_primary_addresses_of_one_kind_are_rejected():
    book = AddressBook((_address(), _address(city="Chennai")))
    diagnostics = AccountValidator().validate_addresses(book.entries)
    assert any(f.code.value == "invalid_address" for f in diagnostics.errors)


def test_an_incomplete_address_is_rejected():
    diagnostics = AccountValidator().validate_addresses(
        (AccountAddress(kind=AddressKind.BILLING, address=BillingAddress(line1="1 MG Road")),)
    )
    assert any(f.code.value == "invalid_address" for f in diagnostics.errors)


def test_a_free_text_country_is_rejected():
    diagnostics = AccountValidator().validate_addresses((_address(country="India"),))
    assert any(f.code.value == "invalid_address" for f in diagnostics.errors)


def test_two_primary_contacts_of_one_role_are_rejected():
    diagnostics = AccountValidator().validate_contacts((_contact(), _contact(email="b@acme.test")))
    assert any(f.code.value == "invalid_contact" for f in diagnostics.errors)


def test_an_unreachable_contact_is_rejected():
    diagnostics = AccountValidator().validate_contacts(
        (AccountFactory.contact(ContactRole.BILLING, name="Nobody"),)
    )
    assert any(f.code.value == "invalid_contact" for f in diagnostics.errors)


@pytest.mark.parametrize("day", [0, 29, 31])
def test_a_billing_day_outside_1_to_28_is_rejected(day):
    diagnostics = AccountValidator().validate_preferences(BillingPreferences(billing_day=day))
    assert any(f.code.value == "invalid_billing_preference" for f in diagnostics.errors)


def test_a_negative_credit_limit_is_rejected():
    diagnostics = AccountValidator().validate_credit(
        CreditProfile(credit_limit=Money(-1, INR)), currency=INR
    )
    assert any(f.code.value == "invalid_credit_limit" for f in diagnostics.errors)


def test_a_credit_limit_in_another_currency_is_rejected():
    diagnostics = AccountValidator().validate_credit(
        CreditProfile(credit_limit=Money(100, Currency.USD)), currency=INR
    )
    assert any(f.code.value == "currency_mismatch" for f in diagnostics.errors)


def test_a_draft_cannot_be_delinquent():
    account = _account(state=AccountState.DRAFT, commercial_status=CommercialStatus.DELINQUENT)
    diagnostics = AccountValidator().validate_account(account)
    assert any(f.code.value == "invalid_commercial_status" for f in diagnostics.errors)


def test_a_credit_limit_permits_within_it_and_not_beyond():
    """Comparison only — this domain owns no balance, so the exposure is an argument."""
    profile = CreditProfile(credit_limit=Money(100_000, INR))
    assert profile.permits(Money(99_999, INR))
    assert profile.permits(Money(100_000, INR))
    assert not profile.permits(Money(100_001, INR))
    assert not profile.permits(Money(1, Currency.USD))  # never a silent conversion
    assert not replace(profile, credit_hold=True).permits(Money(1, INR))
    assert CreditProfile().permits(Money(10**9, INR))  # no limit set


# ── 5. Foundation: search + profile ─────────────────────────────────────────────


def test_search_is_deterministic_and_ordered_by_number():
    accounts = [_account("BA-003", account_id="c"), _account("BA-001", account_id="a")]
    result = AccountSearchService().search(accounts, AccountSearchQuery())
    assert [a.account_number for a in result.accounts] == ["BA-001", "BA-003"]
    assert result.total == 2


def test_search_matches_across_identity_fields():
    account = _account(
        "BA-001", legal_entity=LegalEntity(legal_name="Acme Pvt Ltd", tax_identifier="29ABC")
    )
    service = AccountSearchService()
    for text in ("acme", "29abc", "ba-001"):
        assert service.search([account], AccountSearchQuery(text=text)).total == 1
    assert service.search([account], AccountSearchQuery(text="nomatch")).total == 0


def test_search_filters_compose():
    a = _account("BA-001", account_id="a")
    b = _account("BA-002", account_id="b", state=AccountState.SUSPENDED, parent="a")
    service = AccountSearchService()
    assert service.search([a, b], AccountSearchQuery(roots_only=True)).total == 1
    assert service.search([a, b], AccountSearchQuery(states=(AccountState.SUSPENDED,))).total == 1
    assert service.search([a, b], AccountSearchQuery(parent_account_id="a")).total == 1


def test_search_pages_without_losing_the_total():
    accounts = [_account(f"BA-{i:03d}", account_id=str(i)) for i in range(5)]
    result = AccountSearchService().search(accounts, AccountSearchQuery(limit=2, offset=1))
    assert len(result.accounts) == 2 and result.total == 5


def test_the_commercial_profile_is_derived_not_stored():
    accounts = _tree_accounts()
    profile = CommercialProfileService().compose(accounts[1], accounts)
    assert profile.parent.id == "a" and profile.child_count == 1
    assert profile.descendant_count == 1 and profile.hierarchy_depth == 1
    assert profile.is_self_paying and not profile.is_root
    assert profile.invoiced_account_id == "a"


# ── 6. Application over a real database ─────────────────────────────────────────


def _create(app, name="Acme", *, activate=True, **kw) -> CommercialAccount:
    account = app.commands.dispatch(
        CreateBillingAccount(
            **ORG,
            display_name=name,
            currency=INR,
            contacts=(_contact(),),
            addresses=(_address(),),
            **kw,
        )
    )
    if activate:
        app.commands.dispatch(ActivateBillingAccount(**ORG, account_id=account.id))
    return account


def test_account_numbers_are_allocated_and_never_reused(app):
    first = _create(app, "One", activate=False)
    second = _create(app, "Two", activate=False)
    assert first.account_number == "BA-000001"
    assert second.account_number == "BA-000002"

    # Deleting a draft must not hand its number back.
    app.commands.dispatch(DeleteDraftAccount(**ORG, account_id=second.id))
    third = _create(app, "Three", activate=False)
    assert third.account_number == "BA-000003"


def test_account_lifecycle_through_the_application(app):
    account = _create(app, activate=False)
    view = app.queries.dispatch(GetBillingAccount(**ORG, account_id=account.id))
    assert view.state == "draft" and view.allowed_triggers == ("activate", "delete")

    app.commands.dispatch(ActivateBillingAccount(**ORG, account_id=account.id))
    assert app.queries.dispatch(GetBillingAccount(**ORG, account_id=account.id)).state == "active"

    app.commands.dispatch(SuspendBillingAccount(**ORG, account_id=account.id, reason="unpaid"))
    suspended = app.queries.dispatch(GetBillingAccount(**ORG, account_id=account.id))
    assert suspended.state == "suspended"

    # "Activate" from suspended means resume — the Foundation picks the trigger.
    app.commands.dispatch(ActivateBillingAccount(**ORG, account_id=account.id))
    assert app.queries.dispatch(GetBillingAccount(**ORG, account_id=account.id)).state == "active"

    app.commands.dispatch(CloseCommercialAccount(**ORG, account_id=account.id, reason="churn"))
    closed = app.queries.dispatch(GetBillingAccount(**ORG, account_id=account.id))
    assert closed.state == "closed" and closed.close_reason == "churn" and closed.is_retained_only

    app.commands.dispatch(ArchiveBillingAccount(**ORG, account_id=account.id))
    assert app.queries.dispatch(GetBillingAccount(**ORG, account_id=account.id)).state == "archived"
    app.commands.dispatch(RestoreBillingAccount(**ORG, account_id=account.id))
    assert app.queries.dispatch(GetBillingAccount(**ORG, account_id=account.id)).state == "closed"


def test_an_active_account_can_never_be_deleted(app):
    account = _create(app)
    with pytest.raises(InvalidBillingTransition):
        app.commands.dispatch(DeleteDraftAccount(**ORG, account_id=account.id))


def test_a_closed_account_refuses_commercial_edits(app):
    account = _create(app)
    app.commands.dispatch(CloseCommercialAccount(**ORG, account_id=account.id))
    with pytest.raises(BillingConflict):
        app.commands.dispatch(
            UpdateBillingPreferences(
                **ORG, account_id=account.id, preferences=BillingPreferences(billing_day=5)
            )
        )


def test_a_note_may_still_be_added_to_a_closed_account(app):
    """A note is a record *about* the relationship, not a change to it."""
    account = _create(app)
    app.commands.dispatch(CloseCommercialAccount(**ORG, account_id=account.id))
    app.commands.dispatch(AddAccountNote(**ORG, account_id=account.id, body="final statement sent"))
    view = app.queries.dispatch(GetBillingAccount(**ORG, account_id=account.id))
    assert [n.body for n in view.notes] == ["final statement sent"]


def test_illegal_transition_is_rejected_through_the_application(app):
    account = _create(app, activate=False)
    with pytest.raises(InvalidBillingTransition):
        app.commands.dispatch(SuspendBillingAccount(**ORG, account_id=account.id))


def test_hierarchy_through_the_application(app):
    parent = _create(app, "Parent")
    child = _create(app, "Child")
    grandchild = _create(app, "Grandchild")
    app.commands.dispatch(AssignParent(**ORG, account_id=child.id, parent_account_id=parent.id))
    app.commands.dispatch(AssignParent(**ORG, account_id=grandchild.id, parent_account_id=child.id))

    tree = app.queries.dispatch(ListHierarchy(**ORG, account_id=grandchild.id))
    assert tree.root.account_id == parent.id
    assert tree.size == 3 and tree.depth == 2
    assert tree.invoiced_account_id == parent.id

    view = app.queries.dispatch(GetBillingAccount(**ORG, account_id=child.id))
    assert view.kind == "child" and view.is_child and not view.is_billable


def test_a_hierarchy_cycle_is_rejected_through_the_application(app):
    a = _create(app, "A")
    b = _create(app, "B")
    app.commands.dispatch(AssignParent(**ORG, account_id=b.id, parent_account_id=a.id))
    with pytest.raises(BillingValidationError) as excinfo:
        app.commands.dispatch(AssignParent(**ORG, account_id=a.id, parent_account_id=b.id))
    assert any(f.code.value == "account_cycle" for f in excinfo.value.diagnostics.errors)


def test_detaching_a_child_returns_it_to_the_root(app):
    parent = _create(app, "Parent")
    child = _create(app, "Child")
    app.commands.dispatch(AssignParent(**ORG, account_id=child.id, parent_account_id=parent.id))
    app.commands.dispatch(AssignParent(**ORG, account_id=child.id, parent_account_id=""))
    tree = app.queries.dispatch(ListHierarchy(**ORG, account_id=child.id))
    assert tree.root.account_id == child.id and tree.size == 1


def test_payer_assignment_is_independent_of_the_hierarchy(app):
    parent = _create(app, "Parent")
    child = _create(app, "Child")
    outsider = _create(app, "Outsider")
    app.commands.dispatch(AssignParent(**ORG, account_id=child.id, parent_account_id=parent.id))
    # An account outside the hierarchy pays for the child — legitimate, and neither graph breaks.
    app.commands.dispatch(AssignPayer(**ORG, account_id=child.id, payer_account_id=outsider.id))

    profile = app.queries.dispatch(CommercialProfile(**ORG, account_id=child.id))
    assert profile.parent_account_id == parent.id
    assert profile.payer_account_id == outsider.id
    assert profile.invoiced_account_id == outsider.id  # the payer wins over the parent
    assert profile.payer_chain == (child.id, outsider.id)

    tree = app.queries.dispatch(ListHierarchy(**ORG, account_id=child.id))
    assert tree.root.account_id == parent.id  # the hierarchy is untouched


def test_a_payer_loop_is_rejected_through_the_application(app):
    a = _create(app, "A")
    b = _create(app, "B")
    app.commands.dispatch(AssignPayer(**ORG, account_id=a.id, payer_account_id=b.id))
    with pytest.raises(BillingValidationError) as excinfo:
        app.commands.dispatch(AssignPayer(**ORG, account_id=b.id, payer_account_id=a.id))
    assert any(f.code.value == "payer_loop" for f in excinfo.value.diagnostics.errors)


def test_commercial_detail_flows_through(app):
    account = _create(app)
    app.commands.dispatch(
        UpdateBillingPreferences(
            **ORG,
            account_id=account.id,
            preferences=BillingPreferences(billing_day=15, payment_terms_days=45),
        )
    )
    app.commands.dispatch(
        UpdateContacts(
            **ORG,
            account_id=account.id,
            contacts=(_contact(), _contact(ContactRole.TECHNICAL, email="eng@acme.test")),
        )
    )
    app.commands.dispatch(
        UpdateAddresses(
            **ORG,
            account_id=account.id,
            addresses=(_address(), _address(AddressKind.SHIPPING, city="Chennai")),
        )
    )
    app.commands.dispatch(
        UpdateCreditProfile(
            **ORG,
            account_id=account.id,
            credit=CreditProfile(
                credit_limit=Money(500_000, INR), risk_rating=RiskRating.LOW, credit_hold=True
            ),
            commercial_status=CommercialStatus.CREDIT_HOLD,
        )
    )
    view = app.queries.dispatch(GetBillingAccount(**ORG, account_id=account.id))
    assert view.preferences.billing_day == 15 and view.preferences.payment_terms_days == 45
    assert {c.role for c in view.contacts} == {"billing", "technical"}
    assert {a.kind for a in view.addresses} == {"billing", "shipping"}
    assert view.credit.credit_limit.minor == 500_000 and view.credit.has_limit
    assert view.commercial_status == "credit_hold" and view.is_on_hold


def test_an_invalid_credit_limit_is_rejected_through_the_application(app):
    account = _create(app)
    with pytest.raises(BillingValidationError):
        app.commands.dispatch(
            UpdateCreditProfile(
                **ORG,
                account_id=account.id,
                credit=CreditProfile(credit_limit=Money(100, Currency.USD)),
            )
        )


def test_search_through_the_application(app):
    _create(app, "Acme Group")
    _create(app, "Beta Labs", activate=False)
    result = app.queries.dispatch(SearchBillingAccounts(**ORG))
    assert [a.display_name for a in result.accounts] == ["Acme Group", "Beta Labs"]
    assert result.total == 2

    drafts = app.queries.dispatch(
        SearchBillingAccounts(**ORG, query=AccountSearchQuery(states=(AccountState.DRAFT,)))
    )
    assert [a.display_name for a in drafts.accounts] == ["Beta Labs"]


def test_an_account_resolves_by_id_or_by_number(app):
    account = _create(app)
    by_id = app.queries.dispatch(GetBillingAccount(**ORG, account_id=account.id))
    by_number = app.queries.dispatch(
        GetBillingAccount(**ORG, account_number=account.account_number)
    )
    assert by_id.id == by_number.id


def test_every_command_appends_account_history(app):
    account = _create(app)
    app.commands.dispatch(AddAccountNote(**ORG, account_id=account.id, body="hello"))
    app.commands.dispatch(UpdateContacts(**ORG, account_id=account.id, contacts=(_contact(),)))
    actions = {a.action for a in app.queries.dispatch(AccountHistory(**ORG, account_id=account.id))}
    assert actions == {
        "account.created",
        "account.activated",
        "account.note_added",
        "account.contacts_updated",
    }


def test_history_records_the_states_a_transition_moved_between(app):
    account = _create(app, activate=False)
    app.commands.dispatch(ActivateBillingAccount(**ORG, account_id=account.id))
    entries = app.queries.dispatch(AccountHistory(**ORG, account_id=account.id))
    activation = next(e for e in entries if e.action == "account.activated")
    assert activation.from_state == "draft" and activation.to_state == "active"


def test_a_failed_command_leaves_no_history_behind(app):
    account = _create(app)
    before = len(app.queries.dispatch(AccountHistory(**ORG, account_id=account.id)))
    with pytest.raises(BillingValidationError):
        app.commands.dispatch(
            AssignPayer(**ORG, account_id=account.id, payer_account_id=account.id)
        )
    assert len(app.queries.dispatch(AccountHistory(**ORG, account_id=account.id))) == before


# ── 7. isolation, authorization, durability ─────────────────────────────────────


def test_cross_org_isolation(app):
    account = _create(app)
    other = dict(actor_id="usr_2", org_id="org_2")
    assert app.queries.dispatch(GetBillingAccount(**other, account_id=account.id)) is None
    assert app.queries.dispatch(SearchBillingAccounts(**other)).total == 0
    assert app.queries.dispatch(ListHierarchy(**other, account_id=account.id)) is None
    assert app.queries.dispatch(CommercialProfile(**other, account_id=account.id)) is None


def test_account_numbers_are_sequenced_per_organization(app):
    first = _create(app, activate=False)
    other = dict(actor_id="usr_2", org_id="org_2")
    theirs = app.commands.dispatch(
        CreateBillingAccount(**other, display_name="Theirs", currency=Currency.USD)
    )
    assert first.account_number == theirs.account_number == "BA-000001"


def test_a_parent_in_another_organization_is_unreachable(app):
    other = dict(actor_id="usr_2", org_id="org_2")
    theirs = app.commands.dispatch(
        CreateBillingAccount(**other, display_name="Theirs", currency=INR)
    )
    mine = _create(app, "Mine")
    with pytest.raises(BillingValidationError):
        app.commands.dispatch(AssignParent(**ORG, account_id=mine.id, parent_account_id=theirs.id))


def test_every_account_command_and_query_is_authorized():
    from billing.application import ALL_ACCOUNT_COMMANDS, ALL_ACCOUNT_QUERIES

    wired = build_persistent_billing_application(
        ids=SequentialIdGenerator(), clock=FixedClock(), authorizer=DenyAllAuthorizer()
    )
    app = wired.application
    for message in ALL_ACCOUNT_COMMANDS:
        with pytest.raises(BillingAuthorizationError):
            app.commands.dispatch(message(**ORG))
    for message in ALL_ACCOUNT_QUERIES:
        with pytest.raises(BillingAuthorizationError):
            app.queries.dispatch(message(**ORG))
    wired.database.close()


def test_accounts_and_history_survive_a_restart():
    """A second Application over the same storage proves the data landed in the database
    rather than in some object's memory."""
    database = BillingDatabase("sqlite://")
    first = build_persistent_billing_application(
        database=database,
        ids=SequentialIdGenerator(),
        clock=FixedClock(),
        authorizer=AllowAllAuthorizer(),
    )
    parent = _create(first.application, "Parent")
    child = _create(first.application, "Child")
    first.application.commands.dispatch(
        AssignParent(**ORG, account_id=child.id, parent_account_id=parent.id)
    )

    # The restarted instance mints real ids: a second *deterministic* generator over the same
    # database would re-issue `bev_1` and collide with the durable fact stream the first
    # instance already wrote — which is exactly what an append-only event store should do.
    second = build_persistent_billing_application(
        database=database,
        create_all=False,
        ids=DefaultIdGenerator(),
        clock=FixedClock(),
        authorizer=AllowAllAuthorizer(),
    )
    app = second.application
    view = app.queries.dispatch(GetBillingAccount(**ORG, account_number="BA-000002"))
    assert view.parent_account_id == parent.id and view.state == "active"
    tree = app.queries.dispatch(ListHierarchy(**ORG, account_id=child.id))
    assert tree.size == 2
    assert len(app.queries.dispatch(AccountHistory(**ORG, account_id=child.id))) == 3

    # The number sequence survives too — it is stored, not derived.
    third = _create(app, "Third", activate=False)
    assert third.account_number == "BA-000003"
    database.close()


def test_account_tables_are_created_by_the_migration_chain():
    from sqlalchemy import inspect

    from billing.persistence.account_models import ACCOUNT_TABLE_NAMES
    from billing.persistence.migrations import MIGRATIONS, v0018_billing_accounts

    # The chain grows additively and never reorders: this migration keeps its position
    # whatever lands after it. Asserting the position states that rule; asserting the
    # tail would only state today's length.
    assert MIGRATIONS[5] is v0018_billing_accounts
    assert v0018_billing_accounts.VERSION == "0018"
    versions = [m.VERSION for m in MIGRATIONS]
    assert versions == sorted(versions)
    for earlier, later in zip(versions[:-1], versions[1:], strict=True):
        assert int(later) == int(earlier) + 1

    database = BillingDatabase("sqlite://")
    for module in MIGRATIONS:
        module.upgrade(database)
    tables = set(inspect(database.engine).get_table_names())
    assert set(ACCOUNT_TABLE_NAMES) <= tables
    assert all(n.startswith("cp_billing_") for n in ACCOUNT_TABLE_NAMES)
    # M9.2's own account table is untouched and still there.
    assert "cp_billing_account" in tables
    database.close()


def test_account_migration_is_idempotent_and_alters_nothing():
    from sqlalchemy import inspect

    from billing.persistence.migrations import v0018_billing_accounts

    source = Path(v0018_billing_accounts.__file__).read_text()
    assert "ALTER TABLE" not in source and "DROP" not in source

    database = BillingDatabase("sqlite://")
    v0018_billing_accounts.upgrade(database)
    before = set(inspect(database.engine).get_table_names())
    v0018_billing_accounts.upgrade(database)
    assert set(inspect(database.engine).get_table_names()) == before
    database.close()


def test_the_append_only_history_store_exposes_no_mutation_path():
    from billing.foundation import AccountActivityRepository

    methods = {n for n in dir(AccountActivityRepository) if not n.startswith("_")}
    assert not methods & {"update", "delete", "remove", "replace", "amend"}


# ── 8. HTTP surface ─────────────────────────────────────────────────────────────


def _http_create(client, name="Acme", *, activate=True) -> dict:
    created = client.post(
        PREFIX,
        headers=_auth(),
        json={
            "display_name": name,
            "currency": "INR",
            "legal_entity": {"legal_name": f"{name} Private Limited"},
            "contacts": [{"role": "billing", "email": "ap@acme.test"}],
            "addresses": [
                {"kind": "billing", "line1": "1 MG Road", "city": "Bengaluru", "country": "IN"}
            ],
        },
    )
    assert created.status_code == 201, created.text
    body = created.json()
    if activate:
        client.post(f"{PREFIX}/{body['id']}/activate", headers=_auth())
    return body


def test_account_lifecycle_over_http(client):
    created = _http_create(client, activate=False)
    assert created["state"] == "draft"
    assert created["allowed_triggers"] == ["activate", "delete"]
    assert created["account_number"] == "BA-000001"

    activated = client.post(f"{PREFIX}/{created['id']}/activate", headers=_auth()).json()
    assert activated["state"] == "active" and activated["allowed_triggers"] == ["suspend", "close"]

    suspended = client.post(
        f"{PREFIX}/{created['id']}/suspend", headers=_auth(), json={"reason": "unpaid"}
    ).json()
    assert suspended["state"] == "suspended"

    closed = client.post(
        f"{PREFIX}/{created['id']}/close", headers=_auth(), json={"reason": "churn"}
    ).json()
    assert closed["state"] == "closed" and closed["is_retained_only"] is True


def test_deleting_an_active_account_over_http_is_409(client):
    created = _http_create(client)
    assert client.delete(f"{PREFIX}/{created['id']}", headers=_auth()).status_code == 409


def test_deleting_a_draft_over_http_succeeds(client):
    created = _http_create(client, activate=False)
    deleted = client.delete(f"{PREFIX}/{created['id']}", headers=_auth())
    assert deleted.status_code == 200 and deleted.json()["state"] == "deleted"


def test_editing_a_closed_account_over_http_is_409(client):
    created = _http_create(client)
    client.post(f"{PREFIX}/{created['id']}/close", headers=_auth(), json={"reason": ""})
    response = client.patch(
        f"{PREFIX}/{created['id']}/preferences", headers=_auth(), json={"billing_day": 5}
    )
    assert response.status_code == 409


def test_an_invalid_account_over_http_is_422_with_diagnostics(client):
    response = client.post(
        PREFIX,
        headers=_auth(),
        json={"display_name": "", "currency": "INR"},
    )
    assert response.status_code == 422
    assert response.json()["error"]["diagnostics"]


def test_hierarchy_over_http(client):
    parent = _http_create(client, "Parent")
    child = _http_create(client, "Child")
    client.post(
        f"{PREFIX}/{child['id']}/parent",
        headers=_auth(),
        json={"parent_account_id": parent["id"]},
    )
    tree = client.get(f"{PREFIX}/{child['id']}/hierarchy", headers=_auth()).json()
    assert tree["root"]["account_id"] == parent["id"]
    assert tree["invoiced_account_id"] == parent["id"]
    assert tree["size"] == 2 and len(tree["root"]["children"]) == 1


def test_a_hierarchy_cycle_over_http_is_422_with_the_path(client):
    a = _http_create(client, "A")
    b = _http_create(client, "B")
    client.post(f"{PREFIX}/{b['id']}/parent", headers=_auth(), json={"parent_account_id": a["id"]})
    response = client.post(
        f"{PREFIX}/{a['id']}/parent", headers=_auth(), json={"parent_account_id": b["id"]}
    )
    assert response.status_code == 422
    messages = " ".join(d["message"] for d in response.json()["error"]["diagnostics"])
    assert "cycle" in messages and "→" in messages


def test_a_payer_loop_over_http_is_422(client):
    a = _http_create(client, "A")
    b = _http_create(client, "B")
    client.post(f"{PREFIX}/{a['id']}/payer", headers=_auth(), json={"payer_account_id": b["id"]})
    response = client.post(
        f"{PREFIX}/{b['id']}/payer", headers=_auth(), json={"payer_account_id": a["id"]}
    )
    assert response.status_code == 422


def test_commercial_profile_over_http(client):
    parent = _http_create(client, "Parent")
    child = _http_create(client, "Child")
    cid = child["id"]
    client.post(f"{PREFIX}/{cid}/parent", headers=_auth(), json={"parent_account_id": parent["id"]})
    client.post(f"{PREFIX}/{cid}/payer", headers=_auth(), json={"payer_account_id": parent["id"]})
    profile = client.get(f"{PREFIX}/{child['id']}/profile", headers=_auth()).json()
    assert profile["parent_display_name"] == "Parent"
    assert profile["payer_display_name"] == "Parent"
    assert profile["invoiced_account_id"] == parent["id"]
    assert profile["is_self_paying"] is False and profile["is_root"] is False


def test_contacts_addresses_preferences_credit_over_http(client):
    created = _http_create(client)
    cid = created["id"]
    client.patch(
        f"{PREFIX}/{cid}/contacts",
        headers=_auth(),
        json={
            "contacts": [
                {"role": "billing", "email": "ap@acme.test"},
                {"role": "technical", "email": "eng@acme.test"},
            ]
        },
    )
    client.patch(
        f"{PREFIX}/{cid}/addresses",
        headers=_auth(),
        json={
            "addresses": [
                {"kind": "billing", "line1": "1 MG Road", "city": "Bengaluru", "country": "IN"},
                {"kind": "shipping", "line1": "9 Anna Salai", "city": "Chennai", "country": "IN"},
            ]
        },
    )
    client.patch(
        f"{PREFIX}/{cid}/preferences",
        headers=_auth(),
        json={"invoice_delivery": "portal", "billing_day": 15, "payment_terms_days": 45},
    )
    credited = client.patch(
        f"{PREFIX}/{cid}/credit",
        headers=_auth(),
        json={
            "credit": {"credit_limit": {"minor": 500000, "currency": "INR"}, "risk_rating": "low"},
            "commercial_status": "watch",
        },
    ).json()
    assert {c["role"] for c in credited["contacts"]} == {"billing", "technical"}
    assert {a["kind"] for a in credited["addresses"]} == {"billing", "shipping"}
    assert credited["preferences"]["billing_day"] == 15
    assert credited["credit"]["credit_limit"] == {"minor": 500000, "currency": "INR"}
    assert credited["commercial_status"] == "watch"


def test_a_billing_day_beyond_28_is_rejected_at_the_wire(client):
    created = _http_create(client)
    response = client.patch(
        f"{PREFIX}/{created['id']}/preferences", headers=_auth(), json={"billing_day": 31}
    )
    assert response.status_code == 422


def test_notes_and_history_over_http(client):
    created = _http_create(client)
    noted = client.post(
        f"{PREFIX}/{created['id']}/notes", headers=_auth(), json={"body": "spoke to AP"}
    )
    assert noted.status_code == 201
    assert [n["body"] for n in noted.json()["notes"]] == ["spoke to AP"]
    history = client.get(f"{PREFIX}/{created['id']}/history", headers=_auth()).json()
    assert {h["action"] for h in history} == {
        "account.created",
        "account.activated",
        "account.note_added",
    }


def test_search_over_http(client):
    _http_create(client, "Acme Group")
    _http_create(client, "Beta Labs")
    body = client.get(f"{PREFIX}?limit=10", headers=_auth()).json()
    assert [a["display_name"] for a in body["accounts"]] == ["Acme Group", "Beta Labs"]
    assert body["total"] == 2
    filtered = client.get(f"{PREFIX}?text=beta", headers=_auth()).json()
    assert [a["display_name"] for a in filtered["accounts"]] == ["Beta Labs"]


def test_an_account_is_addressable_by_number_over_http(client):
    _http_create(client)
    response = client.get(f"{PREFIX}/BA-000001", headers=_auth())
    assert response.status_code == 200 and response.json()["account_number"] == "BA-000001"


def test_account_routes_require_identity_and_an_org(client):
    assert client.get(PREFIX, headers={}).status_code == 401
    assert client.get(PREFIX, headers=_auth(org_id="")).status_code == 401


def test_account_authorization_denial_is_403():
    with _client(authorizer=DenyAllAuthorizer()) as denied:
        response = denied.get(PREFIX, headers=_auth())
        assert response.status_code == 403
        assert "denied" in response.json()["error"]["message"]


def test_cross_org_isolation_over_http(client):
    created = _http_create(client)
    assert client.get(f"{PREFIX}/{created['id']}", headers=_auth(org_id="org_2")).status_code == 404


def test_the_organization_always_comes_from_the_token(client):
    """A body-supplied org must never influence what is written."""
    client.post(
        PREFIX,
        headers=_auth(org_id="org_1"),
        json={"display_name": "Acme", "currency": "INR", "org_id": "org_hacker"},
    )
    assert client.get(f"{PREFIX}/BA-000001", headers=_auth(org_id="org_hacker")).status_code == 404
    assert client.get(f"{PREFIX}/BA-000001", headers=_auth(org_id="org_1")).status_code == 200


def test_there_is_no_delete_verb_on_any_relationship_or_history_path(client):
    schema = client.get("/openapi.json").json()
    for path, verbs in schema["paths"].items():
        tail = ("/history", "/parent", "/payer", "/notes")
        if "/billing-accounts/" in path and path.endswith(tail):
            assert "delete" not in verbs, path


def test_account_openapi_is_tagged_and_documents_errors(client):
    schema = client.get("/openapi.json").json()
    create = schema["paths"][PREFIX]["post"]
    assert create["tags"] == ["accounts"]
    assert {"401", "403", "404", "409", "422"} <= set(create["responses"])


def test_the_m9_5_account_surface_is_untouched(client):
    """`/accounts` is M9.5's and is reused unchanged, not moved or shadowed."""
    paths = set(client.get("/openapi.json").json()["paths"])
    assert "/v1/billing/accounts" in paths
    assert "/v1/billing/accounts/{account_id}" in paths
    assert "/v1/billing/billing-accounts" in paths
    assert "/v1/billing/billing-accounts/{identifier}" in paths


# ── 9. Studio ───────────────────────────────────────────────────────────────────


STUDIO = BILLING / "customer" / "studio" / "accounts.html"


def test_accounts_studio_is_served(client):
    response = client.get("/studio/accounts.html")
    assert response.status_code == 200
    assert "Billing Accounts" in response.text


def test_accounts_studio_is_self_contained():
    html = STUDIO.read_text()
    for forbidden in ("http://", "https://", "cdn.", "<script src", "@import"):
        assert forbidden not in html, forbidden


def test_accounts_studio_has_a_screen_for_every_required_area():
    tabs = dict(_studio_tabs(STUDIO.read_text()))
    for required in (
        "list",
        "details",
        "hierarchy",
        "profile",
        "contacts",
        "addresses",
        "preferences",
        "history",
        "search",
    ):
        assert required in tabs, required
    assert len(tabs) == 9


def test_accounts_studio_owns_no_state_and_walks_no_graph():
    """ADR-0021 taken literally: the Hierarchy Viewer draws the tree the server resolved."""
    html = STUDIO.read_text()
    assert "localStorage" not in html and "sessionStorage" not in html
    assert "invoiced_account_id" in html
    for forbidden in ("parent_account_id ===", "function findCycle", "while (parent"):
        assert forbidden not in html, forbidden


def _studio_tabs(html: str) -> list[tuple[str, str]]:
    import re

    start = html.index("const TABS = [")
    body = html[start : html.index("];", start)]
    return [(m.group(1), m.group(2)) for m in re.finditer(r'\["(\w+)",\s*"([^"]+)"\]', body)]


# ── 10. architecture compliance ─────────────────────────────────────────────────


ACCOUNT_MODULES = (
    BILLING / "foundation" / "commercial_accounts.py",
    BILLING / "foundation" / "account_rules.py",
    BILLING / "foundation" / "account_factories.py",
    BILLING / "application" / "billing_accounts.py",
    BILLING / "persistence" / "account_models.py",
    BILLING / "persistence" / "account_repositories.py",
    BILLING / "customer" / "account_schemas.py",
    BILLING / "customer" / "routers" / "billing_accounts.py",
)


def _code_only(path: Path) -> str:
    """Source with docstrings stripped. These scans are about what the domain *is*, and prose
    explaining why it holds no subscription must not read as holding one."""
    tree = ast.parse(path.read_text())
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            if (
                node.body
                and isinstance(node.body[0], ast.Expr)
                and isinstance(node.body[0].value, ast.Constant)
                and isinstance(node.body[0].value.value, str)
            ):
                node.body = node.body[1:] or [ast.Pass()]
    return ast.unparse(tree)


def test_accounts_own_no_subscription_pricing_usage_or_payment():
    """The boundary this domain rests on: it says *who* has the commercial relationship, never
    what they bought or what they owe."""
    for path in ACCOUNT_MODULES:
        source = _code_only(path)
        for forbidden in (
            "Subscription(",
            "SubscriptionItem",
            "Invoice(",
            "InvoiceLine",
            "UsageRecord",
            "UsageAggregate",
            "LedgerEntry",
            "Payment(",
            "PricingBook",
            "PriceBookVersion",
            "ResolvedPrice",
            "CatalogVersion",
            "Product(",
        ):
            assert forbidden not in source, (path.name, forbidden)


def test_accounts_compute_no_balance_and_no_charge():
    """A credit limit is a policy figure. Nothing here knows what a customer currently owes —
    the arithmetic that would compute it lives in the Ledger, and none of it is here."""
    for path in ACCOUNT_MODULES:
        source = _code_only(path)
        for forbidden in (".times(", ".allocate(", ".scaled(", "balance =", "outstanding ="):
            assert forbidden not in source, (path.name, forbidden)


def test_the_billing_hierarchy_never_touches_epic_8_tenancy():
    """A reseller hierarchy must require no change to organization tenancy (ADR-0023 §5.2).
    `org_id` is a string this domain scopes on and never dereferences."""
    for path in ACCOUNT_MODULES:
        source = _code_only(path)
        for forbidden in (
            "from api",
            "import api",
            "Organization",
            "Workspace",
            "Project",
            "workspace_id",
            "project_id",
        ):
            assert forbidden not in source, (path.name, forbidden)


def test_account_foundation_stays_pure():
    for path in (
        BILLING / "foundation" / "commercial_accounts.py",
        BILLING / "foundation" / "account_rules.py",
        BILLING / "foundation" / "account_factories.py",
    ):
        source = path.read_text().lower()
        for forbidden in ("sqlmodel", "sqlalchemy", "fastapi", "pydantic", "session.", "select("):
            assert forbidden not in source, (path.name, forbidden)


def test_account_layers_import_only_downward():
    def imports(path: Path) -> set[str]:
        tree = ast.parse(path.read_text())
        found: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                found.update(a.name.split(".")[0] for a in node.names)
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                found.add(node.module.split(".")[0])
        return found

    allowed = {
        "core",
        "billing",
        "__future__",
        "abc",
        "dataclasses",
        "enum",
        "typing",
        "collections",
    }
    for path in (
        BILLING / "foundation" / "commercial_accounts.py",
        BILLING / "foundation" / "account_rules.py",
        BILLING / "foundation" / "account_factories.py",
    ):
        assert imports(path) <= allowed, (path.name, imports(path))

    app_imports = imports(BILLING / "application" / "billing_accounts.py")
    assert "sqlmodel" not in app_imports and "fastapi" not in app_imports


def test_account_use_cases_contain_no_rule():
    """Orchestration only: no validation, no cycle detection, no lifecycle table, no
    filtering — all of it is called, none of it is re-implemented."""
    source = (BILLING / "application" / "billing_accounts.py").read_text()
    for forbidden in (
        "def validate_",
        "def find_cycle",
        "def find_loop",
        "ACCOUNT_LIFECYCLE",
        "def ancestors",
        "MAX_HIERARCHY_DEPTH",
    ):
        assert forbidden not in source, forbidden


def test_account_controllers_stay_transport_only():
    tree = ast.parse((BILLING / "customer" / "routers" / "billing_accounts.py").read_text())
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name != "_account_response":
            body = ast.unparse(node)
            assert "commands.dispatch" in body or "queries.dispatch" in body, node.name
            for forbidden in (
                "repositories",
                "session",
                ".commit()",
                "uow.",
                "validate",
                "find_cycle",
            ):
                assert forbidden not in body, (node.name, forbidden)


def test_repositories_resolve_no_hierarchy_and_choose_no_payer():
    source = _code_only(BILLING / "persistence" / "account_repositories.py")
    for forbidden in (
        "AccountHierarchyResolver",
        "PayerResolver",
        "ancestors",
        "descendants",
        "find_cycle",
        "ultimate_payer",
    ):
        assert forbidden not in source, forbidden


def test_command_and_query_coverage_is_complete():
    from billing.application import ALL_ACCOUNT_COMMANDS, ALL_ACCOUNT_QUERIES

    wired = build_persistent_billing_application(
        ids=SequentialIdGenerator(), clock=FixedClock(), authorizer=AllowAllAuthorizer()
    )
    for message in (*ALL_ACCOUNT_COMMANDS, *ALL_ACCOUNT_QUERIES):
        bus = (
            wired.application.commands
            if message in ALL_ACCOUNT_COMMANDS
            else wired.application.queries
        )
        assert message in bus._handlers, message.__name__  # noqa: SLF001
    assert len(ALL_ACCOUNT_COMMANDS) == 14
    assert len(ALL_ACCOUNT_QUERIES) == 5
    wired.database.close()


def test_billing_accounts_are_additive_and_optional():
    """Without account repositories the Application is exactly what M9.7 built."""
    from billing.application import BillingRepositories
    from billing.application.context import BillingContext

    fields = {f.name for f in BillingRepositories.__dataclass_fields__.values()}
    assert "commercial_accounts" in fields
    assert BillingRepositories.__dataclass_fields__["commercial_accounts"].default is None
    assert BillingContext.__dataclass_fields__["commercial_accounts"].default is None


def test_frozen_lower_layers_kept_their_surfaces():
    from billing import application, foundation, persistence

    for name in (
        "BillingFactory",
        "Money",
        "Product",
        "CatalogVersion",
        "PricingBook",
        "BillingAccount",
    ):
        assert name in foundation.__all__
    for name in ("BillingApplication", "CreateProduct", "CreatePriceBook", "OpenBillingAccount"):
        assert name in application.__all__
    for name in ("BillingDatabase", "build_persistent_billing_application"):
        assert name in persistence.__all__
    assert len(foundation.__all__) > 282  # M9.7 left it at 282; growth only


def test_the_catalog_and_pricing_boundaries_still_hold():
    """M9.6's and M9.7's boundaries must survive the arrival of a third domain."""
    for path in (BILLING / "foundation" / "products.py", BILLING / "application" / "catalog.py"):
        code = _code_only(path)
        for forbidden in ("Money", "Currency", "CreditProfile"):
            assert forbidden not in code, (path.name, forbidden)
    for path in (BILLING / "foundation" / "price_books.py", BILLING / "application" / "pricing.py"):
        code = _code_only(path)
        for forbidden in (".times(", ".allocate(", "CommercialAccount"):
            assert forbidden not in code, (path.name, forbidden)


def test_billing_is_still_absent_from_the_runtime_composition_root():
    assert "billing" not in (PLATFORM / "bootstrap.py").read_text().lower()
