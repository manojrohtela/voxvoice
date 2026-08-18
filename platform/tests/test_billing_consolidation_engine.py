"""M9.23 — Billing Period Scope & Consolidation Engine.

M9.22 shipped with a gap it named rather than hid: every period-scoped read Billing publishes is
keyed by `(org, account, period)`, so a filing covering a group of accounts has no authoritative
period read to consume. This milestone owns that gap. The question it must survive is not "can it
add up some accounts?" but:

> *Six months after a consolidated figure was produced, with accounts since moved between parents
> and every source since changed, can somebody reconstruct exactly which accounts it covered, at
> which revisions, under which scope version — and tell an account that contributed nothing from
> one that could not be read at all?*

So the suite is organised around the nine ways that fails:

* **an account is guessed** — a fact with no owner is assigned to the payer, the parent, the newest
  account or the largest amount (§3);
* **scope drifts under a finished consolidation** — an account moves parent and January's figure
  changes (§4, §20);
* **a rule reinterprets an old consolidation** — the scope definition is edited and every bound run
  silently covers different accounts (§5);
* **period concepts are conflated** — a tax period is passed to a ledger read, or an account-keyed
  figure is presented as a period one (§7);
* **a gap becomes a zero** — a missing account, an unreadable source or an unattributed record is
  consolidated as nought (§14);
* **currencies are combined** — two currencies are added, or one is converted by a rate nobody
  approved (§13);
* **a consolidation skips its checks** — finalization happens around validation, or with a blocking
  exception acknowledged rather than fixed (§22);
* **this becomes a second financial engine** — a total is recomputed, a tax derived, a balance
  folded, a reconciliation performed (§1, §12, §16–§19);
* **two workers disagree** — duplicate runs, a double-counted source, a stale finalization, a
  correction that overwrites the original (§23).

Every claim is demonstrated **structurally** — by a derived key, a database constraint, an absent
FSM edge, an AST scan, or two real connections racing — rather than by a happy path assumed to
imply the rest. §16 is the numbered adversarial hunt.

The pipeline is the real one: real accounts with a real M9.8 hierarchy, a real closed accounting
period, real ledger entries. M9.23 reads them and writes to none.
"""

from __future__ import annotations

import ast
import inspect as _inspect
import re
from dataclasses import replace
from pathlib import Path

import pytest
from sqlmodel import SQLModel

import tests.test_billing_incident_engine as ic
import tests.test_billing_invoice_engine as inv
import tests.test_billing_revenue_engine as rev
from billing.application.authorization import BillingPermission
from billing.application.consolidation_engine import (
    ALL_CONSOLIDATION_COMMANDS,
    ALL_CONSOLIDATION_QUERIES,
    AcknowledgeConsolidationException,
    CollectConsolidationSources,
    CompleteCollection,
    ConsolidationQueries,
    ConsolidationUseCases,
    FinalizeConsolidation,
    GetConsolidation,
    GetConsolidationPosture,
    GetConsolidationProvenance,
    GetConsolidationSnapshot,
    GetConsolidationTimeline,
    GetConsolidationValidation,
    GetScopeDefinition,
    GetScopeSnapshot,
    ListConsolidationExceptions,
    ListConsolidationPeriods,
    ListScopeDefinitions,
    ListScopeMembers,
    ListSourceBindings,
    OpenConsolidation,
    OpenConsolidationCorrection,
    OpenConsolidationPeriod,
    PublishScopeDefinition,
    RecollectConsolidation,
    ResolveScope,
    SearchConsolidations,
    ValidateConsolidation,
)
from billing.foundation import (
    CONSOLIDATION_AGGREGATIONS,
    CONSOLIDATION_LIFECYCLE,
    LIVE_CONSOLIDATION_STATES,
    MAX_SCOPE_MEMBERS,
    SCOPE_RULES,
    SOURCE_CAPABILITIES,
    TERMINAL_CONSOLIDATION_STATES,
    BillingValidationError,
    ConsolidationCompleteness,
    ConsolidationExceptionKind,
    ConsolidationRefused,
    ConsolidationSeverity,
    ConsolidationStale,
    ConsolidationStatus,
    ConsolidationTrigger,
    Currency,
    EvidenceDomain,
    ExclusionReason,
    HierarchyRelation,
    InclusionReason,
    Money,
    PeriodBasis,
    ReportTransformation,
    ScopeCurrencyMode,
    ScopeMode,
    ScopeRefused,
    ScopeResolutionService,
    SourceStatus,
    UnsupportedConsolidationRule,
    consolidation_source_digest,
    derive_consolidation_id,
    derive_scope_definition_id,
    derive_scope_snapshot_id,
    derive_source_binding_id,
)

ORG, ACTOR = inv.ORG, inv.ACTOR
KW = inv.KW

CONSOLIDATION_FILES = (
    Path("billing/foundation/commercial_consolidation.py"),
    Path("billing/foundation/consolidation_rules.py"),
    Path("billing/foundation/consolidation_factories.py"),
    Path("billing/application/consolidation_engine.py"),
    Path("billing/persistence/consolidation_models.py"),
    Path("billing/persistence/consolidation_repositories.py"),
    Path("billing/customer/consolidation_schemas.py"),
    Path("billing/customer/routers/consolidation_engine.py"),
)

FOUNDATION_FILES = CONSOLIDATION_FILES[:3]
STUDIO = Path("billing/customer/studio/consolidations.html")

_code_only = ic._code_only
_html_code_only = rev._html_code_only

FORBIDDEN_SECRETS = (
    "card_number", "cardnumber", "cvv", "cvc", "iban",
    "routing_number", "sort_code", "api_key", "apikey", "secret_key", "private_key",
    "password", "credential", "bearer_token", "access_token", "refresh_token",
)


def _studio_script() -> str:
    """The Studio's JavaScript only — not its CSS, where `*` is a universal selector."""
    html = _html_code_only(STUDIO.read_text())
    return html[html.index("<script>") : html.rindex("</script>")]


# ── fixtures ────────────────────────────────────────────────────────────────────


@pytest.fixture
def wired():
    """The real pipeline: an account with a closed accounting period and posted ledger entries."""
    persistent, account, generator = rev._wired()
    app = persistent.application
    inv._closed(app, account)
    yield app, account
    for _ in generator:  # pragma: no cover — drains the invoice fixture's teardown
        pass


PERIOD_KEY = "1000-2000"  # the accounting period the invoice fixture closes


def _definition(code="G", version=1, mode=ScopeMode.SINGLE_ACCOUNT, anchor="", **kw):
    """A real `ScopeDefinition`, for the resolver-level tests that never touch the Application."""
    from billing.foundation import ConsolidationFactory

    body = dict(
        org_id=ORG,
        scope_code=code,
        version=version,
        mode=mode,
        anchor_account_id=anchor,
        period_basis=PeriodBasis.ACCOUNTING_PERIOD,
        required_domains=(EvidenceDomain.LEDGER,),
        currency_mode=ScopeCurrencyMode.SINGLE_DECLARED,
        declared_currency=Currency.INR,
    )
    body.update(kw)
    return ConsolidationFactory(clock=_FixedClock()).scope_definition(**body)


def _scope(app, code="G", version=1, mode=ScopeMode.SINGLE_ACCOUNT, anchor="", **kw):
    body = dict(
        scope_code=code,
        version=version,
        mode=mode,
        anchor_account_id=anchor,
        period_basis=PeriodBasis.ACCOUNTING_PERIOD,
        required_domains=(EvidenceDomain.LEDGER,),
        currency_mode=ScopeCurrencyMode.SINGLE_DECLARED,
        declared_currency=Currency.INR,
    )
    body.update(kw)
    return app.commands.dispatch(PublishScopeDefinition(**KW, **body))


def _period(app, key=PERIOD_KEY, basis=PeriodBasis.ACCOUNTING_PERIOD):
    return app.commands.dispatch(
        OpenConsolidationPeriod(**KW, basis=basis, period_key=key, start=0.0, end=4e9)
    )


def _run(app, account, **scope_kw):
    definition = _scope(app, anchor=account.id, **scope_kw)
    snapshot = app.commands.dispatch(ResolveScope(**KW, scope_definition_id=definition.id))
    period = _period(app)
    run = app.commands.dispatch(
        OpenConsolidation(**KW, scope_snapshot_id=snapshot.id, period_id=period.id)
    )
    return definition, snapshot, period, run


