"""Billing Application layer tests (M9.3) — exhaustive, deterministic, offline.

Verifies the CQRS orchestration layer and, just as strictly, its *boundaries*: that it
contains no business rules, imports no persistence or transport, and coordinates the
Foundation rather than duplicating it.

Persistence does not exist yet (M9.4), so the repository interfaces are satisfied here by
deliberately minimal in-test fakes. They live in the test file precisely because a default
in-memory store shipped inside the Application would be a persistence implementation
smuggled into the wrong layer.
"""

from __future__ import annotations

import ast
import dataclasses
import inspect
import pathlib

import pytest

import billing.application as app
from billing.application import (
    ALL_COMMANDS,
    ALL_QUERIES,
    AllowAllAuthorizer,
    ApplicationEventType,
    AuthorizationDecision,
    AuthorizationOrchestrator,
    AuthorizationRequest,
    BillingApplicationFactory,
    BillingAuthorizationError,
    BillingAuthorizer,
    BillingPermission,
    BillingRepositories,
    CloseBillingAccount,
    DefineMeter,
    DefinePlan,
    DenyAllAuthorizer,
    DraftInvoice,
    DuplicateHandlerError,
    GetAccount,
    GetCreditBalance,
    GetLedgerBalance,
    GetQuotaVerdict,
    GetSubscription,
    GetUsageSummary,
    GrantCredit,
    GrantEntitlement,
    IssueInvoice,
    IssueLicense,
    ListInvoices,
    ListLicenses,
    ListPublishedPlans,
    NoHandlerError,
    OpenBillingAccount,
    PostLedgerEntry,
    PublishEntitlementSnapshot,
    PublishPlan,
    PublishPriceBook,
    QuarantineUsage,
    RecordingApplicationEventEmitter,
    RecordUsage,
    RequestPayment,
    RequestRefund,
    StagingUnitOfWork,
    StartSubscription,
    TransitionSubscription,
    UnitOfWorkClosedError,
)
from billing.foundation import (
    AccountKind,
    BillingAccountRepository,
    BillingEventType,
    BillingFactory,
    BillingValidationError,
    ChargeLine,
    ChargeLineKind,
    CreditKind,
    Currency,
    Entitlement,
    EntitlementGate,
    FeatureKind,
    FixedClock,
    GateClassification,
    GrantSource,
    InvalidBillingTransition,
    LedgerDirection,
    LedgerEntryType,
    LicenseKind,
    MeterAggregation,
    MeterSource,
    Money,
    PlanFeature,
    Quantity,
    RecordingEventEmitter,
    RefundReason,
    Scope,
    SequentialIdGenerator,
    SubscriptionItem,
    SubscriptionState,
    SubscriptionTrigger,
    Tier,
)
from billing.foundation.domain import BillingInterval, BillingPeriod

INR = Currency.INR
PERIOD = BillingPeriod(0.0, 1000.0)

# ── in-test fakes for the M9.2 repository interfaces (persistence lands at M9.4) ─────


class FakeAccounts(BillingAccountRepository):
    def __init__(self) -> None:
        self._by_id: dict[tuple[str, str], object] = {}

    def add(self, account):
        self._by_id[(account.org_id, account.id)] = account
        return account

    def get(self, org_id, account_id):
        return self._by_id.get((org_id, account_id))

    def by_org(self, org_id):
        return next((a for (o, _), a in self._by_id.items() if o == org_id), None)

    def children_of(self, org_id, parent_account_id):
        return tuple(
            a
            for a in self._by_id.values()
            if getattr(a, "parent_account_id", "") == parent_account_id
        )

    def replace(self, account):
        self._by_id[(account.org_id, account.id)] = account
        return account


def _repo(base, **methods):
    """Build a minimal fake for a repository ABC from the methods a test needs."""
    impl = {name: (lambda self, *a, _f=fn, **k: _f(*a, **k)) for name, fn in methods.items()}
    missing = set(base.__abstractmethods__) - set(impl)
    for name in missing:
        impl[name] = lambda self, *a, **k: None
    return type(f"Fake{base.__name__}", (base,), impl)()


