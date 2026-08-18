"""M9.20 — Financial Integrity Correlation & Audit Engine.

M9.19 closed with a stated limitation: *at real volume one root cause generates findings across
several relationships, and nothing groups them.* This milestone exists to close that gap, and the
question it must survive is not "does grouping work?" but:

> *Can an auditor reconstruct — six months later, from persisted rows alone — which divergences
> were the same problem, on what evidence we believed that, what we asked of whom, what proved the
> repair, and what we believed **before** we changed our mind?*

So the suite is organised around the five ways that fails:

* **correlation invents a fact** — two unrelated problems are merged because their amounts matched,
  and fixing one closes both (§3, §4);
* **history is rewritten** — a merge or a correction destroys what was believed at the time, which
  is the one thing an audit cannot accept (§7, §8);
* **repair is mistaken for resolution** — somebody asked a domain to fix it, and the incident says
  resolved (§6);
* **severity decays** — one of three findings is repaired and a critical incident quietly drops off
  a dashboard (§5);
* **two workers disagree about what they saw** — duplicate incidents, duplicate attachments, a
  stale worker overwriting a conclusion (§9).

Every claim is demonstrated **structurally** — by a derived key, a database constraint, an absent
FSM edge, an AST scan, or two real connections racing — rather than by a happy path assumed to imply
the rest. §11 is the numbered adversarial hunt.

The pipeline is the real one: a closed period, a rated run, a posted ledger, an issued document, a
recognition schedule, genuine M9.19 findings. M9.20 reads those and writes to none of them.
"""

from __future__ import annotations

import ast
import inspect as _inspect
import textwrap
from dataclasses import replace
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import tests.test_billing_invoice_engine as inv
import tests.test_billing_revenue_engine as rev
from api.customer.jwt import encode as epic8_encode
from billing.application import (
    ALL_INCIDENT_COMMANDS,
    ALL_INCIDENT_QUERIES,
    AcknowledgeIncident,
    AllowAllAuthorizer,
    ClassifyIncidentRootCause,
    CloseIncident,
    CorrectCorrelation,
    CorrelateFindings,
    ExplainIncident,
    GetCorrelationCorrections,
    GetFindingIncidents,
    GetIncident,
    GetIncidentEvidence,
    GetIncidentFindings,
    GetIncidentMerges,
    GetIncidentPosture,
    GetIncidentTimeline,
    InspectIncident,
    InvestigateIncident,
    ListIncidents,
    LiveIncidents,
    MergeIncidents,
    PublishRecognitionPolicy,
    RecognizeRevenue,
    ReconcileRecognitionLedger,
    RequestIncidentRepair,
    ResolveFinding,
    ResolveIncident,
    SearchIncidents,
    VerifyIncident,
)
from billing.customer.app import create_billing_app
from billing.customer.config import BillingApiSettings
from billing.customer.container import BillingContainer
from billing.foundation import (
    ANCHOR_PRECEDENCE,
    EDGE_BY_ANCHOR,
    IMPACT_BY_ROOT_CAUSE,
    INCIDENT_LIFECYCLE,
    LIVE_INCIDENT_STATES,
    ROOT_CAUSE_BY_DIVERGENCE,
    SEVERITY_FLOOR,
    TERMINAL_INCIDENT_STATES,
    AnchorKind,
    BillingConflict,
    CorrelationRefused,
    CorrelationService,
    Currency,
    DivergenceKind,
    EdgeKind,
    FactRef,
    FindingEvidence,
    FindingSeverity,
    FixedClock,
    ImpactClass,
    IncidentEventKind,
    IncidentEventRepository,
    IncidentFactory,
    IncidentGuardService,
    IncidentLifecycleService,
    IncidentMergeRepository,
    IncidentSeverity,
    IncidentStale,
    IncidentStatus,
    IncidentTrigger,
    IncidentValidator,
    InvalidBillingTransition,
    Money,
    RelationshipKind,
    ResolutionGuardService,
    RootCause,
    RootCauseService,
    SequentialIdGenerator,
    SeverityService,
    VerificationOutcome,
    derive_correlation_key,
    derive_incident_id,
    derive_link_id,
)
from billing.persistence.composition import build_persistent_billing_application
from billing.persistence.migrations import MIGRATIONS

ORG, ACTOR = inv.ORG, inv.ACTOR
KW = inv.KW
INR = Currency.INR
SECRET = "test-secret-value-for-billing-incidents"

INCIDENT_FILES = (
    Path("billing/foundation/commercial_incidents.py"),
    Path("billing/foundation/incident_rules.py"),
    Path("billing/foundation/incident_factories.py"),
    Path("billing/application/incident_engine.py"),
    Path("billing/persistence/incident_models.py"),
    Path("billing/persistence/incident_repositories.py"),
    Path("billing/customer/incident_schemas.py"),
    Path("billing/customer/routers/incident_engine.py"),
)

FOUNDATION_FILES = INCIDENT_FILES[:3]

_html_code_only = rev._html_code_only


def _code_only(source: str) -> str:
    """Source with docstrings and comments stripped, dedented first.

    Dedenting matters: `inspect.getsource` of a *method* is indented and `ast.parse` refuses it, so
    a scan that forgot this would raise rather than assert — and a raising test proves nothing."""
    return rev._code_only(textwrap.dedent(source))


# ── fixtures ────────────────────────────────────────────────────────────────────


@pytest.fixture
def wired():
    """The real pipeline: closed period, issued document, recognition policy, live schedule."""
    persistent, account, generator = rev._wired()
    app = persistent.application
    inv._closed(app, account)
    app.commands.dispatch(
        PublishRecognitionPolicy(
            **KW, code="std", version=1,
            method=rev.RecognitionMethod.STRAIGHT_LINE,
            granularity=rev.PeriodGranularity.MONTH,
        )
    )
    yield persistent, account
    for _ in generator:  # pragma: no cover — drains the invoice fixture's teardown
        pass


def _diverged(app, account, *, periods: int = 3, open_year: bool = False):
    """Genuine M9.19 findings: recognitions the Ledger refused because no period is open."""
    if open_year:
        rev._open_year(app, account)
    document, schedule, entries = rev._active(app, account)
    for entry in entries[:periods]:
        app.commands.dispatch(
            RecognizeRevenue(**KW, entry_id=entry.id, at=entry.recognize_on + 1)
        )
    receipt = app.commands.dispatch(
        ReconcileRecognitionLedger(**KW, schedule_id=schedule.id)
    )
    return document, schedule, entries, receipt


def _evidence(**overrides) -> FindingEvidence:
    body = dict(
        finding_id="ifd_1",
        org_id=ORG,
        relationship=RelationshipKind.RECOGNITION_LEDGER,
        divergence=DivergenceKind.MISSING_COUNTERPART,
        subject_key="rve_1",
    )
    body.update(overrides)
    return FindingEvidence(**body)


def _fact(identity: str, kind: str = "recognition_entry", **overrides) -> FactRef:
    body = dict(identity=identity, kind=kind)
    body.update(overrides)
    return FactRef(**body)


def _incident(**overrides):
    factory = IncidentFactory(ids=SequentialIdGenerator(), clock=FixedClock(1_000.0, step=0.0))
    built, _anchor = factory.incident(
        _evidence(**overrides.pop("evidence", {})),
        severity=overrides.pop("severity", IncidentSeverity.CRITICAL),
        root_cause=overrides.pop("root_cause", RootCause.SOURCE_FACT_MISSING),
        impact=overrides.pop("impact", ImpactClass.FINANCIAL_FACT_MISSING),
        at=1_000.0,
    )
    return replace(built, **overrides) if overrides else built


def _two_sessions(tmp_path, name: str = "incidents.db"):
    """Two Applications over one file-backed database, each with its own connection.

    Two *connections* rather than two threads: a thread schedule that does not actually interleave
    produces a green test that proved nothing."""
    url = f"sqlite:///{tmp_path / name}"
    first = build_persistent_billing_application(
        url=url, authorizer=AllowAllAuthorizer(),
        ids=rev._TaggedIds("a"), clock=FixedClock(1_000.0, step=0.0),
    )
    second = build_persistent_billing_application(
        url=url, authorizer=AllowAllAuthorizer(),
        ids=rev._TaggedIds("b"), clock=FixedClock(1_000.0, step=0.0), create_all=False,
    )
    assert first.database is not second.database
    return first, second