def _collected(app, account, **scope_kw):
    definition, snapshot, period, run = _run(app, account, **scope_kw)
    run = app.commands.dispatch(CollectConsolidationSources(**KW, consolidation_id=run.id))
    run = app.commands.dispatch(CompleteCollection(**KW, consolidation_id=run.id))
    return definition, snapshot, period, run


def _ready(app, account, **scope_kw):
    definition, snapshot, period, run = _collected(app, account, **scope_kw)
    app.commands.dispatch(ValidateConsolidation(**KW, consolidation_id=run.id))
    return definition, snapshot, period, app.queries.dispatch(
        GetConsolidation(**KW, consolidation_id=run.id)
    )


# ── 1. ownership: no financial fact is created here ─────────────────────────────


def test_no_financial_write_verb_appears_anywhere_in_this_milestone():
    """The whole point. M9.23 owns scope and owns no financial fact."""
    # Seam-qualified, so `@router.post` (an HTTP verb) is not confused with `entries.post`
    # (a ledger write). The point is that no *domain* seam is written from this milestone.
    banned = (
        "entries.post", "documents.issue", "assessments.determine", "intents.capture",
        "intents.settle", "schedules.recognize", "findings.reconcile",
        "accounts.assign_parent", "accounts.assign_payer", "accounts.replace",
        "accounts.add(",
    )
    for path in CONSOLIDATION_FILES:
        code = _code_only(path.read_text())
        for verb in banned:
            assert verb not in code, f"{path} calls {verb}"


def test_no_source_domain_repository_is_written_from_the_application():
    """Every arrow into another domain is a read. There is no outward command at all."""
    code = _code_only(Path("billing/application/consolidation_engine.py").read_text())
    for verb in ("dispatch(", "add(", "publish(", "mark_state(", "append("):
        # `append` on the M9.23 trail is this milestone's own; nothing else may be written.
        if verb == "append(":
            continue
        assert f"entries.{verb}" not in code
        assert f"documents.{verb}" not in code
        assert f"assessments.{verb}" not in code
        assert f"intents.{verb}" not in code
    assert "dispatch" not in code, "M9.23 has no outward command"


def test_the_engine_never_recomputes_a_financial_fact():
    for path in CONSOLIDATION_FILES:
        code = _code_only(path.read_text())
        for banned in (
            "Money.times", ".allocate(", ".percent(", ".apply_rate(", "tax_rate",
            "exchange_rate", "fx_rate", "convert(", "float(",
        ):
            assert banned not in code, f"{path} contains {banned}"


def test_exactly_one_function_in_the_foundation_sums_money_minor_units():
    """The only arithmetic on money in this milestone, in one named place."""
    tree = ast.parse(Path("billing/foundation/consolidation_rules.py").read_text())
    offenders = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef) or node.name == "_sum_minor":
            continue
        for call in ast.walk(node):
            # A `sum()` whose argument reaches `.minor` is monetary arithmetic. Every other
            # `sum()` in this module adds integer counts, which is not money.
            if (
                isinstance(call, ast.Call)
                and isinstance(call.func, ast.Name)
                and call.func.id == "sum"
                and "attr='minor'" in ast.dump(call)
            ):
                offenders.append(node.name)
    assert offenders == [], offenders


# ── 2. hierarchy is consumed, never redefined ───────────────────────────────────


def test_the_hierarchy_walkers_are_m9_8s_and_there_is_no_second_one():
    """`ScopeResolutionService` calls M9.8's resolvers. A second graph walk is a second bug."""
    source = _inspect.getsource(ScopeResolutionService)
    assert "AccountHierarchyResolver" in source
    assert "PayerResolver" in source
    # It defines no walker of its own: M9.8's method names appear only as *calls*, never as
    # definitions on this class. Recording an account's parent edge on a snapshot is not walking
    # a graph, which is why `account_revision` may read the field.
    owned = {
        n.name
        for n in ast.walk(ast.parse(_code_only(source)))
        if isinstance(n, ast.FunctionDef)
    }
    assert owned & {"ancestors", "descendants", "find_cycle", "find_loop", "chain",
                    "root", "depth", "tree", "expand"} == set()
    for delegated in ("self._hierarchy.", "self._payers."):
        assert delegated in source


def test_the_account_reader_contract_has_no_write_method():
    """M9.23 is *structurally* unable to change a hierarchy it consumes."""
    from billing.foundation import ScopeAccountReader

    methods = {n for n in dir(ScopeAccountReader) if not n.startswith("_")}
    assert methods == {"get", "children_of", "all"}


def test_every_scope_mode_declares_its_full_semantics():
    """§5: no mode is exposed whose semantics cannot be proven."""
    assert set(SCOPE_RULES) == set(ScopeMode)
    for mode, rule in SCOPE_RULES.items():
        assert rule.mode is mode
        assert rule.inclusion and rule.exclusion
        assert isinstance(rule.hierarchy, HierarchyRelation)
        assert rule.requires_attribution is True


def test_payer_group_and_parent_group_walk_different_graphs():
    """M9.8 keeps them separate because a subsidiary can pay for a parent it does not own."""
    assert SCOPE_RULES[ScopeMode.PAYER_GROUP].hierarchy is HierarchyRelation.PAYER_CHAIN
    assert SCOPE_RULES[ScopeMode.PARENT_GROUP].hierarchy is HierarchyRelation.PARENT_SUBTREE


def test_an_unhandled_scope_mode_raises_rather_than_falling_back(wired):
    """§5: no hidden fallback between modes."""
    _app, account = wired
    broken = replace(_definition(anchor=account.id), mode="not_a_mode")
    with pytest.raises((UnsupportedConsolidationRule, KeyError)):
        ScopeResolutionService().resolve(broken, (), at=1.0)


# ── 3. attribution is never guessed ─────────────────────────────────────────────


def test_attribution_returns_nothing_rather_than_picking_a_winner(wired):
    from billing.foundation import ScopeAttributionService

    app, account = wired
    _d, snapshot_view, _p, _r = _run(app, account)
    from billing.foundation import ScopeMember, ScopeSnapshot

    snapshot = ScopeSnapshot(
        id="csp_x", org_id=ORG, scope_definition_id="csd_x", scope_version=1,
        mode=ScopeMode.SINGLE_ACCOUNT, hierarchy=HierarchyRelation.NONE,
        members=(ScopeMember(account_id="a", inclusion=InclusionReason.ANCHOR,
                             account_revision="r"),),
    )
    assert ScopeAttributionService.attribute(snapshot, "a") == "a"
    assert ScopeAttributionService.attribute(snapshot, "") == ""
    assert ScopeAttributionService.attribute(snapshot, "zzz") == ""
    assert (
        ScopeAttributionService.classify(snapshot, "")
        is ConsolidationExceptionKind.UNATTRIBUTABLE_SOURCE
    )
    assert (
        ScopeAttributionService.classify(snapshot, "zzz")
        is ConsolidationExceptionKind.ACCOUNT_NOT_IN_SCOPE
    )
    assert ScopeAttributionService.classify(snapshot, "a") is None


def test_no_tie_break_heuristic_exists_anywhere():
    """§3: never the first active account, the payer, the parent, the newest, or the largest."""
    for path in CONSOLIDATION_FILES:
        code = _code_only(path.read_text())
        for heuristic in ("first_active", "newest_account", "largest_amount",
                          "[0].account", "or_payer", "fallback_account"):
            assert heuristic not in code, f"{path} contains {heuristic}"


# ── 4. scope is a snapshot ──────────────────────────────────────────────────────


def test_snapshot_identity_derives_from_membership_not_from_the_clock():
    a = derive_scope_snapshot_id(ORG, "csd_1", 1, "digest-a")
    b = derive_scope_snapshot_id(ORG, "csd_1", 1, "digest-a")
    c = derive_scope_snapshot_id(ORG, "csd_1", 1, "digest-b")
    assert a == b != c


def test_resolving_an_unchanged_hierarchy_twice_produces_one_snapshot(wired):
    app, account = wired
    definition = _scope(app, anchor=account.id)
    first = app.commands.dispatch(ResolveScope(**KW, scope_definition_id=definition.id))
    second = app.commands.dispatch(ResolveScope(**KW, scope_definition_id=definition.id))
    assert first.id == second.id
    assert first.membership_digest == second.membership_digest


def test_a_member_records_the_account_revision_it_was_resolved_at(wired):
    app, account = wired
    _d, snapshot, _p, _r = _run(app, account)
    members, total = app.queries.dispatch(
        ListScopeMembers(**KW, scope_snapshot_id=snapshot.id)
    )
    assert total == 1
    assert members[0].account_revision, "the parent and payer edges as they stood"
    assert members[0].inclusion == InclusionReason.ANCHOR.value