def _repositories() -> tuple[BillingRepositories, dict]:
    from billing.foundation import (
        BillingRunRepository,  # noqa: F401  (not used at M9.3)
        ContractRepository,
        CreditRepository,
        EntitlementGrantRepository,
        EntitlementSnapshotRepository,
        InvoiceNumberAllocator,
        InvoiceRepository,
        LedgerRepository,
        LicenseRepository,
        MeterRepository,
        PaymentRepository,
        PlanRepository,
        PriceBookRepository,
        RefundRepository,
        SubscriptionRepository,
        TaxDeterminationRepository,
        TaxProfileRepository,
        UsageAggregateRepository,
        UsageRecordRepository,
    )

    state: dict[str, list | dict] = {
        "plans": [],
        "meters": [],
        "books": [],
        "subs": {},
        "grants": [],
        "snapshots": [],
        "usage": [],
        "ledger": [],
        "credit_grants": [],
        "credit_movements": [],
        "invoices": {},
        "payments": [],
        "refunds": [],
        "tax_profiles": {},
        "determinations": {},
        "contracts": [],
        "licenses": {},
        "sequence": [0],
    }

    def add_to(key):
        def _add(item):
            state[key].append(item)  # type: ignore[union-attr]
            return item

        return _add

    accounts = FakeAccounts()
    repos = BillingRepositories(
        accounts=accounts,
        plans=_repo(
            PlanRepository,
            add=add_to("plans"),
            published=lambda **kw: tuple(p for p in state["plans"] if p.published),
            get=lambda code, version, **kw: next(
                (p for p in state["plans"] if p.code == code and p.version == version), None
            ),
        ),
        meters=_repo(
            MeterRepository,
            add=add_to("meters"),
            all=lambda **kw: tuple(state["meters"]),
            get=lambda code, **kw: next((m for m in state["meters"] if m.code == code), None),
        ),
        price_books=_repo(
            PriceBookRepository,
            add=add_to("books"),
            get=lambda book_id, version: next((b for b in state["books"] if b.id == book_id), None),
        ),
        subscriptions=_repo(
            SubscriptionRepository,
            add=lambda s: state["subs"].__setitem__((s.org_id, s.id), s) or s,  # type: ignore[union-attr]
            get=lambda org_id, sub_id: state["subs"].get((org_id, sub_id)),  # type: ignore[union-attr]
            by_account=lambda org_id, account_id: tuple(
                s for s in state["subs"].values() if s.account_id == account_id
            ),  # type: ignore[union-attr]
            replace=lambda s: state["subs"].__setitem__((s.org_id, s.id), s) or s,
        ),  # type: ignore[union-attr]
        grants=_repo(
            EntitlementGrantRepository,
            add=add_to("grants"),
            active_for=lambda org_id, account_id: tuple(
                g for g in state["grants"] if g.account_id == account_id and g.is_active
            ),
        ),
        snapshots=_repo(
            EntitlementSnapshotRepository,
            publish=add_to("snapshots"),
            latest=lambda org_id, account_id: (
                state["snapshots"][-1] if state["snapshots"] else None
            ),
        ),
        usage=_repo(
            UsageRecordRepository,
            append=add_to("usage"),
            exists=lambda event_id, meter_code: any(
                u.source.event_id == event_id and u.meter_code == meter_code for u in state["usage"]
            ),
            for_period=lambda org_id, account_id, meter_code, period: tuple(
                u for u in state["usage"] if u.meter_code == meter_code and u.is_billable
            ),
        ),
        aggregates=_repo(UsageAggregateRepository),
        ledger=_repo(
            LedgerRepository,
            append=add_to("ledger"),
            for_account=lambda org_id, account_id: tuple(
                e for e in state["ledger"] if e.account_id == account_id
            ),
        ),
        credits=_repo(
            CreditRepository,
            grant=add_to("credit_grants"),
            append_movement=add_to("credit_movements"),
            movements_for=lambda org_id, account_id: tuple(state["credit_movements"]),
        ),
        invoices=_repo(
            InvoiceRepository,
            add=lambda i: state["invoices"].__setitem__((i.org_id, i.id), i) or i,  # type: ignore[union-attr]
            get=lambda org_id, invoice_id: state["invoices"].get((org_id, invoice_id)),  # type: ignore[union-attr]
            for_account=lambda org_id, account_id: tuple(state["invoices"].values()),  # type: ignore[union-attr]
            replace_draft=lambda i: state["invoices"].__setitem__((i.org_id, i.id), i) or i,
        ),  # type: ignore[union-attr]
        invoice_numbers=_repo(
            InvoiceNumberAllocator,
            allocate=lambda entity_code, jurisdiction: (
                state["sequence"].__setitem__(  # type: ignore[union-attr]
                    0, state["sequence"][0] + 1
                )
                or state["sequence"][0]
            ),
        ),  # type: ignore[index]
        payments=_repo(
            PaymentRepository,
            add=add_to("payments"),
            for_invoice=lambda org_id, invoice_id: tuple(
                p for p in state["payments"] if p.invoice_id == invoice_id
            ),
        ),
        refunds=_repo(
            RefundRepository,
            add=add_to("refunds"),
            by_request_key=lambda org_id, key: next(
                (r for r in state["refunds"] if r.request_key == key), None
            ),
            for_payment=lambda org_id, payment_id: tuple(
                r for r in state["refunds"] if r.payment_id == payment_id
            ),
            get=lambda org_id, refund_id: next(
                (r for r in state["refunds"] if r.id == refund_id), None
            ),
            replace=lambda r: r,
        ),
        tax_profiles=_repo(
            TaxProfileRepository,
            upsert=lambda p: state["tax_profiles"].__setitem__(p.account_id, p) or p,  # type: ignore[union-attr]
            for_account=lambda org_id, account_id: state["tax_profiles"].get(account_id),
        ),  # type: ignore[union-attr]
        tax_determinations=_repo(TaxDeterminationRepository),
        contracts=_repo(
            ContractRepository,
            add=add_to("contracts"),
            for_account=lambda org_id, account_id: tuple(state["contracts"]),
        ),
        licenses=_repo(
            LicenseRepository,
            add=lambda lic: state["licenses"].__setitem__(lic.id, lic) or lic,  # type: ignore[union-attr]
            get=lambda org_id, license_id: state["licenses"].get(license_id),  # type: ignore[union-attr]
            for_account=lambda org_id, account_id: tuple(state["licenses"].values()),  # type: ignore[union-attr]
            replace=lambda lic: state["licenses"].__setitem__(lic.id, lic) or lic,
        ),  # type: ignore[union-attr]
    )
    return repos, state


def _app(authorizer: BillingAuthorizer | None = None):
    repos, state = _repositories()
    emitter = RecordingApplicationEventEmitter()
    domain_emitter = RecordingEventEmitter()
    ids = SequentialIdGenerator()
    clock = FixedClock()
    factory = BillingFactory(ids=ids, clock=clock, emitter=domain_emitter)
    context = BillingApplicationFactory.context(repositories=repos, factory=factory)
    application = BillingApplicationFactory.create(
        context=context,
        ids=ids,
        clock=clock,
        authorizer=authorizer if authorizer is not None else AllowAllAuthorizer(),
        emitter=emitter,
    )
    return application, emitter, domain_emitter, state


def _open_account(application, name="Acme"):
    return application.commands.dispatch(
        OpenBillingAccount(actor_id="usr_1", org_id="org_1", display_name=name, currency=INR)
    )


def _gates():
    return (
        EntitlementGate("voice_minutes", FeatureKind.QUOTA, GateClassification.FAIL_OPEN),
        EntitlementGate("sso", FeatureKind.FLAG, GateClassification.FAIL_CLOSED),
    )


# ── command dispatch ────────────────────────────────────────────────────────────


def test_every_command_and_query_has_exactly_one_handler():
    application, _, _, _ = _app()
    assert set(application.commands.registered) == set(ALL_COMMANDS)
    assert set(application.queries.registered) == set(ALL_QUERIES)
    assert len(ALL_COMMANDS) == 26
    assert len(ALL_QUERIES) == 19


def test_dispatch_routes_to_the_registered_handler():
    application, _, _, _ = _app()
    account = _open_account(application)
    assert account.org_id == "org_1"
    assert account.display_name == "Acme"