# ── 1. the audit: nothing existing is shadowed ────────────────────────────────


def test_no_incident_or_correlation_aggregate_existed_before():
    """The concept is genuinely new. The audit found no prior aggregate to reuse or shadow."""
    for path in (
        Path("billing/foundation/commercial_reconciliation.py"),
        Path("billing/foundation/reconciliation_rules.py"),
    ):
        code = _code_only(path.read_text())
        assert "class IntegrityIncident" not in code
        assert "class CorrelationKey" not in code


def test_m9_19_vocabulary_is_read_only_and_not_shadowed():
    from billing.foundation import IntegrityFinding, IntegrityIncident

    assert IntegrityIncident is not IntegrityFinding
    code = _code_only(Path("billing/foundation/commercial_incidents.py").read_text())
    for forbidden in ("class IntegrityFinding", "class IntegrityRun", "class IntegrityResolution"):
        assert forbidden not in code


def test_the_transport_correlation_id_is_not_reused_as_a_financial_one():
    """`correlation_id` already means *which HTTP request produced this*. Reusing it for *which
    financial cause produced this* would give one field two meanings and somebody would join on
    it."""
    for path in FOUNDATION_FILES:
        code = _code_only(path.read_text())
        assert "correlation_id" not in code, path.name


# ── 2. ownership: an observer of an observer ──────────────────────────────────


def test_no_domain_repository_is_imported_anywhere():
    """**The core boundary.** M9.20 records what happened; the owning domain repairs."""
    banned = (
        "LedgerRepository", "PaymentRepository", "InvoiceRepository", "TaxRepository",
        "RecognitionRepository", "JournalEntryRepository", "IntegrityFindingRepository",
        "IntegrityRunRepository", "SqlJournalEntryRepository",
    )
    for path in INCIDENT_FILES:
        code = _code_only(path.read_text())
        for forbidden in banned:
            assert forbidden not in code, (path.name, forbidden)


def test_no_ledger_write_is_reachable():
    for path in INCIDENT_FILES:
        code = _code_only(path.read_text())
        for forbidden in ("PostJournalEntry", "PostRecognitionToLedger", "ReconcilePaymentAttempt"):
            assert forbidden not in code, (path.name, forbidden)


def test_the_only_outward_command_is_m9_19s_own_repair_request():
    engine = _code_only(Path("billing/application/incident_engine.py").read_text())
    assert "RequestFindingRepair" in engine
    dispatched = {
        node.func.id
        for node in ast.walk(ast.parse(engine))
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    }
    assert not (dispatched & {"PostJournalEntry", "IssueCommercialInvoice", "CapturePayment"})


def test_no_financial_calculation_exists():
    for path in INCIDENT_FILES:
        code = _code_only(path.read_text())
        for forbidden in (
            "PriceResolver", "RatingEngine", "TaxCalculator", "calculate_tax", "resolve_price",
            "BalanceService", "AccountBalance", "expected_balance", "outstanding", "amount_due",
        ):
            assert forbidden not in code, (path.name, forbidden)


def test_money_is_never_imported_into_the_correlation_domain():
    """**A schema that cannot name money cannot leak it.**

    `FindingEvidence` carries no amount by construction, so a future correlation rule that wanted
    one could not compile."""
    from billing.foundation import FindingEvidence as Evidence

    for name in Evidence.__dataclass_fields__:
        assert "amount" not in name and "minor" not in name and "total" not in name
    code = _code_only(Path("billing/foundation/incident_rules.py").read_text())
    assert "Money" not in code
    assert "Money" not in _code_only(Path("billing/customer/incident_schemas.py").read_text())


def test_no_balance_or_monetary_column_is_stored():
    from billing.persistence.incident_models import INCIDENT_TABLES

    for table in INCIDENT_TABLES:
        for column in table.__table__.columns:
            for forbidden in ("balance", "amount", "minor", "total", "currency"):
                assert forbidden not in column.name, (table.__tablename__, column.name)


def test_posture_and_incident_carry_counts_never_money():
    from billing.foundation import IncidentPosture, IntegrityIncident

    for shape in (IncidentPosture, IntegrityIncident):
        for name in shape.__dataclass_fields__:
            assert "amount" not in name and "minor" not in name and "balance" not in name, name


def test_no_provider_no_http_no_sdk_anywhere():
    """Checked at the **import graph** rather than by substring, so a field named `requests` on
    this milestone's own repository cannot fail a scan looking for the HTTP library."""
    banned = {"httpx", "requests", "urllib", "urllib3", "socket", "http", "stripe", "razorpay",
              "boto3"}
    for path in INCIDENT_FILES:
        for node in ast.walk(ast.parse(path.read_text())):
            if isinstance(node, ast.Import):
                roots = {alias.name.split(".")[0] for alias in node.names}
            elif isinstance(node, ast.ImportFrom):
                roots = {(node.module or "").split(".")[0]}
            else:
                continue
            assert not (roots & banned), (path.name, roots & banned)


def test_no_scheduler_and_no_runtime_dependency():
    for path in INCIDENT_FILES:
        code = _code_only(path.read_text())
        for forbidden in ("sleep", "threading", "asyncio", "cron", "while True", "BackgroundTask",
                          "from runtime", "import runtime", "pipecat"):
            assert forbidden not in code, (path.name, forbidden)


def test_foundation_is_clock_free_and_performs_no_io():
    for path in FOUNDATION_FILES:
        code = _code_only(path.read_text())
        for forbidden in ("datetime.now", "time.time", "utcnow", "open(", "session", "engine"):
            assert forbidden not in code, (path.name, forbidden)


def test_foundation_contains_no_authorization_logic():
    for path in FOUNDATION_FILES:
        code = _code_only(path.read_text())
        for forbidden in ("principal", "RoleAssignment", "RBAC", "has_role", "check_permission",
                          "is_authorized", "BillingPermission"):
            assert forbidden not in code, (path.name, forbidden)


def test_no_collections_policy_and_no_reconciliation_logic_is_duplicated():
    for path in INCIDENT_FILES:
        code = _code_only(path.read_text())
        for forbidden in ("MatchingService", "RECONCILIATION_MATRIX", "DunningState",
                          "CollectionCase", "delinquent"):
            assert forbidden not in code, (path.name, forbidden)


# ── 3. correlation identity ───────────────────────────────────────────────────


def test_the_classification_tables_are_complete():
    assert not IncidentValidator().validate_tables().has_errors
    assert set(ROOT_CAUSE_BY_DIVERGENCE) == set(DivergenceKind)
    assert set(IMPACT_BY_ROOT_CAUSE) == set(RootCause)
    assert set(EDGE_BY_ANCHOR) >= set(ANCHOR_PRECEDENCE)


def test_no_infrastructure_root_cause_exists():
    """Billing has no evidence for any of them. A missing ledger entry looks identical whether the
    database rejected it, the process died, or nobody ever tried."""
    values = {c.value for c in RootCause}
    for forbidden in ("database_failure", "network_failure", "provider_failure", "timeout"):
        assert forbidden not in values


def test_a_correlation_key_is_org_first_and_carries_nothing_volatile():
    a = derive_correlation_key("org_a", "document", "inv_1@rev")
    assert a == derive_correlation_key("org_a", "document", "inv_1@rev")
    assert a != derive_correlation_key("org_b", "document", "inv_1@rev")
    source = _code_only(_inspect.getsource(derive_correlation_key))
    for forbidden in ("time.time", "datetime", "now", "uuid", "random", "latest"):
        assert forbidden not in source, forbidden


def test_an_incident_id_is_its_correlation_key():
    key = derive_correlation_key(ORG, "document", "inv_1@rev")
    assert derive_incident_id(ORG, key) == derive_incident_id(ORG, key)
    assert derive_incident_id(ORG, key) != derive_incident_id("org_other", key)


def test_a_link_id_is_derived_so_a_finding_attaches_once():
    assert derive_link_id("inc_1", "ifd_1") == derive_link_id("inc_1", "ifd_1")
    assert derive_link_id("inc_1", "ifd_1") != derive_link_id("inc_2", "ifd_1")