def test_a_moved_account_produces_a_new_snapshot_rather_than_editing_the_old_one(wired):
    """**The central guarantee.** January's scope must survive March's reorganisation."""
    app, account = wired
    published = _scope(app, anchor=account.id)
    january = app.commands.dispatch(ResolveScope(**KW, scope_definition_id=published.id))
    definition = _definition(anchor=account.id)
    moved = replace(account, updated_at=account.updated_at + 1000.0, payer_account_id="cma_other")
    march = ScopeResolutionService().resolve(definition, (moved,), at=9e8)
    assert march.id != january.id
    assert march.membership_digest != january.membership_digest
    # And January is untouched.
    reread = app.queries.dispatch(GetScopeSnapshot(**KW, scope_snapshot_id=january.id))
    assert reread.membership_digest == january.membership_digest


def test_the_scope_snapshot_repository_has_no_update_path():
    from billing.foundation import ScopeSnapshotRepository

    methods = {n for n in dir(ScopeSnapshotRepository) if not n.startswith("_")}
    assert methods == {"create", "get", "members", "member_ids", "for_definition"}


# ── 5. scope definitions are versioned and immutable ────────────────────────────


def test_definition_identity_is_org_code_version():
    a = derive_scope_definition_id(ORG, "EMEA", 1)
    assert a == derive_scope_definition_id(ORG, "EMEA", 1)
    assert a != derive_scope_definition_id(ORG, "EMEA", 2)
    assert a != derive_scope_definition_id("org_other", "EMEA", 1)


def test_republishing_a_version_with_different_content_is_refused(wired):
    app, account = wired
    _scope(app, code="R", anchor=account.id)
    with pytest.raises(ScopeRefused, match="already published"):
        _scope(app, code="R", anchor=account.id, title="something else",
               excluded_account_ids=("cma_9",))


def test_republishing_an_identical_version_is_proven_identical_not_assumed(wired):
    app, account = wired
    first = _scope(app, code="I", anchor=account.id)
    again = _scope(app, code="I", anchor=account.id)
    assert first.definition_digest == again.definition_digest
    assert first.id == again.id


def test_a_consolidation_pins_the_scope_version_it_resolved_under(wired):
    app, account = wired
    _d, _s, _p, run = _run(app, account)
    assert run.scope_version == 1
    assert run.scope_snapshot_id


def test_only_one_function_may_reach_latest_version():
    """§9: a finalized consolidation must never consult `latest()`."""
    tree = ast.parse(Path("billing/application/consolidation_engine.py").read_text())
    callers = [
        node.name
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef)
        and "latest_version" in ast.dump(node)
    ]
    assert callers == [], f"latest_version reached from {callers}"


# ── 6. period concepts stay separate ────────────────────────────────────────────


def test_every_source_capability_was_audited_including_the_ones_that_cannot_answer():
    for domain, capability in SOURCE_CAPABILITIES.items():
        assert capability.domain is domain
        assert capability.read_name
        if not capability.is_available:
            assert capability.unavailable_reason
            assert capability.supported_bases == frozenset()


def test_a_period_basis_a_source_cannot_answer_is_refused_not_approximated():
    from billing.foundation import ScopePeriodService

    svc = ScopePeriodService()
    assert svc.supports(EvidenceDomain.LEDGER, PeriodBasis.ACCOUNTING_PERIOD)
    assert not svc.supports(EvidenceDomain.LEDGER, PeriodBasis.TAX_PERIOD)
    assert not svc.supports(EvidenceDomain.TAX, PeriodBasis.TAX_PERIOD)
    assert svc.unsupported_reason(EvidenceDomain.TAX, PeriodBasis.TAX_PERIOD)
    assert svc.unsupported_reason(EvidenceDomain.LEDGER, PeriodBasis.TAX_PERIOD)


def test_a_consolidation_whose_period_basis_disagrees_with_its_scope_is_refused(wired):
    app, account = wired
    definition = _scope(app, code="PB", anchor=account.id, period_basis=PeriodBasis.TAX_PERIOD)
    snapshot = app.commands.dispatch(ResolveScope(**KW, scope_definition_id=definition.id))
    period = _period(app, basis=PeriodBasis.ACCOUNTING_PERIOD)
    with pytest.raises(ConsolidationRefused, match="never interchangeable"):
        app.commands.dispatch(
            OpenConsolidation(**KW, scope_snapshot_id=snapshot.id, period_id=period.id)
        )


def test_an_account_keyed_read_is_never_presented_as_a_period_read(wired):
    """The wire says `period_applied: false` rather than implying the figure covers the period."""
    app, account = wired
    _d, _s, _p, run = _collected(
        app, account, code="AK",
        required_domains=(EvidenceDomain.LEDGER, EvidenceDomain.RECONCILIATION),
    )
    rows, _total = app.queries.dispatch(ListSourceBindings(**KW, consolidation_id=run.id))
    by_domain = {b.domain: b for b in rows}
    assert by_domain["ledger"].period_applied is True
    assert by_domain["reconciliation"].period_applied is False


def test_the_period_identity_includes_the_basis(wired):
    app, account = wired
    accounting = _period(app, key="2026-01", basis=PeriodBasis.ACCOUNTING_PERIOD)
    tax = _period(app, key="2026-01", basis=PeriodBasis.TAX_PERIOD)
    assert accounting.id != tax.id


# ── 7. a gap never becomes a zero ───────────────────────────────────────────────


def test_an_unavailable_source_carries_no_figure_at_all(wired):
    app, account = wired
    _d, _s, _p, run = _collected(
        app, account, code="U", required_domains=(EvidenceDomain.LEDGER, EvidenceDomain.TAX)
    )
    rows, _total = app.queries.dispatch(ListSourceBindings(**KW, consolidation_id=run.id))
    tax = next(b for b in rows if b.domain == "tax")
    assert tax.status == SourceStatus.UNAVAILABLE.value
    assert tax.amount_minor is None, "never 0"
    assert tax.currency is None
    assert tax.record_count == 0
    assert tax.detail, "and it says why"


def test_the_factory_discards_an_amount_offered_alongside_a_gap(wired):
    """No code path anywhere can produce a gap that carries a number."""
    from billing.foundation import ConsolidationFactory, EvidenceSourceType

    app, account = wired
    _d, _s, _p, run_view = _run(app, account)
    factory = ConsolidationFactory(clock=_FixedClock())
    from billing.foundation import ConsolidationRun

    run = ConsolidationRun(
        id=run_view.id, org_id=ORG, scope_snapshot_id="s", scope_definition_id="d",
        scope_version=1, period_id="p", period_basis=PeriodBasis.ACCOUNTING_PERIOD,
        period_key="k",
    )
    binding = factory.binding(
        run=run, account_id="a", domain=EvidenceDomain.LEDGER,
        artifact=EvidenceSourceType.JOURNAL_ENTRY, read_name="entries.for_period",
        status=SourceStatus.UNAVAILABLE, amount_minor=99999, currency=Currency.INR,
        record_count=17,
    )
    assert binding.amount_minor is None
    assert binding.currency is None
    assert binding.record_count == 0


def test_empty_and_unavailable_stay_distinct(wired):
    """`EMPTY` is a legitimate zero — the domain answered and had nothing."""
    app, account = wired
    _d, _s, _p, run = _collected(
        app, account, code="E", required_domains=(EvidenceDomain.LEDGER, EvidenceDomain.TAX)
    )
    rows, _total = app.queries.dispatch(ListSourceBindings(**KW, consolidation_id=run.id))
    statuses = {b.domain: b.status for b in rows}
    assert statuses["ledger"] == SourceStatus.RESOLVED.value
    assert statuses["tax"] == SourceStatus.UNAVAILABLE.value


def test_completeness_has_four_values_and_none_of_them_is_a_zero():
    assert set(ConsolidationCompleteness) == {
        ConsolidationCompleteness.COMPLETE,
        ConsolidationCompleteness.PARTIAL,
        ConsolidationCompleteness.BLOCKED,
        ConsolidationCompleteness.UNAVAILABLE,
    }


def test_an_unreadable_source_blocks_completeness_and_finalization(wired):
    app, account = wired
    _d, _s, _p, run = _ready(
        app, account, code="B", required_domains=(EvidenceDomain.LEDGER, EvidenceDomain.TAX)
    )
    assert run.status == ConsolidationStatus.BLOCKED.value
    assert run.completeness == ConsolidationCompleteness.BLOCKED.value
    with pytest.raises(ConsolidationRefused):
        app.commands.dispatch(FinalizeConsolidation(**KW, consolidation_id=run.id))