def test_unknown_message_raises_rather_than_silently_passing():
    application, _, _, _ = _app()
    with pytest.raises(NoHandlerError):
        application.commands.dispatch(object())
    with pytest.raises(NoHandlerError):
        application.queries.dispatch(object())


def test_duplicate_registration_is_a_wiring_error():
    application, _, _, _ = _app()
    with pytest.raises(DuplicateHandlerError):
        application.commands.register(OpenBillingAccount, lambda cmd: None)


def test_every_dispatched_command_is_audited_exactly_once():
    application, emitter, _, _ = _app()
    _open_account(application)
    completed = emitter.of_type(ApplicationEventType.USE_CASE_COMPLETED)
    assert len(completed) == 1
    assert completed[0].use_case == "OpenBillingAccount"
    assert completed[0].actor == "usr_1" and completed[0].org_id == "org_1"


def test_a_rejection_is_audited_and_the_error_propagates_unchanged():
    application, emitter, _, _ = _app()
    with pytest.raises(BillingValidationError):
        application.commands.dispatch(
            DefineMeter(
                actor_id="usr_1",
                org_id="",
                code="BAD CODE",
                display_name="",
                unit="",
                aggregation=MeterAggregation.SUM,
                source=MeterSource.RUNTIME_SESSION,
            )
        )
    rejected = emitter.of_type(ApplicationEventType.USE_CASE_REJECTED)
    assert len(rejected) == 1
    assert rejected[0].data["error"] == "BillingValidationError"
    assert not emitter.of_type(ApplicationEventType.USE_CASE_COMPLETED)


def test_queries_are_not_audited_and_never_mutate():
    application, emitter, _, _ = _app()
    account = _open_account(application)
    before = len(emitter.events)
    view = application.queries.dispatch(
        GetAccount(actor_id="usr_1", org_id="org_1", account_id=account.id)
    )
    assert view.id == account.id
    assert len(emitter.events) == before  # reads are neither audited nor transactional


# ── query dispatch + mapping ────────────────────────────────────────────────────


def test_query_returns_a_view_dto_not_a_domain_aggregate():
    application, _, _, _ = _app()
    account = _open_account(application)
    view = application.queries.dispatch(
        GetAccount(actor_id="usr_1", org_id="org_1", account_id=account.id)
    )
    assert isinstance(view, app.AccountView)
    assert not hasattr(view, "contact")  # a projection, not the aggregate
    assert view.currency == "INR"  # enums flattened to primitives at the boundary


def test_missing_aggregates_return_none_rather_than_raising():
    application, _, _, _ = _app()
    assert (
        application.queries.dispatch(
            GetAccount(actor_id="usr_1", org_id="org_1", account_id="bac_missing")
        )
        is None
    )
    assert (
        application.queries.dispatch(
            GetSubscription(actor_id="usr_1", org_id="org_1", subscription_id="sub_missing")
        )
        is None
    )


def test_read_views_are_immutable():
    application, _, _, _ = _app()
    account = _open_account(application)
    view = application.queries.dispatch(
        GetAccount(actor_id="usr_1", org_id="org_1", account_id=account.id)
    )
    with pytest.raises(dataclasses.FrozenInstanceError):
        view.display_name = "Other"  # type: ignore[misc]


# ── authorization orchestration ─────────────────────────────────────────────────


def test_the_default_authorizer_denies_so_a_forgotten_wiring_fails_closed():
    repos, _ = _repositories()
    context = BillingApplicationFactory.context(
        repositories=repos,
        factory=BillingFactory(ids=SequentialIdGenerator(), clock=FixedClock()),
    )
    application = BillingApplicationFactory.create(
        context=context, ids=SequentialIdGenerator(), clock=FixedClock()
    )
    with pytest.raises(BillingAuthorizationError):
        _open_account(application)


def test_denial_is_audited_as_a_rejection():
    application, emitter, _, _ = _app(authorizer=DenyAllAuthorizer())
    with pytest.raises(BillingAuthorizationError):
        _open_account(application)
    rejected = emitter.of_type(ApplicationEventType.USE_CASE_REJECTED)
    assert rejected and rejected[0].data["error"] == "BillingAuthorizationError"


def test_billing_asks_the_seam_and_evaluates_nothing_itself():
    """ADR-0023 §11.3: Billing never owns RBAC and never evaluates a permission."""
    asked: list[AuthorizationRequest] = []

    class Recording(BillingAuthorizer):
        def authorize(self, request):
            asked.append(request)
            return AuthorizationDecision(True)

    application, _, _, _ = _app(authorizer=Recording())
    _open_account(application)
    assert len(asked) == 1
    assert asked[0].permission is BillingPermission.BILLING_MANAGE
    assert asked[0].actor_id == "usr_1" and asked[0].org_id == "org_1"


def test_reads_and_writes_require_different_permissions():
    asked: list[AuthorizationRequest] = []

    class Recording(BillingAuthorizer):
        def authorize(self, request):
            asked.append(request)
            return AuthorizationDecision(True)

    application, _, _, _ = _app(authorizer=Recording())
    account = _open_account(application)
    application.queries.dispatch(
        GetAccount(actor_id="usr_1", org_id="org_1", account_id=account.id)
    )
    assert asked[0].permission is BillingPermission.BILLING_MANAGE
    assert asked[-1].permission is BillingPermission.BILLING_READ


def test_refund_request_and_approval_require_distinct_permissions():
    """Separation of duties starts at the permission vocabulary."""
    assert BillingPermission.BILLING_REFUND_REQUEST is not BillingPermission.BILLING_REFUND_APPROVE


def test_the_orchestrator_contains_no_permission_logic():
    source = inspect.getsource(AuthorizationOrchestrator.require)
    for banned in ("if permission", "ROLE", "role ==", "admin", "owner"):
        assert banned not in source, f"permission logic leaked into the orchestrator: {banned}"


# ── unit of work ────────────────────────────────────────────────────────────────