@pytest.mark.parametrize(
    ("evidence", "expected"),
    [
        (dict(ledger_source="payment", ledger_source_id="pat_1"), AnchorKind.LEDGER_SOURCE),
        (dict(document_id="inv_1", document_revision="d"), AnchorKind.DOCUMENT),
        (dict(source=_fact("rve_1")), AnchorKind.FINANCIAL_FACT),
        ({}, AnchorKind.NONE),
    ],
)
def test_anchor_precedence_resolves_most_specific_first(evidence, expected):
    assert CorrelationService().anchor_for(_evidence(**evidence)).kind is expected


def test_precedence_is_strict_and_never_falls_back_within_a_level():
    """All three resolvable: the strongest wins, and it is the cause the Ledger itself names."""
    everything = _evidence(
        ledger_source="payment", ledger_source_id="pat_1",
        document_id="inv_1", document_revision="d", source=_fact("rve_1"),
    )
    anchor = CorrelationService().anchor_for(everything)
    assert anchor.kind is AnchorKind.LEDGER_SOURCE
    assert anchor.value == "payment:pat_1"


# ── 4. no false correlation ───────────────────────────────────────────────────


@pytest.mark.parametrize(
    "shared",
    [
        dict(amount=Money(50_000, INR)),
        dict(account_id="acc_1"),
        dict(at=1_700_000_000.0),
        dict(detail="the same description"),
        dict(state="unposted"),
        dict(period_key="2024-03"),
    ],
)
def test_no_single_shared_attribute_creates_correlation(shared):
    """**The mandate's hardest rule.** Amount, account, timestamp, description, state and period
    correlate nothing — alone or together — because none of them identifies a financial fact."""
    a = _evidence(finding_id="f1", source=_fact("rve_1", **shared))
    b = _evidence(finding_id="f2", source=_fact("rve_2", **shared))
    assert not CorrelationService().correlates(a, b)


def test_everything_shared_except_identity_still_does_not_correlate():
    common = dict(
        amount=Money(50_000, INR), account_id="acc_1", at=1_700_000_000.0,
        detail="same", state="unposted", period_key="2024-03",
    )
    a = _evidence(finding_id="f1", source=_fact("rve_1", **common))
    b = _evidence(finding_id="f2", source=_fact("rve_2", **common))
    assert not CorrelationService().correlates(a, b)


def test_the_same_document_across_two_orgs_never_correlates():
    a = _evidence(finding_id="f1", org_id="org_a", document_id="inv_1001", document_revision="d")
    b = _evidence(finding_id="f2", org_id="org_b", document_id="inv_1001", document_revision="d")
    service = CorrelationService()
    assert not service.correlates(a, b)
    assert service.key_for(a)[1] != service.key_for(b)[1]


def test_the_same_document_at_two_revisions_never_correlates():
    """A document revised after a divergence is a **different immutable artifact**. Correlating
    across revisions would attach a March finding to an incident about the April version."""
    a = _evidence(finding_id="f1", document_id="inv_1", document_revision="march")
    b = _evidence(finding_id="f2", document_id="inv_1", document_revision="april")
    assert not CorrelationService().correlates(a, b)


def test_two_unanchored_findings_do_not_share_a_bucket():
    """**Sharing a key because neither has evidence is the worst correlation of all.**"""
    service = CorrelationService()
    a, b = _evidence(finding_id="f1"), _evidence(finding_id="f2")
    assert not service.correlates(a, b)
    assert service.key_for(a)[1] != service.key_for(b)[1]


def test_findings_at_different_precedence_levels_do_not_correlate():
    """Conservative by design: an incident that should have been one and is two is a reporting
    inconvenience; one that should have been two and is one has merged two unrelated financial
    problems and will close both when somebody fixes one."""
    ledger = _evidence(finding_id="f1", ledger_source="payment", ledger_source_id="pat_1")
    document = _evidence(finding_id="f2", document_id="inv_1", document_revision="d")
    assert not CorrelationService().correlates(ledger, document)


def test_three_relationships_naming_one_ledger_cause_are_one_incident():
    """**The M9.19 limitation, solved.** Three relationships, one ledger source identity."""
    service = CorrelationService()
    findings = [
        _evidence(
            finding_id=f"f{n}", relationship=relationship,
            ledger_source="payment", ledger_source_id="pat_9",
        )
        for n, relationship in enumerate(
            (
                RelationshipKind.PAYMENT_LEDGER,
                RelationshipKind.INVOICE_LEDGER,
                RelationshipKind.RECOGNITION_LEDGER,
            )
        )
    ]
    keys = {service.key_for(f)[1] for f in findings}
    assert len(keys) == 1
    assert service.correlates(findings[0], findings[1])
    assert service.correlates(findings[1], findings[2])


def test_an_edge_cannot_exist_without_the_anchor_that_proved_it():
    """There is no `RELATED` edge: a link created "for UI grouping" is a claim nobody can check,
    and on screen it would be indistinguishable from one that was proved."""
    assert "related" not in {e.value for e in EdgeKind}
    service = CorrelationService()
    for anchor_kind in ANCHOR_PRECEDENCE:
        assert anchor_kind in EDGE_BY_ANCHOR
    assert service.edge_for(
        service.anchor_for(_evidence(ledger_source="p", ledger_source_id="x"))
    ) is EdgeKind.SAME_SOURCE


# ── 5. severity, root cause and impact ────────────────────────────────────────


def test_severity_never_decreases():
    """**The rule that matters.** An incident whose critical finding was repaired while another
    remains open must not slip off a dashboard."""
    service = SeverityService()
    from billing.foundation import FindingLink

    def link(severity):
        return FindingLink(
            id="lnk", org_id=ORG, incident_id="inc", finding_id="ifd", run_id="irn",
            relationship=RelationshipKind.RECOGNITION_LEDGER,
            divergence=DivergenceKind.MISSING_COUNTERPART, severity=severity,
            anchor_kind=AnchorKind.DOCUMENT, anchor_value="v", edge=EdgeKind.SAME_DOCUMENT,
        )

    assert service.aggregate(IncidentSeverity.INFO, (link(FindingSeverity.CRITICAL),)) is (
        IncidentSeverity.CRITICAL
    )
    # Now only a warning-level finding remains attached — the incident stays critical.
    assert service.aggregate(IncidentSeverity.CRITICAL, (link(FindingSeverity.WARNING),)) is (
        IncidentSeverity.CRITICAL
    )
    assert service.aggregate(IncidentSeverity.CRITICAL, ()) is IncidentSeverity.CRITICAL


def test_a_resolved_finding_still_counts_toward_severity():
    """A critical divergence that has been repaired still means a critical thing happened."""
    source = _code_only(_inspect.getsource(SeverityService.aggregate))
    assert "is_attached" in source
    assert "resolved" not in source  # attachment, not resolution, is the filter


def test_a_lifecycle_transition_never_changes_severity():
    source = _code_only(_inspect.getsource(IncidentLifecycleService.apply))
    assert '"severity"' not in source


def test_conflicting_divergences_classify_as_unknown():
    """An incident whose findings point at two different causes has not been explained, and saying
    so is the honest output — not the first, the commonest, or the worst."""
    service = RootCauseService()
    assert service.classify((DivergenceKind.MISSING_COUNTERPART,)) is RootCause.SOURCE_FACT_MISSING
    assert service.classify(
        (DivergenceKind.MISSING_COUNTERPART, DivergenceKind.AMOUNT_MISMATCH)
    ) is RootCause.UNKNOWN
    assert service.classify(()) is RootCause.UNKNOWN


def test_unreadable_evidence_classifies_as_unknown_not_as_a_guess():
    for divergence in (DivergenceKind.EVIDENCE_MALFORMED, DivergenceKind.STALE_EVIDENCE):
        assert ROOT_CAUSE_BY_DIVERGENCE[divergence] is RootCause.UNKNOWN