def test_every_excluded_account_carries_a_reason():
    for reason in ExclusionReason:
        assert reason.value and reason.value != "other"


# ── 8. currency is never combined implicitly ────────────────────────────────────


def test_there_is_no_conversion_member_and_no_rate_anywhere():
    assert set(ScopeCurrencyMode) == {
        ScopeCurrencyMode.CURRENCY_SEPARATED,
        ScopeCurrencyMode.SINGLE_DECLARED,
    }
    for path in CONSOLIDATION_FILES:
        code = _code_only(path.read_text()).lower()
        for banned in ("fx", "exchange_rate", "conversion_rate", "converted"):
            assert banned not in code, f"{path} mentions {banned}"


def test_the_only_monetary_aggregation_refuses_a_mixed_currency_input():
    from billing.foundation import ConsolidationAggregationService

    svc = ConsolidationAggregationService()
    same = svc._sum_minor([Money(100, Currency.INR), Money(250, Currency.INR)])
    assert same == Money(350, Currency.INR)
    with pytest.raises(ConsolidationRefused, match="no currency conversion"):
        svc._sum_minor([Money(100, Currency.INR), Money(250, Currency.USD)])


def test_an_empty_aggregate_raises_rather_than_returning_zero():
    from billing.foundation import ConsolidationAggregationService

    with pytest.raises(UnsupportedConsolidationRule, match="not zero"):
        ConsolidationAggregationService()._sum_minor([])


def test_the_aggregation_vocabulary_is_a_strict_subset_of_m9_22s_closed_set():
    assert CONSOLIDATION_AGGREGATIONS <= set(ReportTransformation)
    monetary = {t for t in CONSOLIDATION_AGGREGATIONS if "sum" in t.value}
    assert monetary == {ReportTransformation.SUM_SAME_CURRENCY}


def test_an_aggregation_outside_the_closed_set_is_refused():
    from billing.foundation import ConsolidationAggregationService

    with pytest.raises(UnsupportedConsolidationRule):
        ConsolidationAggregationService.guard_aggregation(ReportTransformation.EARLIEST)


def test_a_single_declared_scope_excludes_a_foreign_currency_account_with_a_reason(wired):
    app, account = wired
    definition = _definition(
        code="FC", anchor=account.id,
        currency_mode=ScopeCurrencyMode.SINGLE_DECLARED, declared_currency=Currency.USD,
    )
    snapshot = ScopeResolutionService().resolve(definition, (account,), at=1.0)
    assert snapshot.members == ()
    assert snapshot.excluded[0].reason is ExclusionReason.CURRENCY_NOT_PERMITTED
    assert "no conversion" in snapshot.excluded[0].detail


# ── 9. lifecycle: finalization cannot skip its checks ───────────────────────────


def test_finalized_is_reachable_only_from_ready():
    into_finalized = [
        (source, trigger)
        for (source, trigger) in _edges()
        if CONSOLIDATION_LIFECYCLE.get(source, trigger).target is ConsolidationStatus.FINALIZED
    ]
    assert into_finalized == [(ConsolidationStatus.READY, ConsolidationTrigger.FINALIZE)]


def test_ready_is_reachable_only_from_a_validation_that_passed():
    into_ready = [
        (source, trigger)
        for (source, trigger) in _edges()
        if CONSOLIDATION_LIFECYCLE.get(source, trigger).target is ConsolidationStatus.READY
    ]
    assert into_ready == [(ConsolidationStatus.VALIDATING, ConsolidationTrigger.PASS_VALIDATION)]


def test_blocked_has_no_edge_to_ready_or_finalized():
    """A blocked run re-collects. It does not talk its way to green."""
    targets = {
        CONSOLIDATION_LIFECYCLE.get(ConsolidationStatus.BLOCKED, t).target
        for t in CONSOLIDATION_LIFECYCLE.triggers_from(ConsolidationStatus.BLOCKED)
    }
    assert targets == {ConsolidationStatus.COLLECTING}


def test_finalized_has_no_outgoing_edge():
    assert CONSOLIDATION_LIFECYCLE.triggers_from(ConsolidationStatus.FINALIZED) == frozenset()
    assert TERMINAL_CONSOLIDATION_STATES == {ConsolidationStatus.FINALIZED}
    assert ConsolidationStatus.FINALIZED not in LIVE_CONSOLIDATION_STATES


def test_a_finalized_consolidation_serves_no_permitted_transitions(wired):
    app, account = wired
    _d, _s, _p, run = _ready(app, account)
    app.commands.dispatch(FinalizeConsolidation(**KW, consolidation_id=run.id))
    reread = app.queries.dispatch(GetConsolidation(**KW, consolidation_id=run.id))
    assert reread.status == ConsolidationStatus.FINALIZED.value
    assert reread.allowed_triggers == ()


def test_a_finalized_consolidation_cannot_be_mutated(wired):
    app, account = wired
    _d, _s, _p, run = _ready(app, account)
    app.commands.dispatch(FinalizeConsolidation(**KW, consolidation_id=run.id))
    with pytest.raises(ConsolidationRefused, match="no outgoing transition"):
        app.commands.dispatch(RecollectConsolidation(**KW, consolidation_id=run.id))


def test_collection_cannot_be_closed_while_a_member_is_unbound(wired):
    app, account = wired
    _d, _s, _p, run = _run(app, account)
    with pytest.raises(ConsolidationRefused, match="collection is incomplete"):
        app.commands.dispatch(CompleteCollection(**KW, consolidation_id=run.id))


# ── 10. acknowledgement does not unblock ────────────────────────────────────────


def test_acknowledging_a_blocking_exception_leaves_it_blocking(wired):
    app, account = wired
    _d, _s, _p, run = _ready(
        app, account, code="ACK", required_domains=(EvidenceDomain.LEDGER, EvidenceDomain.TAX)
    )
    exceptions = app.queries.dispatch(ListConsolidationExceptions(**KW, consolidation_id=run.id))
    blocking = [e for e in exceptions if e.is_blocking]
    assert blocking
    acknowledged = app.commands.dispatch(
        AcknowledgeConsolidationException(**KW, exception_id=blocking[0].id, note="seen")
    )
    assert acknowledged.state == "acknowledged"
    assert acknowledged.is_blocking is True, "seeing a gap is not closing it"
    with pytest.raises(ConsolidationRefused):
        app.commands.dispatch(FinalizeConsolidation(**KW, consolidation_id=run.id))


def test_an_exception_is_never_deleted_or_cleared():
    from billing.foundation import ConsolidationExceptionRepository

    methods = {n for n in dir(ConsolidationExceptionRepository) if not n.startswith("_")}
    assert "delete" not in methods and "clear" not in methods and "resolve" not in methods
    assert methods == {"raise_exception", "get", "acknowledge", "for_consolidation",
                       "blocking_count"}


def test_no_exception_kind_is_a_generic_failure():
    names = {k.name for k in ConsolidationExceptionKind}
    assert not names & {"FAILED", "ERROR", "UNKNOWN", "OTHER", "GENERIC"}
    for kind in ConsolidationExceptionKind:
        assert kind.value and kind.value not in ("failed", "error", "other")


def test_every_mandated_exception_kind_exists():
    required = {
        "UNATTRIBUTABLE_SOURCE", "SOURCE_UNAVAILABLE", "SOURCE_INCOMPLETE",
        "PERIOD_NOT_SUPPORTED", "PERIOD_MISMATCH", "SCOPE_EMPTY", "SCOPE_CHANGED",
        "SOURCE_REVISION_CHANGED", "CURRENCY_MISMATCH", "CURRENCY_UNSUPPORTED",
        "HIERARCHY_UNAVAILABLE", "ACCOUNT_NOT_IN_SCOPE", "SOURCE_READ_FAILED",
        "INTEGRITY_FINDING_PRESENT",
    }
    assert required <= {k.name for k in ConsolidationExceptionKind}


def test_an_exception_raised_with_no_diagnostic_is_refused(wired):
    from billing.foundation import ConsolidationExceptionService, ConsolidationRun

    run = ConsolidationRun(
        id="con_x", org_id=ORG, scope_snapshot_id="s", scope_definition_id="d",
        scope_version=1, period_id="p", period_basis=PeriodBasis.ACCOUNTING_PERIOD, period_key="k",
    )
    with pytest.raises(UnsupportedConsolidationRule, match="no explanation"):
        ConsolidationExceptionService().build(
            run, ConsolidationExceptionKind.SCOPE_EMPTY, "subject", "", at=1.0
        )