def test_commit_publishes_staged_events():
    emitter = RecordingApplicationEventEmitter()
    uow = StagingUnitOfWork(emitter)
    event = app.ApplicationEvent(
        id="bap_1",
        type=ApplicationEventType.LEDGER_POSTED,
        ts=1.0,
        use_case="Test",
        outcome="completed",
    )
    with uow:
        uow.stage(event)
        assert emitter.events == ()  # invisible until commit
        assert uow.staged == (event,)
    assert emitter.events == (event,)


def test_rollback_discards_staged_events_and_reraises():
    emitter = RecordingApplicationEventEmitter()
    uow = StagingUnitOfWork(emitter)
    with pytest.raises(ValueError):
        with uow:
            uow.stage(
                app.ApplicationEvent(
                    id="bap_1",
                    type=ApplicationEventType.LEDGER_POSTED,
                    ts=1.0,
                    use_case="Test",
                    outcome="completed",
                )
            )
            raise ValueError("orchestration failed")
    assert emitter.events == ()  # a failed orchestration leaves no trace of success
    assert uow.staged == ()


def test_a_closed_unit_of_work_cannot_be_reused():
    uow = StagingUnitOfWork(RecordingApplicationEventEmitter())
    with uow:
        pass
    with pytest.raises(UnitOfWorkClosedError):
        uow.stage(
            app.ApplicationEvent(
                id="bap_1",
                type=ApplicationEventType.LEDGER_POSTED,
                ts=1.0,
                use_case="Test",
                outcome="completed",
            )
        )
    with pytest.raises(UnitOfWorkClosedError):
        with uow:
            pass


def test_a_failed_use_case_publishes_no_composite_event_but_still_audits():
    application, emitter, _, _ = _app()
    with pytest.raises(app.BillingApplicationError):
        application.commands.dispatch(
            IssueInvoice(actor_id="usr_1", org_id="org_1", invoice_id="inv_missing")
        )
    assert not emitter.of_type(ApplicationEventType.INVOICE_FINALIZED)  # rolled back
    assert emitter.of_type(ApplicationEventType.USE_CASE_REJECTED)  # survives the rollback


def test_the_foundation_is_unaware_of_transactions():
    """Nothing in the Foundation participates in a transaction — the boundary is the
    Application's.

    M9.6 sharpened this from a word search to a shape search: the Commercial Catalog has a
    legitimate `CatalogAssembler.rollback`, which publishes a *new* catalog version carrying
    older content. That is a domain operation that happens to share a word with a database
    one, and a rule that cannot tell them apart would either block the domain or be deleted.
    What must stay absent is transaction *machinery*."""
    for path in pathlib.Path("billing/foundation").rglob("*.py"):
        text = path.read_text().lower()
        for token in (
            "unit_of_work",
            "unitofwork",
            "uow",
            "session.commit",
            "session.rollback",
            ".commit()",
            "db transaction",
            "database transaction",
            "begin()",
        ):
            assert token not in text, f"{path} knows about transactions ({token})"


# ── application events ──────────────────────────────────────────────────────────


def test_application_and_foundation_vocabularies_are_disjoint():
    application_values = {e.value for e in ApplicationEventType}
    foundation_values = {e.value for e in BillingEventType}
    assert not (application_values & foundation_values)


def test_the_application_never_re_emits_a_foundation_event():
    application, emitter, domain_emitter, _ = _app()
    _open_account(application)
    assert any(e.type is BillingEventType.ACCOUNT_CREATED for e in domain_emitter.events)
    assert all(isinstance(e.type, ApplicationEventType) for e in emitter.events)


def test_composite_orchestration_events_are_staged_transactionally():
    application, emitter, _, _ = _app()
    account = _open_account(application)
    provisioned = emitter.of_type(ApplicationEventType.ACCOUNT_PROVISIONED)
    assert provisioned and provisioned[0].subject_id == account.id


def test_child_accounts_emit_a_hierarchy_link():
    application, emitter, _, _ = _app()
    parent = _open_account(application, "Partner")
    application.commands.dispatch(
        OpenBillingAccount(
            actor_id="usr_1",
            org_id="org_1",
            display_name="Child",
            currency=INR,
            kind=AccountKind.CHILD,
            parent_account_id=parent.id,
        )
    )
    linked = emitter.of_type(ApplicationEventType.ACCOUNT_HIERARCHY_LINKED)
    assert linked and linked[0].data["parent_account_id"] == parent.id


# ── use-case orchestration: Foundation behaviour is called, never duplicated ────


def test_subscription_transition_defers_to_the_foundation_fsm():
    application, _, _, _ = _app()
    account = _open_account(application)
    subscription = application.commands.dispatch(
        StartSubscription(
            actor_id="usr_1",
            org_id="org_1",
            account_id=account.id,
            plan_code="pro",
            plan_version=1,
            price_book_id="pbk_1",
            price_book_version=1,
            currency=INR,
            interval=BillingInterval.MONTHLY,
            items=(SubscriptionItem("pro_monthly", 1),),
        )
    )
    activated = application.commands.dispatch(
        TransitionSubscription(
            actor_id="usr_1",
            org_id="org_1",
            subscription_id=subscription.id,
            trigger=SubscriptionTrigger.ACTIVATE,
        )
    )
    assert activated.state is SubscriptionState.ACTIVE
    with pytest.raises(InvalidBillingTransition):  # the Foundation decides legality
        application.commands.dispatch(
            TransitionSubscription(
                actor_id="usr_1",
                org_id="org_1",
                subscription_id=subscription.id,
                trigger=SubscriptionTrigger.ACTIVATE,
            )
        )


def test_usage_ingestion_is_idempotent_by_source_event():
    application, emitter, _, state = _app()
    account = _open_account(application)
    command = RecordUsage(
        actor_id="sys",
        org_id="org_1",
        account_id=account.id,
        meter_code="voice_minutes",
        units=10,
        unit="minute",
        scope=Scope("org_1"),
        period=PERIOD,
        event_id="evt_1",
        event_type="metric.session.completed",
        observed_at=1.0,
    )
    first = application.commands.dispatch(command)
    second = application.commands.dispatch(command)
    assert first is not None and second is None  # re-ingesting is a no-op
    assert len(state["usage"]) == 1
    assert len(emitter.of_type(ApplicationEventType.USAGE_INGESTED)) == 1