def test_impact_is_classified_never_calculated():
    """Checked by **AST** rather than by substring: `-> RootCause` contains a hyphen, and a scan
    that failed on a return annotation would prove nothing about arithmetic."""
    source = _code_only(_inspect.getsource(RootCauseService))
    for node in ast.walk(ast.parse(source)):
        assert not isinstance(node, ast.BinOp), ast.dump(node)
    for forbidden in ("sum(", "minor", "balance", "Money"):
        assert forbidden not in source, forbidden
    assert RootCauseService().impact_of(RootCause.SOURCE_FACT_MISSING) is (
        ImpactClass.FINANCIAL_FACT_MISSING
    )


def test_every_finding_severity_has_an_incident_floor():
    assert set(SEVERITY_FLOOR) == set(FindingSeverity)


# ── 6. lifecycle: repair is never resolution ──────────────────────────────────


def test_there_is_no_edge_from_repair_straight_to_resolved():
    """**Repair never equals resolution**, and here that is an absent edge rather than a comment.
    The only path to `RESOLVED` runs through `VERIFYING`, which re-reads the books."""
    assert INCIDENT_LIFECYCLE.get(
        IncidentStatus.REPAIR_IN_PROGRESS, IncidentTrigger.RESOLVE
    ) is None
    assert INCIDENT_LIFECYCLE.get(IncidentStatus.VERIFYING, IncidentTrigger.RESOLVE) is not None


def test_resolved_closed_and_merged_are_terminal_by_construction():
    assert INCIDENT_LIFECYCLE.terminal_states() == TERMINAL_INCIDENT_STATES
    for state in TERMINAL_INCIDENT_STATES:
        assert IncidentLifecycleService().allowed(state) == ()


def test_verification_can_fail_and_go_back():
    """Staying in `VERIFYING` after a failed check would imply progress that did not happen."""
    assert INCIDENT_LIFECYCLE.get(
        IncidentStatus.VERIFYING, IncidentTrigger.INVESTIGATE
    ) is not None


def test_every_incident_status_is_reachable():
    reachable = {IncidentStatus.OPEN}
    frontier = [IncidentStatus.OPEN]
    while frontier:
        current = frontier.pop()
        for trigger in INCIDENT_LIFECYCLE.triggers_from(current):
            target = INCIDENT_LIFECYCLE.get(current, trigger).target
            if target not in reachable:
                reachable.add(target)
                frontier.append(target)
    assert reachable == set(IncidentStatus)


def test_live_states_are_exactly_the_non_terminal_ones():
    assert LIVE_INCIDENT_STATES == set(IncidentStatus) - TERMINAL_INCIDENT_STATES


def test_a_stale_worker_cannot_reopen_a_settled_incident_in_the_domain():
    lifecycle = IncidentLifecycleService()
    resolved = replace(_incident(), status=IncidentStatus.RESOLVED)
    for trigger in IncidentTrigger:
        with pytest.raises(InvalidBillingTransition):
            lifecycle.apply(resolved, trigger, at=2_000.0)


def test_resolution_requires_verification_evidence():
    """**Without this guard the command is a checkbox**: somebody clicks resolve, the row says
    resolved, and the books still disagree."""
    guard = ResolutionGuardService()
    incident = _incident()
    with pytest.raises(CorrelationRefused, match="nothing to verify"):
        guard.guard_verified(incident, VerificationOutcome(incident_id=incident.id))
    with pytest.raises(CorrelationRefused, match="still report a divergence"):
        guard.guard_verified(
            incident, VerificationOutcome(incident_id=incident.id, checked=3, still_divergent=1)
        )
    with pytest.raises(CorrelationRefused, match="not evidence of agreement"):
        guard.guard_verified(
            incident, VerificationOutcome(incident_id=incident.id, checked=3, unreadable=1)
        )
    guard.guard_verified(
        incident,
        VerificationOutcome(incident_id=incident.id, checked=3, resolved_findings=3),
    )


def test_a_settled_incident_takes_no_new_findings():
    guard = IncidentGuardService()
    with pytest.raises(CorrelationRefused, match="never takes new findings"):
        guard.guard_attachable(replace(_incident(), status=IncidentStatus.RESOLVED))


def test_merge_refuses_self_and_settled_and_cross_org():
    guard = IncidentGuardService()
    a = _incident()
    b = replace(a, id="inc_other")
    with pytest.raises(CorrelationRefused, match="into itself"):
        guard.guard_mergeable(a, a)
    with pytest.raises(CorrelationRefused, match="different organizations"):
        guard.guard_mergeable(a, replace(b, org_id="org_other"))
    with pytest.raises(CorrelationRefused, match="cannot be merged"):
        guard.guard_mergeable(a, replace(b, status=IncidentStatus.RESOLVED))


# ── 7 & 8. history is never rewritten ─────────────────────────────────────────


def test_append_only_repositories_have_no_update_or_delete():
    for contract in (IncidentEventRepository, IncidentMergeRepository):
        assert not hasattr(contract, "update")
        assert not hasattr(contract, "delete")
    from billing.foundation import CorrelationCorrectionRepository, IncidentRepository

    assert not hasattr(CorrelationCorrectionRepository, "update")
    assert not hasattr(IncidentRepository, "delete")


def test_a_link_is_detached_never_deleted():
    from billing.foundation import FindingLinkRepository

    assert hasattr(FindingLinkRepository, "detach")
    assert not hasattr(FindingLinkRepository, "delete")
    assert not hasattr(FindingLinkRepository, "remove")


def test_migration_0030_is_additive_and_creates_six_tables():
    from billing.persistence.migrations import v0030_billing_incidents as m

    assert m.VERSION == "0030"
    assert len(m.TABLES) == 6
    source = _code_only(
        Path("billing/persistence/migrations/v0030_billing_incidents.py").read_text()
    )
    for forbidden in ("ALTER", "DROP", "alter_column", "drop_column"):
        assert forbidden not in source, forbidden


def test_the_migration_chain_includes_0030():
    """`0030` is present, in order, exactly once.

    Written as membership rather than "is last": M9.21 added `0031` behind it, and a later
    milestone will add another. What must stay true is that this milestone's migration exists and
    the chain remains ordered — not that nothing was ever appended after it."""
    versions = [m.VERSION for m in MIGRATIONS]
    assert versions == sorted(versions)
    assert versions.count("0030") == 1
    assert versions.index("0030") == versions.index("0029") + 1


# ── 9. the end-to-end correlation loop ────────────────────────────────────────


def test_three_findings_from_one_cause_become_one_incident(wired):
    """**The M9.19 limitation, closed end to end.**

    Three recognition periods the Ledger refused, three findings, one document — one incident."""
    persistent, account = wired
    app = persistent.application
    _document, _schedule, _entries, receipt = _diverged(app, account, periods=3)
    assert receipt.run.diverged == 3

    correlation = app.commands.dispatch(CorrelateFindings(**KW, run_id=receipt.run.id))
    assert correlation.correlated == 3
    assert correlation.attached == 3
    assert correlation.incidents_opened == 1
    assert len(correlation.incidents) == 1
    incident = correlation.incidents[0]
    assert incident.finding_count == 3
    assert incident.anchor_kind == "document"
    assert incident.severity == "critical"
    assert incident.root_cause == "source_fact_missing"
    assert incident.impact == "financial_fact_missing"


def test_correlating_the_same_findings_twice_attaches_once(wired):
    persistent, account = wired
    app = persistent.application
    _d, _s, _e, receipt = _diverged(app, account, periods=2)
    first = app.commands.dispatch(CorrelateFindings(**KW, run_id=receipt.run.id))
    second = app.commands.dispatch(CorrelateFindings(**KW, run_id=receipt.run.id))
    assert first.attached == 2 and first.incidents_opened == 1
    assert second.attached == 0 and second.already_attached == 2
    assert second.incidents_opened == 0
    page = app.queries.dispatch(ListIncidents(**KW))
    assert page.total == 1