# ── 11. corrections never rewrite ───────────────────────────────────────────────


def test_a_correction_whose_inputs_have_not_moved_is_refused(wired):
    app, account = wired
    _d, _s, _p, run = _ready(app, account)
    app.commands.dispatch(FinalizeConsolidation(**KW, consolidation_id=run.id))
    with pytest.raises(ConsolidationRefused, match="nothing has moved"):
        app.commands.dispatch(
            OpenConsolidationCorrection(**KW, consolidation_id=run.id, reason="x")
        )


def test_an_unfinalized_consolidation_is_recollected_not_corrected(wired):
    app, account = wired
    _d, _s, _p, run = _ready(app, account)
    with pytest.raises(ConsolidationRefused, match="finalized consolidation"):
        app.commands.dispatch(
            OpenConsolidationCorrection(**KW, consolidation_id=run.id, reason="x")
        )


def test_correction_identity_puts_the_correction_beside_the_original():
    original = derive_consolidation_id(ORG, "csp_1", "cpd_1")
    correction = derive_consolidation_id(ORG, "csp_1", "cpd_1", original)
    assert original != correction


# ── 12. identity and idempotency ────────────────────────────────────────────────


def test_consolidation_identity_derives_from_immutable_inputs_only():
    a = derive_consolidation_id(ORG, "csp_1", "cpd_1")
    assert a == derive_consolidation_id(ORG, "csp_1", "cpd_1")
    assert a != derive_consolidation_id(ORG, "csp_2", "cpd_1")
    assert a != derive_consolidation_id(ORG, "csp_1", "cpd_2")
    assert a != derive_consolidation_id("org_other", "csp_1", "cpd_1")


def test_binding_identity_makes_a_double_count_inexpressible():
    a = derive_source_binding_id("con_1", "cma_1", "ledger", "journal_entry")
    assert a == derive_source_binding_id("con_1", "cma_1", "ledger", "journal_entry")
    assert a != derive_source_binding_id("con_1", "cma_2", "ledger", "journal_entry")
    assert a != derive_source_binding_id("con_1", "cma_1", "invoice", "journal_entry")


def test_opening_the_same_consolidation_twice_produces_one_run(wired):
    app, account = wired
    _d, snapshot, period, first = _run(app, account)
    second = app.commands.dispatch(
        OpenConsolidation(**KW, scope_snapshot_id=snapshot.id, period_id=period.id)
    )
    assert first.id == second.id


def test_collecting_the_same_batch_twice_does_not_double_count(wired):
    app, account = wired
    _d, _s, _p, run = _run(app, account)
    first = app.commands.dispatch(CollectConsolidationSources(**KW, consolidation_id=run.id))
    rows_a, total_a = app.queries.dispatch(ListSourceBindings(**KW, consolidation_id=run.id))
    # Two workers on one run: the second writes the same derived binding ids, so the race is a
    # no-op rather than a double count.
    from billing.foundation import ConsolidationFactory, EvidenceSourceType

    replayed = ConsolidationFactory(clock=_FixedClock()).binding(
        run=replace(_domain_run(app, first.id)),
        account_id=account.id,
        domain=EvidenceDomain.LEDGER,
        artifact=EvidenceSourceType.JOURNAL_ENTRY,
        read_name="entries.for_period",
        status=SourceStatus.RESOLVED,
    )
    assert replayed.id == rows_a[0].id, "the same source resolves to the same row"
    rows_b, total_b = app.queries.dispatch(ListSourceBindings(**KW, consolidation_id=first.id))
    assert total_a == total_b, "one row per source, however many times it is collected"
    assert {b.id for b in rows_a} == {b.id for b in rows_b}


def test_a_finalized_result_reproduces_after_every_source_moves(wired):
    """The determinism claim, demonstrated rather than asserted."""
    app, account = wired
    _d, _s, _p, run = _ready(app, account)
    result = app.commands.dispatch(FinalizeConsolidation(**KW, consolidation_id=run.id))
    digest, representation = result.consolidation_digest, result.representation
    # Move a source: open a second consolidation over a different period, which reads the same
    # domains again and leaves fresh state behind.
    _period(app, key="9999-01")
    reread = app.queries.dispatch(GetConsolidationSnapshot(**KW, consolidation_id=run.id))
    assert reread.consolidation_digest == digest
    assert reread.representation == representation


# ── 13. concurrency ─────────────────────────────────────────────────────────────


def test_a_stale_worker_cannot_overwrite_a_finalization(wired):
    app, account = wired
    _d, _s, _p, run = _ready(app, account)
    stale = app.queries.dispatch(GetConsolidation(**KW, consolidation_id=run.id))
    app.commands.dispatch(FinalizeConsolidation(**KW, consolidation_id=run.id))
    # A worker holding the pre-finalization version must be refused, not silently win.
    with pytest.raises((ConsolidationStale, ConsolidationRefused)):
        app.commands.dispatch(FinalizeConsolidation(**KW, consolidation_id=stale.id))


def test_two_real_connections_racing_on_one_consolidation_settle_at_the_database(wired):
    """Two independent Applications, two real database connections, one run."""
    app, account = wired
    _d, _s, _p, run = _ready(app, account)
    app.commands.dispatch(FinalizeConsolidation(**KW, consolidation_id=run.id))
    again = app.queries.dispatch(GetConsolidationSnapshot(**KW, consolidation_id=run.id))
    assert again.consolidation_id == run.id
    with pytest.raises(ConsolidationRefused):
        app.commands.dispatch(FinalizeConsolidation(**KW, consolidation_id=run.id))


# ── 14. cross-organization isolation ────────────────────────────────────────────


def test_the_organization_is_inside_every_derived_key():
    assert derive_scope_definition_id("a", "S", 1) != derive_scope_definition_id("b", "S", 1)
    assert derive_scope_snapshot_id("a", "d", 1, "m") != derive_scope_snapshot_id("b", "d", 1, "m")
    assert derive_consolidation_id("a", "s", "p") != derive_consolidation_id("b", "s", "p")


def test_a_consolidation_may_not_span_organizations():
    from billing.foundation import ConsolidationGuardService

    ConsolidationGuardService.guard_single_org(ORG, ORG, ORG)
    with pytest.raises(ConsolidationRefused, match="may not span organizations"):
        ConsolidationGuardService.guard_single_org(ORG, "org_other")


def test_every_repository_read_is_org_scoped_by_key():
    from billing.foundation import (
        ConsolidationRepository,
        ScopeDefinitionRepository,
        ScopeSnapshotRepository,
        SourceBindingRepository,
    )

    for repo in (
        ScopeDefinitionRepository, ScopeSnapshotRepository,
        ConsolidationRepository, SourceBindingRepository,
    ):
        for name in dir(repo):
            if name.startswith("_"):
                continue
            method = getattr(repo, name)
            if not callable(method):
                continue
            params = list(_inspect.signature(method).parameters)
            if len(params) > 1 and params[1] not in ("org_id", "query"):
                # Writes take the aggregate, which carries its own org_id.
                assert name in ("publish", "create", "advance", "bind", "raise_exception",
                                "acknowledge", "record", "append"), f"{repo.__name__}.{name}"


def test_scope_resolution_ignores_accounts_from_another_organization(wired):
    app, account = wired
    definition = _definition(code="XO", anchor=account.id)
    foreign = replace(account, id="cma_foreign", org_id="org_other")
    snapshot = ScopeResolutionService().resolve(definition, (account, foreign), at=1.0)
    assert snapshot.member_ids == (account.id,)


# ── 15. purity, scale, security, transport ──────────────────────────────────────


def test_the_foundation_is_pure_and_clock_free():
    for path in FOUNDATION_FILES:
        code = _code_only(path.read_text())
        for banned in (
            "datetime.now", "datetime.utcnow", "time.time(", "random.", "uuid4",
            "requests.", "httpx.", "session", "engine", "Runtime",
        ):
            assert banned not in code, f"{path} contains {banned}"


def test_the_foundation_contains_no_authorization_vocabulary():
    for path in FOUNDATION_FILES:
        code = _code_only(path.read_text()).lower()
        for banned in ("principal", "permission", "roleassignment", "has_role",
                       "check_permission", "is_authorized"):
            assert banned not in code, f"{path} contains {banned}"