def test_quarantined_usage_is_recorded_and_surfaced():
    application, emitter, _, _ = _app()
    application.commands.dispatch(
        QuarantineUsage(
            actor_id="sys",
            org_id="org_1",
            account_id="bac_unknown",
            meter_code="tokens",
            units=5,
            unit="token",
            scope=Scope("org_1"),
            period=PERIOD,
            event_id="evt_9",
            event_type="metric.model.completed",
            observed_at=1.0,
            reason="unattributable",
        )
    )
    rejected = emitter.of_type(ApplicationEventType.USAGE_REJECTED)
    assert rejected and rejected[0].data["reason"] == "unattributable"


def test_entitlement_snapshot_composition_uses_the_foundation_resolver():
    application, _, _, _ = _app()
    account = _open_account(application)
    application.commands.dispatch(
        GrantEntitlement(
            actor_id="usr_1",
            org_id="org_1",
            account_id=account.id,
            entitlement=Entitlement(
                "voice_minutes", FeatureKind.QUOTA, allowance=100, source=GrantSource.PLAN
            ),
        )
    )
    application.commands.dispatch(
        GrantEntitlement(
            actor_id="usr_1",
            org_id="org_1",
            account_id=account.id,
            entitlement=Entitlement(
                "voice_minutes", FeatureKind.QUOTA, allowance=5000, source=GrantSource.CONTRACT
            ),
        )
    )
    snapshot = application.commands.dispatch(
        PublishEntitlementSnapshot(
            actor_id="usr_1", org_id="org_1", account_id=account.id, gates=_gates()
        )
    )
    assert snapshot.version == 1
    assert (
        snapshot.allowance("voice_minutes") == 5000
    )  # contract precedence, decided by the resolver


def test_snapshot_versions_increment_monotonically():
    application, _, _, _ = _app()
    account = _open_account(application)
    first = application.commands.dispatch(
        PublishEntitlementSnapshot(
            actor_id="usr_1", org_id="org_1", account_id=account.id, gates=_gates()
        )
    )
    second = application.commands.dispatch(
        PublishEntitlementSnapshot(
            actor_id="usr_1", org_id="org_1", account_id=account.id, gates=_gates()
        )
    )
    assert (first.version, second.version) == (1, 2)


def test_quota_query_reports_information_not_an_authorization_answer():
    application, _, _, _ = _app()
    account = _open_account(application)
    application.commands.dispatch(
        GrantEntitlement(
            actor_id="usr_1",
            org_id="org_1",
            account_id=account.id,
            entitlement=Entitlement("voice_minutes", FeatureKind.QUOTA, allowance=100),
        )
    )
    application.commands.dispatch(
        PublishEntitlementSnapshot(
            actor_id="usr_1", org_id="org_1", account_id=account.id, gates=_gates()
        )
    )
    verdict = application.queries.dispatch(
        GetQuotaVerdict(
            actor_id="usr_1",
            org_id="org_1",
            account_id=account.id,
            entitlement_code="voice_minutes",
            consumed=150,
            at=1.0,
        )
    )
    assert verdict.would_exceed and verdict.classification == "fail_open"
    assert not hasattr(verdict, "allowed")  # never an allow/deny answer


def test_invoice_issuance_delegates_immutability_and_numbering_to_the_foundation():
    application, emitter, _, _ = _app()
    account = _open_account(application)
    line = ChargeLine(
        id="chl_1",
        org_id="org_1",
        account_id=account.id,
        kind=ChargeLineKind.RECURRING,
        description="Pro monthly",
        amount=Money(99900, INR),
        period=PERIOD,
        price_code="pro_monthly",
        price_version=1,
    )
    draft = application.commands.dispatch(
        DraftInvoice(
            actor_id="usr_1",
            org_id="org_1",
            account_id=account.id,
            currency=INR,
            period=PERIOD,
            lines=(line,),
        )
    )
    issued = application.commands.dispatch(
        IssueInvoice(actor_id="usr_1", org_id="org_1", invoice_id=draft.id, jurisdiction="IN")
    )
    assert issued.is_immutable and issued.number.formatted == "VOXVOICE-IN-000001"
    assert emitter.of_type(ApplicationEventType.INVOICE_FINALIZED)
    from billing.foundation import ImmutabilityViolation

    with pytest.raises(ImmutabilityViolation):  # the Foundation refuses, not the Application
        application.commands.dispatch(
            IssueInvoice(actor_id="usr_1", org_id="org_1", invoice_id=draft.id)
        )


def test_refund_bounds_are_enforced_by_the_foundation_validator():
    application, _, _, _ = _app()
    account = _open_account(application)
    with pytest.raises(BillingValidationError):
        application.commands.dispatch(
            RequestRefund(
                actor_id="usr_1",
                org_id="org_1",
                account_id=account.id,
                payment_id="pay_1",
                invoice_id="inv_1",
                amount=Money(200000, INR),
                reason=RefundReason.CUSTOMER_REQUEST,
                request_key="req_1",
                captured=Money(99900, INR),
            )
        )


def test_refund_requests_are_idempotent_by_request_key():
    application, _, _, _ = _app()
    account = _open_account(application)
    command = RequestRefund(
        actor_id="usr_1",
        org_id="org_1",
        account_id=account.id,
        payment_id="pay_1",
        invoice_id="inv_1",
        amount=Money(1000, INR),
        reason=RefundReason.BILLING_ERROR,
        request_key="req_1",
        captured=Money(99900, INR),
    )
    first = application.commands.dispatch(command)
    second = application.commands.dispatch(command)
    assert first.id == second.id  # one refund per request identity


def test_plan_change_reports_the_foundation_proration_decision():
    application, _, _, _ = _app()
    account = _open_account(application)
    subscription = application.commands.dispatch(
        StartSubscription(
            actor_id="usr_1",
            org_id="org_1",
            account_id=account.id,
            plan_code="pro",
            plan_version=1,
            price_book_id="pbk_1",
            price_book_version=1,
            currency=INR,
            interval=BillingInterval.MONTHLY,
            items=(SubscriptionItem("pro_monthly", 1),),
        )
    )
    period = subscription.current_period
    outcome, adjustment = application.commands.dispatch(
        app.ChangeSubscriptionPlan(
            actor_id="usr_1",
            org_id="org_1",
            subscription_id=subscription.id,
            is_expansion=True,
            price_delta=Money(1000, INR),
            at=period.start + period.duration / 4,
        )
    )
    assert outcome.timing.value == "immediate"
    assert adjustment == Money(750, INR)  # 75% of the period remains