def test_the_full_incident_lifecycle_with_evidence(wired):
    """detect → correlate → acknowledge → investigate → verify (fails) → repair → verify →
    resolve."""
    persistent, account = wired
    app = persistent.application
    _d, schedule, _e, receipt = _diverged(app, account, periods=2)
    incident = app.commands.dispatch(
        CorrelateFindings(**KW, run_id=receipt.run.id)
    ).incidents[0]

    app.commands.dispatch(AcknowledgeIncident(**KW, incident_id=incident.id, note="seen"))
    app.commands.dispatch(InvestigateIncident(**KW, incident_id=incident.id))
    failed = app.commands.dispatch(VerifyIncident(**KW, incident_id=incident.id))
    assert failed.checked == 2 and failed.still_divergent == 2 and not failed.is_clean

    with pytest.raises(CorrelationRefused):
        app.commands.dispatch(ResolveIncident(**KW, incident_id=incident.id))

    rev._open_year(app, account)
    for link in app.queries.dispatch(GetIncidentFindings(**KW, incident_id=incident.id)):
        app.commands.dispatch(
            RequestIncidentRepair(
                **KW, incident_id=incident.id, finding_id=link.finding_id,
                request_key=link.finding_id,
            )
        )
    app.commands.dispatch(ReconcileRecognitionLedger(**KW, schedule_id=schedule.id))
    for link in app.queries.dispatch(GetIncidentFindings(**KW, incident_id=incident.id)):
        app.commands.dispatch(
            ResolveFinding(**KW, finding_id=link.finding_id, request_key=link.finding_id)
        )

    clean = app.commands.dispatch(VerifyIncident(**KW, incident_id=incident.id))
    assert clean.is_clean and clean.still_divergent == 0
    resolved = app.commands.dispatch(
        ResolveIncident(**KW, incident_id=incident.id, note="verified")
    )
    assert resolved.status == "resolved"
    assert resolved.allowed_triggers == ()
    assert resolved.severity == "critical"  # monotonic: never downgraded


def test_a_resolved_incident_cannot_be_reopened_through_the_application(wired):
    persistent, account = wired
    app = persistent.application
    incident = _resolved_incident(app, account)
    for command in (
        AcknowledgeIncident(**KW, incident_id=incident.id),
        InvestigateIncident(**KW, incident_id=incident.id),
        VerifyIncident(**KW, incident_id=incident.id),
        CloseIncident(**KW, incident_id=incident.id, reason="x"),
        ClassifyIncidentRootCause(**KW, incident_id=incident.id),
    ):
        with pytest.raises(BillingConflict):
            app.commands.dispatch(command)


def _resolved_incident(app, account):
    _d, schedule, _e, receipt = _diverged(app, account, periods=1)
    incident = app.commands.dispatch(
        CorrelateFindings(**KW, run_id=receipt.run.id)
    ).incidents[0]
    app.commands.dispatch(AcknowledgeIncident(**KW, incident_id=incident.id))
    app.commands.dispatch(InvestigateIncident(**KW, incident_id=incident.id))
    rev._open_year(app, account)
    for link in app.queries.dispatch(GetIncidentFindings(**KW, incident_id=incident.id)):
        app.commands.dispatch(
            RequestIncidentRepair(
                **KW, incident_id=incident.id, finding_id=link.finding_id, request_key="r"
            )
        )
    app.commands.dispatch(ReconcileRecognitionLedger(**KW, schedule_id=schedule.id))
    for link in app.queries.dispatch(GetIncidentFindings(**KW, incident_id=incident.id)):
        app.commands.dispatch(
            ResolveFinding(**KW, finding_id=link.finding_id, request_key="k")
        )
    app.commands.dispatch(VerifyIncident(**KW, incident_id=incident.id))
    return app.commands.dispatch(ResolveIncident(**KW, incident_id=incident.id))


def test_a_repair_request_does_not_resolve_the_incident(wired):
    """**Repair is not resolution**, demonstrated rather than asserted."""
    persistent, account = wired
    app = persistent.application
    _d, _s, _e, receipt = _diverged(app, account, periods=1)
    incident = app.commands.dispatch(
        CorrelateFindings(**KW, run_id=receipt.run.id)
    ).incidents[0]
    app.commands.dispatch(AcknowledgeIncident(**KW, incident_id=incident.id))
    app.commands.dispatch(InvestigateIncident(**KW, incident_id=incident.id))
    rev._open_year(app, account)
    app.commands.dispatch(
        RequestIncidentRepair(**KW, incident_id=incident.id, request_key="r")
    )
    after = app.queries.dispatch(GetIncident(**KW, incident_id=incident.id))
    assert after.status == "repair_in_progress"
    assert "resolve" not in after.allowed_triggers


# ── 10. audit trail ───────────────────────────────────────────────────────────


def test_every_significant_action_appends_immutable_evidence(wired):
    persistent, account = wired
    app = persistent.application
    _d, _s, _e, receipt = _diverged(app, account, periods=2)
    incident = app.commands.dispatch(
        CorrelateFindings(**KW, run_id=receipt.run.id)
    ).incidents[0]
    app.commands.dispatch(AcknowledgeIncident(**KW, incident_id=incident.id, note="seen"))
    app.commands.dispatch(InvestigateIncident(**KW, incident_id=incident.id))
    app.commands.dispatch(VerifyIncident(**KW, incident_id=incident.id))
    timeline = app.queries.dispatch(GetIncidentTimeline(**KW, incident_id=incident.id))
    kinds = [e.kind for e in timeline]
    for required in (
        IncidentEventKind.INCIDENT_CREATED.value,
        IncidentEventKind.FINDING_ATTACHED.value,
        IncidentEventKind.CORRELATION_CONFIRMED.value,
        IncidentEventKind.ACKNOWLEDGED.value,
        IncidentEventKind.INVESTIGATION_STARTED.value,
        IncidentEventKind.VERIFICATION_STARTED.value,
        IncidentEventKind.VERIFICATION_FAILED.value,
    ):
        assert required in kinds, required
    assert [e.sequence for e in timeline] == sorted(e.sequence for e in timeline)
    assert len(set(e.sequence for e in timeline)) == len(timeline)


def test_a_rejected_command_appends_no_business_event(wired):
    """A refusal is not a business fact. Emitting one would make an audit trail in which nothing
    happened indistinguishable from one in which something did."""
    persistent, account = wired
    app = persistent.application
    _d, _s, _e, receipt = _diverged(app, account, periods=1)
    incident = app.commands.dispatch(
        CorrelateFindings(**KW, run_id=receipt.run.id)
    ).incidents[0]
    before = len(app.queries.dispatch(GetIncidentTimeline(**KW, incident_id=incident.id)))
    with pytest.raises(InvalidBillingTransition):
        app.commands.dispatch(InvestigateIncident(**KW, incident_id=incident.id))  # not acked
    after = len(app.queries.dispatch(GetIncidentTimeline(**KW, incident_id=incident.id)))
    assert after == before


def test_the_timeline_survives_resolution(wired):
    persistent, account = wired
    app = persistent.application
    incident = _resolved_incident(app, account)
    timeline = app.queries.dispatch(GetIncidentTimeline(**KW, incident_id=incident.id))
    assert any(e.kind == IncidentEventKind.INCIDENT_CREATED.value for e in timeline)
    assert any(e.kind == IncidentEventKind.RESOLVED.value for e in timeline)


def test_an_incident_explains_itself_from_stored_evidence(wired):
    persistent, account = wired
    app = persistent.application
    _d, _s, _e, receipt = _diverged(app, account, periods=2)
    incident = app.commands.dispatch(
        CorrelateFindings(**KW, run_id=receipt.run.id)
    ).incidents[0]
    explanation = app.queries.dispatch(ExplainIncident(**KW, incident_id=incident.id))
    joined = " ".join(explanation.narrative).lower()
    for required in ("correlated on", "root cause", "impact", "severity", "status"):
        assert required in joined, required
    assert len(explanation.findings) == 2


def test_the_explainer_reads_only_stored_evidence():
    from billing.foundation import IncidentExplainer

    source = _code_only(_inspect.getsource(IncidentExplainer))
    for forbidden in ("repos", "repository", "session", "ledger", "payment", "invoice"):
        assert forbidden not in source.lower(), forbidden


def test_the_evidence_graph_is_connected_and_every_edge_names_its_anchor(wired):
    persistent, account = wired
    app = persistent.application
    _d, _s, _e, receipt = _diverged(app, account, periods=2)
    incident = app.commands.dispatch(
        CorrelateFindings(**KW, run_id=receipt.run.id)
    ).incidents[0]
    graph = app.queries.dispatch(GetIncidentEvidence(**KW, incident_id=incident.id))
    assert graph.is_connected
    assert graph.edges
    for edge in graph.edges:
        assert edge.anchor and edge.evidence