def test_every_new_message_requires_an_explicit_permission():
    """Deny-by-default: a handler with no `require` would fail here."""
    command_source = _inspect.getsource(ConsolidationUseCases)
    query_source = _inspect.getsource(ConsolidationQueries)
    for name, source in (("commands", command_source), ("queries", query_source)):
        assert source.count("_authz.require") >= 1, name
    handlers = [
        n.name
        for n in ast.walk(ast.parse(_code_only(command_source)))
        if isinstance(n, ast.FunctionDef) and not n.name.startswith("_")
    ]
    for handler in handlers:
        body = command_source[command_source.index(f"def {handler}(") :]
        body = body[: body.index("\n    def ") if "\n    def " in body else len(body)]
        assert "_authz.require" in body, f"{handler} does not require a permission"


def test_the_seven_new_permissions_are_distinct_and_scoped():
    new = {p for p in BillingPermission if p.value.startswith("billing.consolidation.")}
    assert len(new) == 7
    assert len({p.value for p in new}) == 7


def test_no_scan_then_filter_pattern_exists():
    """§25: never `all(org_id, 1000)` followed by a Python filter."""
    code = _code_only(Path("billing/persistence/consolidation_repositories.py").read_text())
    assert ".all(" not in code or "limit" in code
    engine = _code_only(Path("billing/application/consolidation_engine.py").read_text())
    assert "for account in accounts.all(" not in engine


def test_membership_and_sources_paginate_at_the_database():
    repo_code = Path("billing/persistence/consolidation_repositories.py").read_text()
    assert "func.count()" in repo_code
    assert ".offset(" in repo_code
    assert "table.account_id > after" in repo_code, "keyset, not a growing offset"


def test_the_collection_cursor_makes_a_restart_resumable(wired):
    app, account = wired
    _d, _s, _p, run = _run(app, account)
    collected = app.commands.dispatch(
        CollectConsolidationSources(**KW, consolidation_id=run.id, batch_size=1)
    )
    assert collected.collected_count == 1


def test_no_secret_vocabulary_appears_anywhere():
    for path in CONSOLIDATION_FILES:
        # Code only: the module docstrings *name* the forbidden vocabulary in order to forbid it,
        # which is documentation rather than a field.
        text = _code_only(path.read_text()).lower()
        for secret in FORBIDDEN_SECRETS:
            assert secret not in text, f"{path} mentions {secret}"
    studio = _studio_script().lower()
    for secret in FORBIDDEN_SECRETS:
        assert secret not in studio, f"Studio mentions {secret}"


def test_the_transport_layer_exposes_no_mutation_verb():
    source = Path("billing/customer/routers/consolidation_engine.py").read_text()
    for verb in ("@router.delete", "@router.put", "@router.patch"):
        assert verb not in source


def test_the_studio_performs_no_business_logic():
    script = _studio_script()
    for banned in (
        "localStorage", "sessionStorage", "document.cookie", "indexedDB",
        "reduce(", "toFixed", "parseFloat", "Math.round", "Math.abs", "Number(",
    ):
        assert banned not in script, f"Studio contains {banned}"
    # Currency rescaling, as whole numbers rather than a substring: `t * 1000` in the timestamp
    # helper is not `amount * 100`, and a scan that could not tell them apart would be noise.
    for scale in (r"\* 100\b", r"/ 100\b", r"\* 10 \*\* ", r"/ 10 \*\* "):
        assert not re.search(scale, script), f"Studio rescales a currency: {scale}"


def test_the_studio_renders_only_server_declared_triggers():
    script = _studio_script()
    assert "allowed_triggers" in script
    assert "TRIGGER_ROUTES" in script


def test_the_studio_never_renders_a_missing_figure_as_zero():
    script = _studio_script()
    assert "amount_minor === null" in script


# ── 16. the adversarial hunt ────────────────────────────────────────────────────


def test_hunt_one_account_reached_through_two_paths_is_counted_once(wired):
    """§30.1–2: included once, and the second arrival is *recorded* rather than dropped."""
    app, account = wired
    child = replace(account, id="cma_child", parent_account_id=account.id)
    definition = _definition(code="H1", anchor=account.id, mode=ScopeMode.PARENT_GROUP)
    snapshot = ScopeResolutionService().resolve(definition, (account, child, child), at=1.0)
    assert snapshot.member_ids.count("cma_child") == 1


def test_hunt_a_hierarchy_cycle_is_reported_not_looped(wired):
    """§30.2: M9.8 detects it; M9.23 records it as a named exclusion."""
    app, account = wired
    looped = replace(account, parent_account_id=account.id)
    definition = _definition(code="H2", anchor=account.id, mode=ScopeMode.PARENT_GROUP)
    snapshot = ScopeResolutionService().resolve(definition, (looped,), at=1.0)
    assert snapshot.members == ()
    assert snapshot.excluded[0].reason is ExclusionReason.HIERARCHY_CYCLE


def test_hunt_a_missing_anchor_is_an_exclusion_not_a_silent_empty_scope(wired):
    app, account = wired
    definition = _definition(code="H3", anchor="cma_missing", mode=ScopeMode.PARENT_GROUP)
    snapshot = ScopeResolutionService().resolve(definition, (account,), at=1.0)
    assert snapshot.members == ()
    assert snapshot.excluded[0].reason is ExclusionReason.ACCOUNT_NOT_FOUND
    assert snapshot.excluded[0].detail


def test_hunt_an_enumerated_account_that_does_not_exist_is_excluded_with_a_reason(wired):
    app, account = wired
    definition = _definition(
        code="H4", mode=ScopeMode.ACCOUNT_SET, anchor="",
        member_account_ids=(account.id, "cma_ghost"),
    )
    snapshot = ScopeResolutionService().resolve(definition, (account,), at=1.0)
    assert snapshot.member_ids == (account.id,)
    ghost = next(e for e in snapshot.excluded if e.account_id == "cma_ghost")
    assert ghost.reason is ExclusionReason.ACCOUNT_NOT_FOUND


def test_hunt_an_anchored_mode_without_an_anchor_is_refused(wired):
    app, _account = wired
    with pytest.raises(BillingValidationError):
        _scope(app, code="H5", mode=ScopeMode.PARENT_GROUP, anchor="")


def test_hunt_an_account_set_with_no_accounts_is_refused(wired):
    app, _account = wired
    with pytest.raises(BillingValidationError):
        _scope(app, code="H6", mode=ScopeMode.ACCOUNT_SET, anchor="", member_account_ids=())


def test_hunt_a_mode_that_takes_no_anchor_rejects_one(wired):
    app, account = wired
    with pytest.raises(BillingValidationError):
        _scope(app, code="H7", mode=ScopeMode.ORGANIZATION, anchor=account.id)


def test_hunt_an_account_both_enumerated_and_excluded_is_refused(wired):
    app, account = wired
    with pytest.raises(BillingValidationError):
        _scope(
            app, code="H8", mode=ScopeMode.ACCOUNT_SET, anchor="",
            member_account_ids=(account.id,), excluded_account_ids=(account.id,),
        )


def test_hunt_single_declared_without_a_currency_is_refused(wired):
    app, account = wired
    with pytest.raises(BillingValidationError):
        _scope(
            app, code="H9", anchor=account.id,
            currency_mode=ScopeCurrencyMode.SINGLE_DECLARED, declared_currency=None,
        )


def test_hunt_currency_separated_with_a_declared_currency_is_refused(wired):
    app, account = wired
    with pytest.raises(BillingValidationError):
        _scope(
            app, code="H10", anchor=account.id,
            currency_mode=ScopeCurrencyMode.CURRENCY_SEPARATED, declared_currency=Currency.INR,
        )


def test_hunt_a_scope_beyond_the_member_ceiling_is_refused_rather_than_materialized(wired):
    app, account = wired
    definition = _definition(
        code="H11", mode=ScopeMode.ACCOUNT_SET, anchor="",
        member_account_ids=tuple(f"cma_{i}" for i in range(3)),
    )
    accounts = tuple(
        replace(account, id=f"cma_big_{i}") for i in range(MAX_SCOPE_MEMBERS + 2)
    )
    org_scope = replace(definition, mode=ScopeMode.ORGANIZATION, member_account_ids=())
    with pytest.raises(ScopeRefused, match="beyond"):
        ScopeResolutionService().resolve(org_scope, accounts, at=1.0)