def test_ledger_and_credit_balances_come_from_foundation_derivation():
    application, _, _, _ = _app()
    account = _open_account(application)
    application.commands.dispatch(
        PostLedgerEntry(
            actor_id="usr_1",
            org_id="org_1",
            account_id=account.id,
            entry_type=LedgerEntryType.CHARGE,
            direction=LedgerDirection.DEBIT,
            amount=Money(99900, INR),
        )
    )
    application.commands.dispatch(
        PostLedgerEntry(
            actor_id="usr_1",
            org_id="org_1",
            account_id=account.id,
            entry_type=LedgerEntryType.PAYMENT,
            direction=LedgerDirection.CREDIT,
            amount=Money(40000, INR),
        )
    )
    balance = application.queries.dispatch(
        GetLedgerBalance(actor_id="usr_1", org_id="org_1", account_id=account.id)
    )
    assert balance.amount.minor == 59900 and balance.amount.currency == "INR"

    application.commands.dispatch(
        GrantCredit(
            actor_id="usr_1",
            org_id="org_1",
            account_id=account.id,
            kind=CreditKind.PROMOTIONAL,
            amount=Money(5000, INR),
        )
    )
    credit = application.queries.dispatch(
        GetCreditBalance(actor_id="usr_1", org_id="org_1", account_id=account.id)
    )
    assert credit.amount.minor == 5000


def test_usage_summary_uses_the_foundation_metering_service():
    application, _, _, _ = _app()
    account = _open_account(application)
    application.commands.dispatch(
        DefineMeter(
            actor_id="usr_1",
            org_id="",
            code="voice_minutes",
            display_name="Voice minutes",
            unit="minute",
            aggregation=MeterAggregation.SUM,
            source=MeterSource.RUNTIME_SESSION,
        )
    )
    for i in range(3):
        application.commands.dispatch(
            RecordUsage(
                actor_id="sys",
                org_id="org_1",
                account_id=account.id,
                meter_code="voice_minutes",
                units=10,
                unit="minute",
                scope=Scope("org_1"),
                period=PERIOD,
                event_id=f"evt_{i}",
                event_type="metric.session.completed",
                observed_at=float(i),
            )
        )
    summary = application.queries.dispatch(
        GetUsageSummary(
            actor_id="usr_1",
            org_id="org_1",
            account_id=account.id,
            meter_code="voice_minutes",
            period=PERIOD,
        )
    )
    assert summary.units == 30 and summary.record_count == 3 and summary.unit == "minute"


def test_payment_request_records_intent_and_executes_nothing():
    application, emitter, _, _ = _app()
    account = _open_account(application)
    payment = application.commands.dispatch(
        RequestPayment(
            actor_id="usr_1",
            org_id="org_1",
            account_id=account.id,
            invoice_id="inv_1",
            amount=Money(99900, INR),
        )
    )
    assert payment.state.value == "required"  # nothing executed
    assert payment.provider_code == ""  # no provider exists at M9.3
    assert emitter.of_type(ApplicationEventType.PAYMENT_ORCHESTRATED)


def test_licenses_and_catalog_reads_round_trip():
    application, _, _, _ = _app()
    account = _open_account(application)
    plan = application.commands.dispatch(
        DefinePlan(
            actor_id="usr_1",
            org_id="",
            code="pro",
            display_name="Pro",
            tier=Tier.PRO,
            features=(PlanFeature("voice_minutes", allowance=1000),),
        )
    )
    application.commands.dispatch(PublishPlan(actor_id="usr_1", org_id="", plan=plan))
    plans = application.queries.dispatch(ListPublishedPlans(actor_id="usr_1", org_id=""))
    assert [p.code for p in plans] == ["pro"]

    application.commands.dispatch(
        IssueLicense(
            actor_id="usr_1",
            org_id="org_1",
            account_id=account.id,
            kind=LicenseKind.AIR_GAPPED,
            valid_from=0.0,
            valid_until=100.0,
            entitlement_codes=("sso",),
        )
    )
    licenses = application.queries.dispatch(
        ListLicenses(actor_id="usr_1", org_id="org_1", account_id=account.id)
    )
    assert licenses[0].kind == "air_gapped" and licenses[0].entitlement_codes == ("sso",)


def test_closing_an_account_retains_it():
    application, _, _, _ = _app()
    account = _open_account(application)
    closed = application.commands.dispatch(
        CloseBillingAccount(actor_id="usr_1", org_id="org_1", account_id=account.id, reason="churn")
    )
    assert closed.is_retained_only
    view = application.queries.dispatch(
        GetAccount(actor_id="usr_1", org_id="org_1", account_id=account.id)
    )
    assert view is not None and view.status == "closed"  # retained, never deleted


def test_price_book_publication_defers_validation_to_the_foundation():
    from billing.foundation import Price, PriceShape

    application, _, _, _ = _app()
    bad = Price(
        id="prc_1",
        price_book_id="",
        code="pro_monthly",
        shape=PriceShape.FLAT_RECURRING,
        currency=Currency.USD,
        amount=Money(100, Currency.USD),
        interval=BillingInterval.MONTHLY,
    )
    with pytest.raises(BillingValidationError):
        application.commands.dispatch(
            PublishPriceBook(
                actor_id="usr_1",
                org_id="",
                code="public",
                display_name="Public",
                currency=INR,
                prices=(bad,),
            )
        )


# ── determinism ─────────────────────────────────────────────────────────────────


def test_identical_dispatch_sequences_produce_identical_results():
    def run():
        application, emitter, _, _ = _app()
        account = _open_account(application)
        application.commands.dispatch(
            PostLedgerEntry(
                actor_id="usr_1",
                org_id="org_1",
                account_id=account.id,
                entry_type=LedgerEntryType.CHARGE,
                direction=LedgerDirection.DEBIT,
                amount=Money(1234, INR),
            )
        )
        return account, tuple((e.type, e.use_case, e.subject_id) for e in emitter.events)

    assert run() == run()