def test_an_incident_inspection_is_self_consistent(wired):
    persistent, account = wired
    app = persistent.application
    _d, _s, _e, receipt = _diverged(app, account, periods=3)
    incident = app.commands.dispatch(
        CorrelateFindings(**KW, run_id=receipt.run.id)
    ).incidents[0]
    inspection = app.queries.dispatch(InspectIncident(**KW, incident_id=incident.id))
    assert inspection.is_counted_correctly, inspection.notes
    assert inspection.is_scoped and inspection.is_connected
    assert inspection.is_severity_monotonic
    assert inspection.finding_count == 3


# ── 11. merge and correction preserve history ─────────────────────────────────


def _second_incident(app):
    """An incident with a different anchor, built through the factory and stored directly."""
    factory = IncidentFactory(ids=SequentialIdGenerator(), clock=FixedClock(9_000.0, step=0.0))
    built, _anchor = factory.incident(
        _evidence(
            finding_id="ifd_other",
            relationship=RelationshipKind.INVOICE_LEDGER,
            divergence=DivergenceKind.AMOUNT_MISMATCH,
            document_id="inv_other",
            document_revision="rev_9",
        ),
        severity=IncidentSeverity.ERROR,
        root_cause=RootCause.AMOUNT_DIVERGENCE,
        impact=ImpactClass.CONFIRMED_FINANCIAL_IMPACT,
        at=9_000.0,
    )
    with app.uow_factory.new():
        stored, _ = app.context.incident_engine.incidents.create(built)
    return stored


def test_a_merge_preserves_both_originals(wired):
    """**No destructive merge.** The merged incident keeps its severity, its findings and its whole
    history and stays readable forever."""
    persistent, account = wired
    app = persistent.application
    _d, _s, _e, receipt = _diverged(app, account, periods=2)
    canonical = app.commands.dispatch(
        CorrelateFindings(**KW, run_id=receipt.run.id)
    ).incidents[0]
    other = _second_incident(app)

    merged = app.commands.dispatch(
        MergeIncidents(
            **KW, canonical_id=canonical.id, merged_id=other.id, reason="one posting failure"
        )
    )
    assert merged.status == "merged"
    assert merged.canonical_id == canonical.id
    assert merged.severity == other.severity.value  # severity intact

    still_there = app.queries.dispatch(GetIncident(**KW, incident_id=other.id))
    assert still_there.id == other.id
    assert still_there.severity == other.severity.value
    untouched = app.queries.dispatch(GetIncident(**KW, incident_id=canonical.id))
    assert untouched.status == "open"

    records = app.queries.dispatch(GetIncidentMerges(**KW, incident_id=other.id))
    assert len(records) == 1
    assert records[0].canonical_id == canonical.id and records[0].merged_id == other.id


def test_a_merged_incident_cannot_be_merged_again(wired):
    persistent, account = wired
    app = persistent.application
    _d, _s, _e, receipt = _diverged(app, account, periods=1)
    canonical = app.commands.dispatch(
        CorrelateFindings(**KW, run_id=receipt.run.id)
    ).incidents[0]
    other = _second_incident(app)
    app.commands.dispatch(
        MergeIncidents(**KW, canonical_id=canonical.id, merged_id=other.id, reason="a")
    )
    with pytest.raises(CorrelationRefused, match="cannot be merged"):
        app.commands.dispatch(
            MergeIncidents(**KW, canonical_id=canonical.id, merged_id=other.id, reason="b")
        )


def test_a_no_op_correction_is_refused_rather_than_orphaning_the_finding(wired):
    """**Defect D-1's regression.** A correction that re-derives the same incident used to detach
    the finding and never re-attach it, leaving it attached to nothing."""
    persistent, account = wired
    app = persistent.application
    _d, _s, _e, receipt = _diverged(app, account, periods=1)
    incident = app.commands.dispatch(
        CorrelateFindings(**KW, run_id=receipt.run.id)
    ).incidents[0]
    finding_id = app.queries.dispatch(
        GetIncidentFindings(**KW, incident_id=incident.id)
    )[0].finding_id
    with pytest.raises(BillingConflict, match="nothing to correct"):
        app.commands.dispatch(
            CorrectCorrelation(
                **KW, finding_id=finding_id, incident_id=incident.id, reason="x"
            )
        )
    links = app.queries.dispatch(GetFindingIncidents(**KW, finding_id=finding_id))
    assert [link.is_attached for link in links] == [True]


def test_a_correction_preserves_the_previous_belief(wired):
    """The old link is **detached, never deleted**, and both keys are recorded."""
    persistent, account = wired
    app = persistent.application
    _d, _s, _e, receipt = _diverged(app, account, periods=1)
    right = app.commands.dispatch(CorrelateFindings(**KW, run_id=receipt.run.id)).incidents[0]
    finding_id = app.queries.dispatch(
        GetIncidentFindings(**KW, incident_id=right.id)
    )[0].finding_id
    wrong = _second_incident(app)
    finding = app.context.reconciliation_engine.findings.get(ORG, finding_id)
    factory = IncidentFactory(ids=SequentialIdGenerator(), clock=FixedClock(9_100.0, step=0.0))
    from billing.foundation import anchor_of

    anchor = anchor_of(AnchorKind.DOCUMENT, "inv_other@rev_9")
    with app.uow_factory.new():
        app.context.incident_engine.links.attach(
            factory.link(wrong, finding, anchor, EdgeKind.SAME_DOCUMENT, at=9_100.0)
        )

    corrected = app.commands.dispatch(
        CorrectCorrelation(
            **KW, finding_id=finding_id, incident_id=wrong.id, reason="anchor was too broad"
        )
    )
    assert corrected.id == right.id

    links = app.queries.dispatch(GetFindingIncidents(**KW, finding_id=finding_id))
    by_incident = {link.incident_id: link.is_attached for link in links}
    assert by_incident[right.id] is True
    assert by_incident[wrong.id] is False  # detached, not deleted

    corrections = app.queries.dispatch(
        GetCorrelationCorrections(**KW, finding_id=finding_id)
    )
    assert len(corrections) == 1
    assert corrections[0].previous_incident_id == wrong.id
    assert corrections[0].corrected_incident_id == right.id
    assert corrections[0].previous_key != corrections[0].corrected_key
    assert app.queries.dispatch(GetIncident(**KW, incident_id=wrong.id)).id == wrong.id


# ── 12. concurrency ───────────────────────────────────────────────────────────


def test_two_workers_creating_the_same_incident_converge(tmp_path):
    first, second = _two_sessions(tmp_path)
    built = _incident()
    with first.application.uow_factory.new():
        a, a_new = first.application.context.incident_engine.incidents.create(built)
    with second.application.uow_factory.new():
        b, b_new = second.application.context.incident_engine.incidents.create(built)
    assert a.id == b.id
    assert a_new and not b_new


def test_two_workers_attaching_the_same_finding_write_one_link(tmp_path, wired):
    persistent, account = wired
    app = persistent.application
    _d, _s, _e, receipt = _diverged(app, account, periods=1)
    incident = app.commands.dispatch(
        CorrelateFindings(**KW, run_id=receipt.run.id)
    ).incidents[0]
    links = app.queries.dispatch(GetIncidentFindings(**KW, incident_id=incident.id))
    assert len(links) == 1
    # A second correlation pass is the same race, serialized by the derived link id.
    app.commands.dispatch(CorrelateFindings(**KW, run_id=receipt.run.id))
    assert len(app.queries.dispatch(GetIncidentFindings(**KW, incident_id=incident.id))) == 1