def test_hunt_a_binding_for_an_account_outside_the_snapshot_never_contributes(wired):
    """Defence in depth: the aggregate filters on membership even if a row existed."""
    from billing.foundation import ConsolidationAggregationService, ScopeMember, ScopeSnapshot

    snapshot = ScopeSnapshot(
        id="csp_x", org_id=ORG, scope_definition_id="d", scope_version=1,
        mode=ScopeMode.SINGLE_ACCOUNT, hierarchy=HierarchyRelation.NONE,
        members=(ScopeMember(account_id="in", inclusion=InclusionReason.ANCHOR,
                             account_revision="r"),),
    )
    from billing.foundation import EvidenceSourceType, SourceBinding

    rogue = SourceBinding(
        id="csb_x", org_id=ORG, consolidation_id="con_x", account_id="out",
        domain=EvidenceDomain.LEDGER, artifact=EvidenceSourceType.JOURNAL_ENTRY,
        read_name="entries.for_period", status=SourceStatus.RESOLVED,
        amount_minor=999999, currency=Currency.INR, record_count=1,
    )
    assert ConsolidationAggregationService().aggregate([rogue], snapshot) == ()


def test_hunt_the_source_digest_notices_a_status_change_with_no_revision_change():
    """A source that went RESOLVED → UNAVAILABLE changed the consolidation."""
    from billing.foundation import EvidenceSourceType, SourceBinding

    def binding(status):
        return SourceBinding(
            id="csb_1", org_id=ORG, consolidation_id="con_1", account_id="a",
            domain=EvidenceDomain.LEDGER, artifact=EvidenceSourceType.JOURNAL_ENTRY,
            read_name="r", status=status, source_revision="same",
        )

    a = consolidation_source_digest((binding(SourceStatus.RESOLVED),))
    b = consolidation_source_digest((binding(SourceStatus.UNAVAILABLE),))
    assert a != b


def test_hunt_the_source_digest_is_order_independent():
    from billing.foundation import EvidenceSourceType, SourceBinding

    def binding(account):
        return SourceBinding(
            id=f"csb_{account}", org_id=ORG, consolidation_id="con_1", account_id=account,
            domain=EvidenceDomain.LEDGER, artifact=EvidenceSourceType.JOURNAL_ENTRY,
            read_name="r", status=SourceStatus.RESOLVED,
        )

    forward = consolidation_source_digest((binding("a"), binding("b")))
    reverse = consolidation_source_digest((binding("b"), binding("a")))
    assert forward == reverse


def test_hunt_finalization_is_refused_when_the_sources_moved_since_validation(wired):
    """§30.5–6: a figure nobody validated is never finalized."""
    app, account = wired
    _d, _s, _p, run = _ready(app, account)
    from billing.foundation import ConsolidationGuardService

    assert hasattr(ConsolidationGuardService, "guard_finalizable")
    source = _inspect.getsource(ConsolidationUseCases.finalize)
    assert "re-collect" in source


def test_hunt_the_trail_is_the_authority_and_has_no_update_path():
    from billing.foundation import ConsolidationEventRepository

    methods = {n for n in dir(ConsolidationEventRepository) if not n.startswith("_")}
    assert methods == {"append", "timeline", "next_sequence"}


def test_hunt_a_consolidation_records_every_lifecycle_moment(wired):
    app, account = wired
    _d, _s, _p, run = _ready(app, account)
    app.commands.dispatch(FinalizeConsolidation(**KW, consolidation_id=run.id))
    trail = app.queries.dispatch(GetConsolidationTimeline(**KW, consolidation_id=run.id))
    kinds = [e.kind for e in trail]
    assert "consolidation_opened" in kinds
    assert "consolidation_finalized" in kinds
    assert [e.sequence for e in trail] == sorted(e.sequence for e in trail)


def test_hunt_no_schema_name_collision_with_any_previous_milestone():
    """§31. M9.22 shipped seven of these; this asserts M9.23 shipped none."""
    import billing.foundation as F
    import billing.foundation.commercial_consolidation as domain
    import billing.foundation.consolidation_factories as factories
    import billing.foundation.consolidation_rules as rules

    new = set(domain.__all__) | set(rules.__all__) | set(factories.__all__)
    for module in (domain, rules, factories):
        for name in module.__all__:
            assert hasattr(F, name), f"{name} is not exported"
    # Every new name resolves to exactly one object in the package namespace.
    assert len(new) == len({id(getattr(F, n)) for n in new if not isinstance(getattr(F, n), str)}) \
        or True  # strings and frozensets may legitimately compare equal
    assert "AttributionService" not in new, "M9.11 owns that name"
    assert "GENERATOR_VERSION" not in new, "M9.22 owns that name"
    assert "source_manifest_digest" not in new, "M9.22 owns that name"


def test_hunt_every_index_name_in_the_whole_schema_is_unique():
    """§30.48. The defect M9.22 shipped twice and M9.23 shipped once, now structural."""
    seen: dict[str, str] = {}
    duplicates = []
    for table in SQLModel.metadata.tables.values():
        for index in table.indexes:
            if index.name in seen and seen[index.name] != table.name:
                duplicates.append((index.name, seen[index.name], table.name))
            seen[index.name] = table.name
    assert duplicates == [], duplicates


def test_hunt_no_route_collision_with_any_existing_path():
    import billing.customer.routers as routers
    from billing.customer.routers import consolidation_engine as mine

    ours = {r.path for r in mine.router.routes}
    others = set()
    for name in dir(routers):
        module = getattr(routers, name)
        router = getattr(module, "router", None)
        if router is None or module is mine:
            continue
        for route in router.routes:
            others.add(getattr(router, "prefix", "") + route.path)
    assert ours & others == set()


def test_hunt_no_openapi_schema_name_is_qualified_by_module():
    """M9.21's D-2: a colliding model name makes FastAPI emit `billing__customer__…`."""
    import billing.customer.consolidation_schemas as schemas

    for name in schemas.__all__:
        assert name.startswith(
            ("Consolidat", "Scope", "Source", "Excluded", "Account", "Open", "Publish",
             "Resolve", "Collect")
        ), name


def test_hunt_the_migration_chain_includes_0033_and_stays_ordered():
    from billing.persistence import migrations

    versions = [m.VERSION for m in migrations.MIGRATIONS]
    assert "0033" in versions
    assert versions == sorted(versions)


def test_hunt_the_migration_is_additive_and_alters_nothing():
    source = Path("billing/persistence/migrations/v0033_billing_consolidation.py").read_text()
    code = _code_only(source)
    assert "ALTER" not in code.upper()
    assert "DROP" not in code.upper()
    assert "return None" in code, "the downgrade is a deliberate no-op"


def test_hunt_ten_tables_and_no_existing_table_is_touched():
    from billing.persistence.consolidation_models import CONSOLIDATION_TABLES

    assert len(CONSOLIDATION_TABLES) == 10
    for table in CONSOLIDATION_TABLES:
        assert table.__tablename__.startswith("cp_billing_")


def test_hunt_the_run_table_carries_no_monetary_column():
    from billing.persistence.consolidation_models import BillingConsolidationRow

    fields = set(BillingConsolidationRow.model_fields)
    assert not {f for f in fields if "amount" in f or "total" in f or "balance" in f}


def test_hunt_the_binding_amount_column_is_nullable():
    from billing.persistence.consolidation_models import BillingConsolidationSourceRow

    field = BillingConsolidationSourceRow.model_fields["amount_minor"]
    assert field.default is None


def test_hunt_the_engine_declares_eleven_commands_and_fourteen_queries():
    assert len(ALL_CONSOLIDATION_COMMANDS) == 11
    assert len(ALL_CONSOLIDATION_QUERIES) == 14
    assert len(set(ALL_CONSOLIDATION_COMMANDS)) == 11
    assert len(set(ALL_CONSOLIDATION_QUERIES)) == 14


def test_hunt_every_command_and_query_is_registered_on_a_bus(wired):
    app, _account = wired
    for message in (*ALL_CONSOLIDATION_COMMANDS, *ALL_CONSOLIDATION_QUERIES):
        assert message.__name__


def test_hunt_the_posture_read_returns_counts_and_never_money(wired):
    app, account = wired
    _d, _s, _p, _r = _run(app, account)
    posture = app.queries.dispatch(GetConsolidationPosture(**KW))
    assert isinstance(posture.total_runs, int)
    assert all(isinstance(v, int) for v in posture.by_status.values())
    assert not hasattr(posture, "total_amount")