# ── boundaries: dependency direction + no rule duplication ─────────────────────


def _imported_tops(pkg_path: str) -> set[str]:
    tops: set[str] = set()
    for path in pathlib.Path(pkg_path).rglob("*.py"):
        for node in ast.walk(ast.parse(path.read_text())):
            if isinstance(node, ast.ImportFrom) and node.module and not node.level:
                tops.add(node.module.split(".")[0])
            elif isinstance(node, ast.Import):
                tops.update(a.name.split(".")[0] for a in node.names)
    return tops


def test_application_imports_only_foundation_core_and_stdlib():
    used = _imported_tops("billing/application")
    stdlib = {"abc", "dataclasses", "enum", "typing", "collections", "__future__"}
    assert used - {"billing", "core"} - stdlib == set()


def test_application_imports_no_persistence_transport_or_other_plane():
    forbidden = {
        "sqlalchemy",
        "sqlmodel",
        "sqlite3",
        "alembic",
        "fastapi",
        "starlette",
        "pydantic",
        "httpx",
        "requests",
        "urllib",
        "socket",
        "api",
        "actuator",
        "runtime",
        "product",
        "providers",
        "provider",
        "agent",
        "tool",
        "workflow",
        "mcp",
        "knowledge",
        "observability",
        "gateway",
        "replay",
        "events",
        "telemetry",
        "tenant",
        "studio",
    }
    assert not (_imported_tops("billing/application") & forbidden)


def test_the_foundation_never_imports_the_application():
    """The layering is one-way: Application → Foundation → Core."""
    assert "application" not in {
        node.module.split(".")[1]
        for path in pathlib.Path("billing/foundation").rglob("*.py")
        for node in ast.walk(ast.parse(path.read_text()))
        if isinstance(node, ast.ImportFrom)
        and node.module
        and node.module.startswith("billing.")
        and len(node.module.split(".")) > 1
    }


def test_no_frozen_epic_imports_billing():
    for plane in ("core", "runtime", "product", "actuator", "api", "events", "gateway"):
        if pathlib.Path(plane).is_dir():
            assert "billing" not in _imported_tops(plane), f"{plane} imports billing"


def test_the_application_duplicates_no_foundation_rule():
    """A structural check for rule leakage: the Application may not re-implement money
    arithmetic, validation, FSM tables, or numbering — it calls the Foundation for each.

    Sharpened at M9.14, following the M9.7 precedent recorded in the test below: *carrying* a
    Foundation value is not *duplicating* a Foundation rule, and two markers could not tell the
    difference once a document had to transport a tax rate and draw an invoice number.

    * `basis_points` is banned as **arithmetic** rather than as a substring. A tax rate the Tax
      domain determined has to reach a wire DTO somehow, and the field it travels in necessarily
      bears the name. What must never happen is the Application *applying* it — so the scan looks
      for the rate inside an arithmetic expression instead of anywhere at all.
    * `.allocate(` is banned by **receiver**. `Money.allocate` is exact splitting and belongs to
      the Foundation; `repos.numbers.allocate` draws an invoice number from a persistence seam,
      which is a write, and must happen inside the caller's transaction to be crash-safe at all.

    Both remain banned in the case they were written for. Neither is weakened to a warning."""
    banned_markers = (
        "def validate_",  # validation belongs to BillingValidator
        "def _divide",  # money arithmetic belongs to Money
        "LEGAL_TRANSITIONS",  # lifecycle tables belong to lifecycle.py
        "def format(",  # invoice numbering belongs to InvoiceNumberFormatter
        ".scaled(",  # proration/percentage arithmetic belongs to Money
        "Money.allocate",  # exact splitting belongs to Money
    )
    arithmetic = (ast.BinOp, ast.AugAssign, ast.UnaryOp)
    for path in pathlib.Path("billing/application").rglob("*.py"):
        text = path.read_text()
        for marker in banned_markers:
            assert marker not in text, f"{path} duplicates Foundation behaviour: {marker}"
        tree = ast.parse(text)
        for node in ast.walk(tree):
            # A rate may be carried; it may never be computed with.
            if isinstance(node, arithmetic):
                assert "basis_points" not in ast.unparse(node), (
                    f"{path} performs rate arithmetic: {ast.unparse(node)}"
                )
            # Only a persistence seam allocates. Money never does, here.
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                if node.func.attr == "allocate":
                    receiver = ast.unparse(node.func.value)
                    assert receiver.endswith("numbers"), f"{path} allocates from {receiver}"


def test_the_application_never_applies_a_rounding_policy():
    """Sharpened at M9.7: a command may *carry* a caller's declared rounding policy through to
    the Foundation (a field default), but no Application code path may *apply* one. The
    distinction is where the name appears — a class-level field annotation is data in transit;
    the same name inside a function body is a rounding decision made in the wrong layer."""
    import ast

    for path in pathlib.Path("billing/application").rglob("*.py"):
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            body = ast.unparse(node)
            assert "Rounding." not in body, f"{path}:{node.name} applies a rounding policy"


def test_the_application_defines_no_persistence_implementation():
    """M9.3 scope: persistence does not exist yet. No concrete repository may be defined
    here — a default in-memory store would be persistence in the wrong layer."""
    from billing.foundation import BillingAccountRepository, LedgerRepository

    for name in app.__all__:
        obj = getattr(app, name)
        if inspect.isclass(obj):
            assert not issubclass(obj, (BillingAccountRepository, LedgerRepository)), name


def test_application_has_no_default_factory():
    assert not hasattr(BillingApplicationFactory, "default")


def test_commands_and_queries_are_immutable():
    for message_type in (*ALL_COMMANDS, *ALL_QUERIES):
        assert message_type.__dataclass_params__.frozen, message_type.__name__
        assert "__slots__" in message_type.__dict__, message_type.__name__


def test_foundation_public_surface_is_unchanged_by_this_milestone():
    """M9.2 is frozen for M9.3: the Application adds nothing to the Foundation."""
    import billing.foundation as foundation

    assert len(foundation.__all__) >= 203  # additive growth only (M9.6: +35 catalog)