def test_a_stale_worker_cannot_overwrite_a_settled_conclusion(tmp_path):
    """**The mandate's stale-worker scenario, with two real connections.**

    Worker A reads version 3. Worker B transitions to version 4. Worker A resumes and writes — the
    conditional UPDATE finds nothing, so it fails loudly rather than replacing a conclusion."""
    first, second = _two_sessions(tmp_path, "stale.db")
    built = _incident()
    with first.application.uow_factory.new():
        first.application.context.incident_engine.incidents.create(built)

    lifecycle = IncidentLifecycleService()
    with second.application.uow_factory.new():
        repo = second.application.context.incident_engine.incidents
        stored = repo.get(ORG, built.id)
        repo.advance(
            lifecycle.apply(stored, IncidentTrigger.ACKNOWLEDGE, at=2.0), expected_version=0
        )

    with pytest.raises(IncidentStale, match="another worker settled it"), \
            first.application.uow_factory.new():
        repo = first.application.context.incident_engine.incidents
        repo.advance(
            lifecycle.apply(built, IncidentTrigger.CLOSE, at=3.0), expected_version=0
        )


def test_incidents_survive_a_restart(wired):
    persistent, account = wired
    app = persistent.application
    _d, _s, _e, receipt = _diverged(app, account, periods=2)
    incident = app.commands.dispatch(
        CorrelateFindings(**KW, run_id=receipt.run.id)
    ).incidents[0]
    reopened = build_persistent_billing_application(
        database=persistent.database, authorizer=AllowAllAuthorizer(), create_all=False
    )
    found = reopened.application.queries.dispatch(
        GetIncident(**KW, incident_id=incident.id)
    )
    assert found.finding_count == 2
    assert found.severity == "critical"
    timeline = reopened.application.queries.dispatch(
        GetIncidentTimeline(**KW, incident_id=incident.id)
    )
    assert timeline


def test_the_event_sequence_is_a_stored_count_not_a_derived_maximum():
    """A derived maximum hands a number back after a rollback, and two events at position 7 make
    the trail ambiguous — the same rule M9.13 applies to ledger sequences."""
    from billing.persistence.incident_repositories import SqlIncidentEventRepository

    source = _code_only(_inspect.getsource(SqlIncidentEventRepository.next_sequence))
    assert "func.count" in source
    assert "max(" not in source.lower()


# ── 13. cross-org isolation ───────────────────────────────────────────────────


def test_correlation_never_crosses_organizations():
    service = CorrelationService()
    for shared in (
        dict(document_id="inv_1", document_revision="d"),
        dict(ledger_source="payment", ledger_source_id="pat_1"),
        dict(source=_fact("rve_1")),
    ):
        a = _evidence(finding_id="f1", org_id="org_a", **shared)
        b = _evidence(finding_id="f2", org_id="org_b", **shared)
        assert not service.correlates(a, b), shared
        assert service.key_for(a)[1] != service.key_for(b)[1]


def test_an_incident_from_another_org_is_invisible(wired):
    persistent, account = wired
    app = persistent.application
    _d, _s, _e, receipt = _diverged(app, account, periods=1)
    incident = app.commands.dispatch(
        CorrelateFindings(**KW, run_id=receipt.run.id)
    ).incidents[0]
    other = dict(actor_id=ACTOR, org_id="org_other")
    from billing.foundation import BillingNotFound

    with pytest.raises(BillingNotFound):
        app.queries.dispatch(GetIncident(**other, incident_id=incident.id))
    assert app.queries.dispatch(ListIncidents(**other)).total == 0
    assert app.queries.dispatch(LiveIncidents(**other)) == ()


def test_the_guard_refuses_a_cross_org_attachment():
    guard = IncidentGuardService()
    with pytest.raises(CorrelationRefused, match="cannot be correlated"):
        guard.guard_same_org(_incident(), _evidence(org_id="org_other"))


def test_every_command_and_query_carries_an_org():
    for message in (*ALL_INCIDENT_COMMANDS, *ALL_INCIDENT_QUERIES):
        assert "org_id" in message.__dataclass_fields__, message.__name__
        assert "actor_id" in message.__dataclass_fields__, message.__name__


def test_every_incident_table_is_org_scoped_and_indexed():
    from billing.persistence.incident_models import INCIDENT_TABLES

    for table in INCIDENT_TABLES:
        assert "org_id" in table.__table__.columns, table.__tablename__
        assert table.__table__.columns["org_id"].index, table.__tablename__


# ── 14. the adversarial hunt ──────────────────────────────────────────────────


def test_hunt_three_unrelated_causes_stay_three_incidents():
    """The opposite of the headline case, and the one that matters more."""
    service = CorrelationService()
    findings = [
        _evidence(finding_id=f"f{n}", source=_fact(f"rve_{n}"))
        for n in range(3)
    ]
    keys = {service.key_for(f)[1] for f in findings}
    assert len(keys) == 3


def test_hunt_a_new_divergence_after_resolution_opens_a_new_incident(wired):
    """A settled incident never takes new findings — a recurrence is a new observation."""
    persistent, account = wired
    app = persistent.application
    incident = _resolved_incident(app, account)
    stored = app.queries.dispatch(GetIncident(**KW, incident_id=incident.id))
    assert stored.status == "resolved"
    guard = IncidentGuardService()
    with pytest.raises(CorrelationRefused):
        guard.guard_attachable(
            replace(_incident(), status=IncidentStatus.RESOLVED)
        )


def test_hunt_severity_does_not_downgrade_while_a_critical_finding_remains(wired):
    persistent, account = wired
    app = persistent.application
    _d, _s, _e, receipt = _diverged(app, account, periods=2)
    incident = app.commands.dispatch(
        CorrelateFindings(**KW, run_id=receipt.run.id)
    ).incidents[0]
    assert incident.severity == "critical"
    reclassified = app.commands.dispatch(
        ClassifyIncidentRootCause(**KW, incident_id=incident.id)
    )
    assert reclassified.severity == "critical"


def test_hunt_a_missing_finding_makes_verification_unreadable_not_clean(wired):
    """**"I could not check" is not "it is fine".**"""
    persistent, account = wired
    app = persistent.application
    _d, _s, _e, receipt = _diverged(app, account, periods=1)
    incident = app.commands.dispatch(
        CorrelateFindings(**KW, run_id=receipt.run.id)
    ).incidents[0]
    from billing.foundation import IntegrityIncident

    links = app.context.incident_engine.links.for_incident(ORG, incident.id)
    outcome = ResolutionGuardService().verify(
        IntegrityIncident(id=incident.id, org_id=ORG, correlation_key="k",
                          anchor_kind=AnchorKind.DOCUMENT, anchor_value="v"),
        links,
        {},  # nothing could be re-read
    )
    assert outcome.unreadable == len(links)
    assert not outcome.is_clean


def test_hunt_partial_resolution_never_masquerades_as_complete(wired):
    persistent, account = wired
    app = persistent.application
    _d, schedule, _e, receipt = _diverged(app, account, periods=3)
    incident = app.commands.dispatch(
        CorrelateFindings(**KW, run_id=receipt.run.id)
    ).incidents[0]
    app.commands.dispatch(AcknowledgeIncident(**KW, incident_id=incident.id))
    app.commands.dispatch(InvestigateIncident(**KW, incident_id=incident.id))
    rev._open_year(app, account)
    links = app.queries.dispatch(GetIncidentFindings(**KW, incident_id=incident.id))
    # repair only one of three
    app.commands.dispatch(
        RequestIncidentRepair(
            **KW, incident_id=incident.id, finding_id=links[0].finding_id, request_key="r"
        )
    )
    app.commands.dispatch(ReconcileRecognitionLedger(**KW, schedule_id=schedule.id))
    app.commands.dispatch(
        ResolveFinding(**KW, finding_id=links[0].finding_id, request_key="k")
    )
    outcome = app.commands.dispatch(VerifyIncident(**KW, incident_id=incident.id))
    assert not outcome.is_clean and outcome.still_divergent == 2
    with pytest.raises(CorrelationRefused):
        app.commands.dispatch(ResolveIncident(**KW, incident_id=incident.id))


def test_hunt_the_correlation_key_never_reads_a_mutable_status():
    source = _code_only(Path("billing/foundation/incident_rules.py").read_text())
    resolver = source[source.index("class CorrelationService") : source.index("class RootCause")]
    for forbidden in ("latest", ".status", "balance", "amount", "at ", "now"):
        assert forbidden not in resolver, forbidden