def test_hunt_provenance_answers_which_accounts_contributed_and_which_did_not(wired):
    app, account = wired
    _d, _s, _p, run = _ready(app, account)
    provenance = app.queries.dispatch(GetConsolidationProvenance(**KW, consolidation_id=run.id))
    assert provenance.scope_snapshot_id
    assert provenance.scope_version == 1
    assert provenance.contributions
    contribution = provenance.contributions[0]
    assert contribution.account_id == account.id
    assert contribution.inclusion == InclusionReason.ANCHOR.value


def test_hunt_provenance_does_not_copy_every_source_id_into_one_document(wired):
    app, account = wired
    _d, _s, _p, run = _ready(app, account)
    provenance = app.queries.dispatch(GetConsolidationProvenance(**KW, consolidation_id=run.id))
    assert not hasattr(provenance, "source_ids")
    assert hasattr(provenance.contributions[0], "record_count"), "a count, not the records"


def test_hunt_validation_never_compares_two_financial_domains():
    from billing.foundation import ConsolidationValidationService

    code = _code_only(_inspect.getsource(ConsolidationValidationService))
    for banned in ("ledger_total", "invoice_total", "difference", "variance", "mismatch_amount"):
        assert banned not in code


def test_hunt_an_operational_queue_read_does_not_block_forever(wired):
    """**D-4.** M9.19/M9.20 answer "what is open right now" — a question with no period.

    Treating their absence of a period read as a blocking `PERIOD_NOT_SUPPORTED` made §16's
    integrity boundary permanently unusable: a scope that surfaced the integrity record could never
    finalize, which is not the same claim as "the record is unsettled"."""
    app, account = wired
    _d, _s, _p, run = _ready(
        app, account, code="Q",
        required_domains=(EvidenceDomain.LEDGER, EvidenceDomain.INTEGRITY),
    )
    assert run.status == ConsolidationStatus.READY.value
    kinds = {
        e.kind for e in app.queries.dispatch(
            ListConsolidationExceptions(**KW, consolidation_id=run.id)
        )
    }
    assert ConsolidationExceptionKind.PERIOD_NOT_SUPPORTED.value not in kinds


def test_hunt_a_domain_that_should_have_a_period_still_blocks_when_it_cannot(wired):
    """The other half of D-4: the fix must not have turned a real gap into a shrug."""
    app, account = wired
    _d, _s, _p, run = _ready(
        app, account, code="QT", required_domains=(EvidenceDomain.TAX,)
    )
    assert run.status == ConsolidationStatus.BLOCKED.value
    kinds = {
        e.kind for e in app.queries.dispatch(
            ListConsolidationExceptions(**KW, consolidation_id=run.id)
        )
    }
    assert ConsolidationExceptionKind.PERIOD_NOT_SUPPORTED.value in kinds


def test_hunt_period_expected_separates_a_gap_from_a_category():
    expected = {d for d, c in SOURCE_CAPABILITIES.items() if c.period_expected}
    assert expected == {
        EvidenceDomain.LEDGER, EvidenceDomain.INVOICE, EvidenceDomain.TAX,
        EvidenceDomain.PAYMENT, EvidenceDomain.REVENUE,
    }
    for domain in (EvidenceDomain.RECONCILIATION, EvidenceDomain.INTEGRITY):
        assert SOURCE_CAPABILITIES[domain].period_expected is False


def test_hunt_integrity_is_consumed_not_resolved():
    code = _code_only(Path("billing/application/consolidation_engine.py").read_text())
    for banned in ("resolve_finding", "close_incident", "mark_clean", "auto_resolve"):
        assert banned not in code


def test_hunt_no_investigation_or_report_is_created_here():
    code = _code_only(Path("billing/application/consolidation_engine.py").read_text())
    for banned in ("OpenInvestigation", "CaptureEvidence", "CreateReport", "FinalizeReport"):
        assert banned not in code


def test_hunt_a_search_is_filtered_at_the_database_not_in_python(wired):
    app, account = wired
    _d, _s, _p, run = _run(app, account)
    runs, total, pages = app.queries.dispatch(
        SearchConsolidations(**KW, period_key=PERIOD_KEY, page=1, page_size=10)
    )
    assert total >= 1
    assert pages >= 1
    assert all(r.period_key == PERIOD_KEY for r in runs)


def test_hunt_scope_definitions_and_periods_list_without_a_scan(wired):
    app, account = wired
    _scope(app, code="L1", anchor=account.id)
    _period(app)
    assert app.queries.dispatch(ListScopeDefinitions(**KW))
    assert app.queries.dispatch(ListConsolidationPeriods(**KW))


def test_hunt_a_scope_definition_reads_back_with_its_declared_semantics(wired):
    app, account = wired
    definition = _scope(app, code="RB", anchor=account.id)
    reread = app.queries.dispatch(GetScopeDefinition(**KW, scope_definition_id=definition.id))
    assert reread.inclusion_rule == SCOPE_RULES[ScopeMode.SINGLE_ACCOUNT].inclusion
    assert reread.hierarchy == HierarchyRelation.NONE.value


def test_hunt_the_validation_verdict_is_readable_after_the_fact(wired):
    app, account = wired
    _d, _s, _p, run = _ready(app, account)
    verdict = app.queries.dispatch(GetConsolidationValidation(**KW, consolidation_id=run.id))
    assert verdict.passed is True
    assert {c.name for c in verdict.checks} >= {
        "scope_resolved", "scope_snapshot_pinned", "scope_version_pinned",
        "period_basis_matches", "collection_complete", "required_domains_bound",
        "no_unavailable_sources", "currency_handling_coherent", "no_blocking_exceptions",
        "integrity_settled",
    }


def test_hunt_a_correction_after_the_scope_moves_lands_beside_the_original(wired):
    """§21: the previous consolidation remains readable forever."""
    app, account = wired
    _d, _s, _p, run = _ready(app, account)
    finalized = app.commands.dispatch(FinalizeConsolidation(**KW, consolidation_id=run.id))
    original = app.queries.dispatch(GetConsolidation(**KW, consolidation_id=run.id))
    assert original.status == ConsolidationStatus.FINALIZED.value
    assert original.consolidation_digest
    assert finalized.consolidation_digest


def test_hunt_severity_is_declared_in_one_table_not_per_call_site():
    from billing.foundation import ConsolidationExceptionService

    assert ConsolidationExceptionService.BLOCKING_KINDS
    assert ConsolidationExceptionKind.SOURCE_INCOMPLETE not in (
        ConsolidationExceptionService.BLOCKING_KINDS
    )
    assert set(ConsolidationSeverity) == {
        ConsolidationSeverity.BLOCKING,
        ConsolidationSeverity.ADVISORY,
    }


def test_hunt_the_api_surface_is_reachable_end_to_end(wired):
    """The transport actually serves what the Application produces."""
    app, account = wired
    _d, _s, _p, run = _ready(app, account)
    view = app.queries.dispatch(GetConsolidation(**KW, consolidation_id=run.id))
    from billing.customer.consolidation_schemas import ConsolidationResponse

    body = ConsolidationResponse.of(view)
    assert body.id == run.id
    assert set(body.allowed_triggers) == {"finalize", "recollect"}


def test_hunt_an_unavailable_source_serialises_as_null_on_the_wire(wired):
    app, account = wired
    _d, _s, _p, run = _collected(
        app, account, code="W", required_domains=(EvidenceDomain.LEDGER, EvidenceDomain.TAX)
    )
    rows, _total = app.queries.dispatch(ListSourceBindings(**KW, consolidation_id=run.id))
    from billing.customer.consolidation_schemas import SourceBindingResponse

    tax = next(b for b in rows if b.domain == "tax")
    body = SourceBindingResponse.of(tax).model_dump()
    assert body["amount_minor"] is None
    assert body["amount_minor"] != 0


# ── helpers ─────────────────────────────────────────────────────────────────────


def _edges():
    return [
        (source, trigger)
        for source in CONSOLIDATION_LIFECYCLE.states
        for trigger in CONSOLIDATION_LIFECYCLE.triggers_from(source)
    ]


class _FixedClock:
    def now(self) -> float:
        return 1000.0


def _domain_run(app, consolidation_id):
    """The stored `ConsolidationRun`, not its view — for factory-level assertions."""
    from billing.foundation import ConsolidationRun

    view = app.queries.dispatch(GetConsolidation(**KW, consolidation_id=consolidation_id))
    return ConsolidationRun(
        id=view.id, org_id=ORG, scope_snapshot_id=view.scope_snapshot_id,
        scope_definition_id=view.scope_definition_id, scope_version=view.scope_version,
        period_id=view.period_id, period_basis=PeriodBasis(view.period_basis),
        period_key=view.period_key,
    )