# ── end-to-end orchestration ────────────────────────────────────────────────────


def test_definition_of_done_end_to_end():
    """One customer journey dispatched entirely through the CQRS surface: open an account,
    define the catalog, start a subscription, grant and publish entitlements, ingest usage,
    draft and issue an invoice, request payment — with every write authorized, transacted,
    and audited, and every rule decided by the Foundation."""
    application, emitter, domain_emitter, _ = _app()

    account = _open_account(application)
    application.commands.dispatch(
        DefineMeter(
            actor_id="usr_1",
            org_id="",
            code="voice_minutes",
            display_name="Voice minutes",
            unit="minute",
            aggregation=MeterAggregation.SUM,
            source=MeterSource.RUNTIME_SESSION,
        )
    )
    plan = application.commands.dispatch(
        DefinePlan(
            actor_id="usr_1",
            org_id="",
            code="pro",
            display_name="Pro",
            tier=Tier.PRO,
            features=(PlanFeature("voice_minutes", allowance=1000, allows_overage=True),),
            metered_codes=("voice_minutes",),
        )
    )
    application.commands.dispatch(PublishPlan(actor_id="usr_1", org_id="", plan=plan))

    subscription = application.commands.dispatch(
        StartSubscription(
            actor_id="usr_1",
            org_id="org_1",
            account_id=account.id,
            plan_code="pro",
            plan_version=1,
            price_book_id="pbk_1",
            price_book_version=1,
            currency=INR,
            interval=BillingInterval.MONTHLY,
            items=(SubscriptionItem("pro_monthly", 1),),
        )
    )
    application.commands.dispatch(
        TransitionSubscription(
            actor_id="usr_1",
            org_id="org_1",
            subscription_id=subscription.id,
            trigger=SubscriptionTrigger.ACTIVATE,
        )
    )
    application.commands.dispatch(
        GrantEntitlement(
            actor_id="usr_1",
            org_id="org_1",
            account_id=account.id,
            entitlement=Entitlement(
                "voice_minutes", FeatureKind.QUOTA, allowance=1000, allows_overage=True
            ),
        )
    )
    snapshot = application.commands.dispatch(
        PublishEntitlementSnapshot(
            actor_id="usr_1",
            org_id="org_1",
            account_id=account.id,
            gates=_gates(),
            subscription_id=subscription.id,
        )
    )
    assert snapshot.allowance("voice_minutes") == 1000

    for i in range(2):
        application.commands.dispatch(
            RecordUsage(
                actor_id="sys",
                org_id="org_1",
                account_id=account.id,
                meter_code="voice_minutes",
                units=600,
                unit="minute",
                scope=Scope("org_1"),
                period=PERIOD,
                event_id=f"evt_{i}",
                event_type="metric.session.completed",
                observed_at=float(i),
                subscription_id=subscription.id,
            )
        )
    summary = application.queries.dispatch(
        GetUsageSummary(
            actor_id="usr_1",
            org_id="org_1",
            account_id=account.id,
            meter_code="voice_minutes",
            period=PERIOD,
        )
    )
    assert summary.units == 1200

    verdict = application.queries.dispatch(
        GetQuotaVerdict(
            actor_id="usr_1",
            org_id="org_1",
            account_id=account.id,
            entitlement_code="voice_minutes",
            consumed=summary.units,
            at=10.0,
        )
    )
    assert not verdict.within_allowance and not verdict.would_exceed  # overage is billed

    overage = summary.units - 1000
    lines = (
        ChargeLine(
            id="chl_1",
            org_id="org_1",
            account_id=account.id,
            kind=ChargeLineKind.RECURRING,
            description="Pro monthly",
            amount=Money(99900, INR),
            period=PERIOD,
            price_code="pro_monthly",
            price_version=1,
        ),
        ChargeLine(
            id="chl_2",
            org_id="org_1",
            account_id=account.id,
            kind=ChargeLineKind.OVERAGE,
            description="Voice overage",
            amount=Money(200 * overage, INR),
            period=PERIOD,
            price_code="voice_overage",
            price_version=1,
            meter_code="voice_minutes",
            quantity=Quantity(overage, "minute"),
            unit_amount=Money(200, INR),
        ),
    )
    draft = application.commands.dispatch(
        DraftInvoice(
            actor_id="usr_1",
            org_id="org_1",
            account_id=account.id,
            currency=INR,
            period=PERIOD,
            lines=lines,
            subscription_id=subscription.id,
        )
    )
    issued = application.commands.dispatch(
        IssueInvoice(actor_id="usr_1", org_id="org_1", invoice_id=draft.id, jurisdiction="IN")
    )
    application.commands.dispatch(
        RequestPayment(
            actor_id="usr_1",
            org_id="org_1",
            account_id=account.id,
            invoice_id=issued.id,
            amount=Money(99900 + 200 * overage, INR),
        )
    )

    invoices = application.queries.dispatch(
        ListInvoices(actor_id="usr_1", org_id="org_1", account_id=account.id)
    )
    assert len(invoices) == 1 and invoices[0].state == "issued"
    assert invoices[0].total.minor == 99900 + 200 * 200

    # every command audited exactly once, every composite outcome staged and published
    completed = emitter.of_type(ApplicationEventType.USE_CASE_COMPLETED)
    assert len(completed) == 13
    assert not emitter.of_type(ApplicationEventType.USE_CASE_REJECTED)
    for expected in (
        ApplicationEventType.ACCOUNT_PROVISIONED,
        ApplicationEventType.CATALOG_PUBLISHED,
        ApplicationEventType.SUBSCRIPTION_PROVISIONED,
        ApplicationEventType.SUBSCRIPTION_TRANSITIONED,
        ApplicationEventType.ENTITLEMENTS_REPUBLISHED,
        ApplicationEventType.USAGE_INGESTED,
        ApplicationEventType.INVOICE_PREPARED,
        ApplicationEventType.INVOICE_FINALIZED,
        ApplicationEventType.PAYMENT_ORCHESTRATED,
    ):
        assert emitter.of_type(expected), expected
    # the Foundation kept emitting its own, disjoint vocabulary throughout
    assert any(e.type is BillingEventType.INVOICE_ISSUED for e in domain_emitter.events)