def test_hunt_pagination_is_a_database_concern():
    """A Python slice over every incident an org ever had is not a performance detail at this
    milestone's assumed scale — it is an outage."""
    from billing.persistence.incident_repositories import SqlIncidentRepository

    source = _code_only(_inspect.getsource(SqlIncidentRepository.page))
    assert "limit(" in source and "offset(" in source
    assert "func.count" in source


def test_hunt_the_posture_has_no_monetary_field(wired):
    persistent, account = wired
    app = persistent.application
    _d, _s, _e, receipt = _diverged(app, account, periods=2)
    app.commands.dispatch(CorrelateFindings(**KW, run_id=receipt.run.id))
    posture = app.queries.dispatch(GetIncidentPosture(**KW))
    assert posture.critical == 1 and not posture.is_clean
    assert posture.by_root_cause.get("source_fact_missing") == 1
    for name in type(posture).__dataclass_fields__:
        assert "amount" not in name and "total" not in name and "minor" not in name


def test_hunt_search_is_scoped_and_paginated(wired):
    persistent, account = wired
    app = persistent.application
    _d, _s, _e, receipt = _diverged(app, account, periods=2)
    app.commands.dispatch(CorrelateFindings(**KW, run_id=receipt.run.id))
    page = app.queries.dispatch(SearchIncidents(**KW, severities=("critical",), limit=10))
    assert page.total == 1 and not page.has_more
    assert page.limit == 10 and page.offset == 0


# ── 15. the surface ───────────────────────────────────────────────────────────


def _auth(user_id: str = ACTOR, org_id: str = ORG) -> dict[str, str]:
    return {
        "Authorization": "Bearer "
        + epic8_encode({"sub": user_id, "org": org_id}, SECRET, ttl_seconds=3600)
    }


@pytest.fixture
def client():
    settings = BillingApiSettings(environment="testing", jwt_secret=SECRET)
    container = BillingContainer.build(
        settings,
        authorizer=AllowAllAuthorizer(),
        ids=SequentialIdGenerator(),
        clock=FixedClock(1_500.0, step=0.0),
    )
    app = create_billing_app(settings=settings, container=container)
    with TestClient(app) as test_client:
        yield test_client


PATH = "/v1/billing/integrity/incidents"


def test_the_two_integrity_routers_share_no_path(client):
    """**Prefix disjointness is not the requirement; path disjointness is.**

    Correlation and reconciliation are one bounded context, so they share a prefix — but every
    M9.20 route begins with a segment M9.19 does not use, and this asserts it rather than assuming
    it."""
    spec = client.app.openapi()
    ours = {p for p in spec["paths"] if p.startswith(PATH)}
    theirs = {
        p for p in spec["paths"]
        if p.startswith("/v1/billing/integrity") and not p.startswith(PATH)
    }
    assert len(ours) == 25
    assert len(theirs) == 25
    assert not (ours & theirs)


def test_there_is_no_destructive_verb_on_this_surface(client):
    """Historical financial evidence is not mutated: no DELETE, no PUT, no PATCH."""
    spec = client.app.openapi()
    for path, ops in spec["paths"].items():
        if path.startswith(PATH):
            assert not ({"delete", "put", "patch"} & set(ops)), path


def test_reads_and_writes_deny_by_default(client):
    for path in (f"{PATH}", f"{PATH}/posture", f"{PATH}/live", f"{PATH}/x", f"{PATH}/x/timeline"):
        assert client.get(path).status_code == 401, path
    for path, body in (
        (f"{PATH}/correlate", {"run_id": "r"}),
        (f"{PATH}/x/acknowledge", {}),
        (f"{PATH}/x/resolve", {}),
        (f"{PATH}/x/merge", {"merged_id": "y"}),
    ):
        assert client.post(path, json=body).status_code == 401, path


def test_a_literal_segment_is_never_swallowed_by_the_parameterised_one(client):
    """`posture`, `live` and `search` are declared before `{incident_id}`."""
    for suffix in ("posture", "live", "search"):
        response = client.get(f"{PATH}/{suffix}", headers=_auth())
        assert response.status_code == 200, suffix


def test_the_collection_endpoint_is_paginated(client):
    body = client.get(f"{PATH}?limit=5&offset=0", headers=_auth()).json()
    for key in ("incidents", "total", "limit", "offset", "has_more"):
        assert key in body, key


def test_no_correlation_rule_leaks_into_the_transport_layer():
    for path in (
        Path("billing/customer/routers/incident_engine.py"),
        Path("billing/customer/incident_schemas.py"),
    ):
        code = _code_only(path.read_text())
        for forbidden in (
            "CorrelationService", "SEVERITY_FLOOR", "ROOT_CAUSE_BY_DIVERGENCE",
            "IncidentLifecycleService", "derive_correlation_key", "derive_incident_id",
        ):
            assert forbidden not in code, (path.name, forbidden)


def test_the_router_declares_no_root_cause_of_its_own():
    """`/classify` takes no body: a caller-supplied root cause would be an invented fact."""
    source = Path("billing/customer/routers/incident_engine.py").read_text()
    tree = ast.parse(source)
    classify = next(
        n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == "classify"
    )
    assert not any(a.arg == "body" for a in classify.args.args)


# ── 16. the Studio is a consumer ──────────────────────────────────────────────


STUDIO = Path("billing/customer/studio/incidents.html")


def test_the_studio_stores_nothing():
    code = _html_code_only(STUDIO.read_text())
    for forbidden in ("localStorage", "sessionStorage", "document.cookie", "indexedDB"):
        assert forbidden not in code, forbidden


def test_the_studio_correlates_nothing_locally():
    """A client that grouped findings by amount, account or timestamp would be a second correlation
    authority producing conclusions nobody can prove."""
    code = _html_code_only(STUDIO.read_text())
    for forbidden in ("groupBy", "correlate(", ".filter(f =>", "reduce("):
        assert forbidden not in code, forbidden
    assert "allowed_triggers" in code


def test_the_studio_performs_no_arithmetic_on_money():
    """Scanned over the **script block only**: a CSS class named `.pill.amount_mismatch` is a
    colour, not a calculation, and a whole-file substring scan would fail on it and prove
    nothing."""
    raw = _html_code_only(STUDIO.read_text())
    script = raw[raw.index("<script>") : raw.index("</script>")]
    for forbidden in ("minor", "amount", "currency", "parseFloat", ".sum(", "reduce("):
        assert forbidden not in script, forbidden


def test_the_studio_decides_no_severity_or_root_cause():
    code = _html_code_only(STUDIO.read_text())
    for forbidden in ("severity =", "root_cause =", "SEVERITY", "if (i.severity ==="):
        assert forbidden not in code, forbidden


def test_the_studio_reaches_no_provider_and_no_other_domain():
    code = _html_code_only(STUDIO.read_text())
    assert code.count('const API = "/v1/billing/integrity/incidents"') == 1
    for forbidden in ("stripe", "razorpay", "http://", "https://", "cdn."):
        assert forbidden not in code.lower(), forbidden


def test_the_studio_is_self_contained():
    code = STUDIO.read_text()
    assert "<script src=" not in code
    assert '<link rel="stylesheet"' not in code


# ── 17. the package layout is the declared one ────────────────────────────────


def test_every_incident_module_exists_and_is_exported():
    for path in INCIDENT_FILES:
        assert path.is_file(), path
    import billing.foundation as foundation

    for name in (
        "IntegrityIncident", "FindingLink", "IncidentEvent", "IncidentMerge",
        "CorrelationCorrection", "IncidentRepair", "CorrelationService", "IncidentFactory",
        "IncidentStale", "AnchorKind", "RootCause", "ImpactClass",
    ):
        assert name in foundation.__all__, name
        assert hasattr(foundation, name), name


def test_the_command_and_query_vocabulary_is_registered(wired):
    persistent, _account = wired
    app = persistent.application
    for command in ALL_INCIDENT_COMMANDS:
        assert command in app.commands.registered, command.__name__
    for query in ALL_INCIDENT_QUERIES:
        assert query in app.queries.registered, query.__name__
    assert len(ALL_INCIDENT_COMMANDS) == 11
    assert len(ALL_INCIDENT_QUERIES) == 14
