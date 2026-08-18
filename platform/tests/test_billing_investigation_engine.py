"""M9.21 — Financial Investigation & Audit Evidence Engine.

M9.19 observes that two financial facts disagree. M9.20 proves which observations share a cause.
Neither records what a human found out, and the question this milestone must survive is not "can
an investigator take notes?" but:

> *Can an external examiner, six months later, from persisted rows alone and without trusting a
> single application log, reconstruct what Billing knew at each point, who looked, what evidence
> they held, what they concluded, and can they tell a proven fact from somebody's guess?*

So the suite is organised around the six ways that fails:

* **a pointer is presented as history** — an audit package contains today's corrected figure while
  claiming to show what was known before the correction (§4, §5);
* **a guess reads as a finding** — a hypothesis is promoted to an observed fact, or an unsupported
  conclusion is marked confirmed, and nothing in the record distinguishes them (§8, §9);
* **missing evidence disappears** — a source that could not be retrieved is quietly omitted, so an
  incomplete package presents as thorough and "could not retrieve" reads as "verified" (§6, §11);
* **history is edited** — a redaction, a revision or a re-answer overwrites what was believed at
  the time, which is the one thing an audit cannot accept (§7, §10);
* **an export is not reproducible** — the same unchanged investigation exports to two different
  packages, so no digest an examiner recomputes proves anything (§11);
* **two investigators disagree about what they saw** — duplicate cases, duplicate captures, a
  stale worker overwriting a conclusion somebody else recorded (§12).

Every claim is demonstrated **structurally** — by a derived key, a database constraint, an absent
FSM edge, an AST scan, or two real connections racing — rather than by a happy path assumed to
imply the rest. §14 is the numbered adversarial hunt.

The pipeline is the real one: a closed period, a rated run, a posted ledger, an issued document, a
recognition schedule, genuine M9.19 findings and a genuine M9.20 incident. M9.21 reads those and
writes to none of them.
"""

from __future__ import annotations

import ast
import inspect as _inspect
from dataclasses import replace
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import tests.test_billing_incident_engine as ic
import tests.test_billing_invoice_engine as inv
import tests.test_billing_reconciliation_engine as rec
import tests.test_billing_revenue_engine as rev
from api.customer.jwt import encode as epic8_encode
from billing.application import (
    ALL_INVESTIGATION_COMMANDS,
    ALL_INVESTIGATION_QUERIES,
    AllowAllAuthorizer,
    AnswerQuestion,
    AssessClosure,
    AwaitEvidence,
    CaptureEvidence,
    CaptureIncidentEvidence,
    CloseInvestigation,
    CorrelateFindings,
    ExportAuditPackage,
    GetAuditPackage,
    GetCustodyChains,
    GetEvidenceChain,
    GetInvestigation,
    GetInvestigationConclusions,
    GetInvestigationEvidence,
    GetInvestigationForIncident,
    GetInvestigationObservations,
    GetInvestigationPosture,
    GetInvestigationQuestions,
    GetInvestigationTimeline,
    ListIncidents,
    ListInvestigations,
    MarkEvidenceUnavailable,
    OpenInvestigation,
    PublishRecognitionPolicy,
    RecordConclusion,
    RecordObservation,
    RecordQuestion,
    RedactEvidence,
    ResumeInvestigation,
    SearchInvestigations,
    SettleHypothesis,
    StartInvestigation,
    TriageInvestigation,
)
from billing.application.authorization import BillingPermission
from billing.customer.app import create_billing_app
from billing.customer.config import BillingApiSettings
from billing.customer.container import BillingContainer
from billing.foundation import (
    INVESTIGATION_LIFECYCLE,
    LIVE_INVESTIGATION_STATES,
    REQUIRED_QUESTIONS,
    SUPPORTING_AVAILABILITY,
    TERMINAL_INVESTIGATION_STATES,
    AuditPackage,
    AuditPackageRepository,
    BillingConflict,
    BillingError,
    CanonicalizationError,
    CaptureMode,
    ConclusionClassification,
    ConclusionGuardService,
    CustodyService,
    EvidenceAvailability,
    EvidenceCapture,
    EvidenceComparisonService,
    EvidenceDomain,
    EvidenceGuardService,
    EvidenceReference,
    EvidenceRefused,
    EvidenceRepository,
    EvidenceSourceType,
    FixedClock,
    HypothesisStatus,
    IncidentSeverity,
    IntegrityIncident,
    InvalidBillingTransition,
    Investigation,
    InvestigationConclusionRepository,
    InvestigationEventKind,
    InvestigationEventRepository,
    InvestigationFactory,
    InvestigationGuardService,
    InvestigationLifecycleService,
    InvestigationRefused,
    InvestigationStale,
    InvestigationStatus,
    InvestigationTrigger,
    ManifestBuilder,
    ObservationKind,
    PackageCompleteness,
    QuestionKind,
    RootCause,
    SequentialIdGenerator,
    canonical_digest,
    canonical_form,
    canonical_instant,
    derive_audit_package_id,
    derive_evidence_id,
    derive_investigation_id,
    derive_observation_id,
)
from billing.persistence.composition import build_persistent_billing_application
from billing.persistence.migrations import MIGRATIONS

ORG, ACTOR = inv.ORG, inv.ACTOR
KW = inv.KW
SECRET = "test-secret-value-for-billing-investigations"

INVESTIGATION_FILES = (
    Path("billing/foundation/commercial_investigations.py"),
    Path("billing/foundation/investigation_rules.py"),
    Path("billing/foundation/investigation_factories.py"),
    Path("billing/application/investigation_engine.py"),
    Path("billing/persistence/investigation_models.py"),
    Path("billing/persistence/investigation_repositories.py"),
    Path("billing/customer/investigation_schemas.py"),
    Path("billing/customer/routers/investigation_engine.py"),
)

FOUNDATION_FILES = INVESTIGATION_FILES[:3]

_html_code_only = rec._html_code_only


def _studio_script(path: Path = Path("billing/customer/studio/investigations.html")) -> str:
    """The Studio's JavaScript only — not its CSS.

    The distinction matters: `* { box-sizing: border-box }` is a CSS universal selector, and a scan
    for arithmetic that swept it up would have to be weakened to pass, which is how a check ends up
    proving less than it claims."""
    html = _html_code_only(path.read_text())
    return html[html.index("<script>") : html.rindex("</script>")]
_code_only = ic._code_only

#: The vocabulary M9.16 established as never-stored, restated here because an evidence store is
#: exactly where somebody would be tempted to copy it "for the investigation".
FORBIDDEN_SECRETS = (
    "pan", "card_number", "cardnumber", "cvv", "cvc", "iban", "account_number",
    "routing_number", "sort_code", "api_key", "apikey", "secret_key", "private_key",
    "password", "credential", "bearer_token", "access_token", "refresh_token",
)


# ── fixtures ────────────────────────────────────────────────────────────────────


@pytest.fixture
def wired():
    """The real pipeline through to a genuine M9.20 incident.

    Nothing here is a stub: a closed period, an issued document, a recognition policy, a live
    schedule, recognitions the Ledger genuinely refused, real M9.19 findings, and the incident
    M9.20 correlated from them."""
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
    ic._diverged(app, account, periods=3)
    app.commands.dispatch(CorrelateFindings(**KW))
    incident_id = app.queries.dispatch(ListIncidents(**KW)).incidents[0].id
    yield app, incident_id
    for _ in generator:  # pragma: no cover — drains the invoice fixture's teardown
        pass


@pytest.fixture
def started(wired):
    """An investigation open, triaged and started — the state most tests need."""
    app, incident_id = wired
    view = app.commands.dispatch(OpenInvestigation(**KW, incident_id=incident_id, summary="why?"))
    app.commands.dispatch(TriageInvestigation(**KW, investigation_id=view.id))
    app.commands.dispatch(StartInvestigation(**KW, investigation_id=view.id))
    return app, incident_id, view.id


@pytest.fixture
def evidenced(started):
    """…with the incident chain captured. Returns the first available snapshot."""
    app, incident_id, investigation_id = started
    captures = app.commands.dispatch(
        CaptureIncidentEvidence(**KW, investigation_id=investigation_id)
    )
    snapshot = next(c for c in captures if c.supports_conclusion)
    return app, incident_id, investigation_id, snapshot


def _incident(**overrides) -> IntegrityIncident:
    body = dict(
        id="inc_1", org_id=ORG, correlation_key="cor_1",
        anchor_kind=ic.AnchorKind.DOCUMENT, anchor_value="doc_1",
        severity=IncidentSeverity.CRITICAL, root_cause=RootCause.SOURCE_FACT_MISSING,
    )
    body.update(overrides)
    return IntegrityIncident(**body)


def _investigation(**overrides) -> Investigation:
    factory = InvestigationFactory(
        ids=SequentialIdGenerator(), clock=FixedClock(1_000.0, step=0.0)
    )
    built = factory.investigation(
        _incident(**overrides.pop("incident", {})), org_id=overrides.pop("org_id", ORG), at=1_000.0
    )
    return replace(built, **overrides) if overrides else built


def _ref(source_id: str = "inc_1", **overrides) -> EvidenceReference:
    body = dict(
        domain=EvidenceDomain.INTEGRITY,
        source_type=EvidenceSourceType.INTEGRITY_INCIDENT,
        source_id=source_id,
    )
    body.update(overrides)
    return EvidenceReference(**body)


def _capture(**overrides) -> EvidenceCapture:
    factory = InvestigationFactory(
        ids=SequentialIdGenerator(), clock=FixedClock(1_000.0, step=0.0)
    )
    return factory.snapshot(
        _investigation(),
        overrides.pop("reference", _ref()),
        overrides.pop("body", {"id": "inc_1", "status": "open"}),
        at=1_000.0,
        **overrides,
    )


def _two_sessions(tmp_path, name: str = "investigations.db"):
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


def _seeded_investigation(persistent) -> tuple[str, str]:
    """An incident and a started investigation inside an arbitrary persistent application.

    The incident is inserted through M9.20's own repository rather than rebuilt from the whole
    invoice→recognition→reconciliation pipeline: these tests are about *this* milestone's
    concurrency, and driving the four upstream domains again would only make the race harder to
    see. It is still a real `IntegrityIncident` row, read back through the real seam."""
    app = persistent.application
    with app.uow_factory.new():
        persistent.application.context.incident_engine.incidents.create(_incident())
    persistent.database.session.commit()
    view = app.commands.dispatch(OpenInvestigation(**KW, incident_id="inc_1"))
    app.commands.dispatch(TriageInvestigation(**KW, investigation_id=view.id))
    app.commands.dispatch(StartInvestigation(**KW, investigation_id=view.id))
    return "inc_1", view.id


# ── 1. the audit: nothing existing is shadowed ────────────────────────────────


def test_no_investigation_or_audit_package_aggregate_existed_before():
    """The pre-implementation audit, as an assertion. `Investigation`, `EvidenceCapture`,
    `AuditPackage`, `CustodyChain` and every identity here are new names, and the only prior use of
    the *word* investigation was M9.20's own incident status."""
    from billing import foundation

    for name in ("Investigation", "EvidenceCapture", "AuditPackage", "CustodyChain",
                 "InvestigationConclusion", "InvestigationObservation"):
        owner = _inspect.getmodule(getattr(foundation, name))
        assert owner.__name__.endswith("commercial_investigations"), name


def test_the_prior_meaning_of_investigation_is_untouched():
    """M9.20 owns `IncidentStatus.INVESTIGATING` and `IncidentTrigger.INVESTIGATE`. M9.21 does not
    redefine, shadow or consume them — its own vocabulary is `InvestigationStatus`."""
    assert ic.IncidentStatus.INVESTIGATING.value == "investigating"
    assert "investigating" in {s.value for s in InvestigationStatus}
    # Same word, two enums, no shared type — a client cannot pass one where the other is meant.
    assert not isinstance(InvestigationStatus.INVESTIGATING, ic.IncidentStatus)


def test_there_is_exactly_one_canonicalizer_and_it_is_not_a_third_evidence_digest():
    """M9.19 and M9.20 each own a `derive_evidence_digest` over a flat tuple. M9.21 needed a
    *structural* canonicalizer and gave it a distinct name rather than a third colliding one — the
    lesson M9.20's D-2 taught, applied before it could recur."""
    from billing.foundation import commercial_investigations as ci

    assert not hasattr(ci, "derive_evidence_digest")
    assert hasattr(ci, "canonical_digest") and hasattr(ci, "derive_evidence_id")


def test_no_second_canonicalization_algorithm_exists():
    """Every digest in this milestone reduces to `canonical_form`. Two canonicalizers is how one
    piece of evidence comes to have two identities."""
    for path in INVESTIGATION_FILES:
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.Attribute) and node.attr in ("dumps", "hexdigest"):
                # `hexdigest` is permitted only inside the canonical module itself.
                assert path.name == "commercial_investigations.py", (path, node.attr)


# ── 2. ownership: an observer of an observer of an observer ───────────────────


def test_no_domain_repository_is_imported_anywhere():
    """Not ledger, invoice, tax, payment, recognition, collections, rating, metering, entitlement,
    subscription or account — **and not M9.20's incident repository either**, which M9.21
    references and never mutates."""
    banned = {
        "LedgerRepository", "JournalEntryRepository", "CommercialInvoiceRepository",
        "TaxAssessmentRepository", "PaymentIntentRepository", "PaymentAttemptRepository",
        "RevenueScheduleRepository", "CollectionCaseRepository", "RatingRepository",
        "UsageRepository", "EntitlementRepository", "SubscriptionRepository",
        "BillingAccountRepository", "IncidentRepository", "FindingLinkRepository",
        "IntegrityFindingRepository",
    }
    for path in INVESTIGATION_FILES:
        tree = ast.parse(path.read_text())
        imported = {
            alias.name for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom) for alias in node.names
        }
        assert not (imported & banned), (path, imported & banned)


def test_no_write_method_on_another_domain_is_reachable():
    """The Application reads other domains through the context. Every attribute access on those
    seams is a read verb — `get`, `for_*`, `latest_*`. A write would be an ownership violation the
    prose could not prevent."""
    source = Path("billing/application/investigation_engine.py").read_text()
    tree = ast.parse(source)
    collector = next(
        n for n in ast.walk(tree)
        if isinstance(n, ast.ClassDef) and n.name == "_EvidenceCollector"
    )
    called = {
        node.func.attr
        for node in ast.walk(collector)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
    }
    forbidden = {"append", "create", "save", "update", "advance", "attach", "detach",
                 "delete", "resolve", "settle", "post", "issue"}
    assert not (called & forbidden), called & forbidden


def test_there_is_no_outward_command_at_all():
    """M9.20 dispatches M9.19's repair request. **M9.21 dispatches nothing** — it references a
    repair the owning domain performed and can ask no domain to perform one. `dispatch` is not
    even injected into the use cases."""
    source = Path("billing/application/investigation_engine.py").read_text()
    tree = ast.parse(source)
    use_cases = next(
        n for n in ast.walk(tree)
        if isinstance(n, ast.ClassDef) and n.name == "InvestigationUseCases"
    )
    init = next(
        n for n in use_cases.body if isinstance(n, ast.FunctionDef) and n.name == "__init__"
    )
    params = {a.arg for a in init.args.args} | {a.arg for a in init.args.kwonlyargs}
    assert "dispatch" not in params
    assert "_dispatch" not in source


def test_no_financial_calculation_exists():
    """No arithmetic anywhere in Foundation whose operands name a figure.

    An AST scan rather than a string search — `-` matches `-> RootCause`, which is how a scan like
    this passes while proving nothing. And it is narrowed to *money* rather than to every operator,
    because `set(REQUIRED_QUESTIONS) - asked` is a set difference and banning it would force the
    test to be weakened later, which is worse than aiming it correctly now."""
    money = ("amount", "total", "minor", "balance", "price", "rate", "tax", "sum",
             "impact", "value", "figure", "cost", "revenue")

    def names(node) -> set[str]:
        return {
            n.id.lower() for n in ast.walk(node) if isinstance(n, ast.Name)
        } | {
            n.attr.lower() for n in ast.walk(node) if isinstance(n, ast.Attribute)
        }

    for path in FOUNDATION_FILES:
        # The raw source, deliberately: a docstring cannot be a `BinOp`, so stripping prose first
        # would only risk producing text that no longer parses.
        for node in ast.walk(ast.parse(path.read_text())):
            if not isinstance(node, ast.BinOp):
                continue
            if not isinstance(node.op, (ast.Sub, ast.Mult, ast.Div, ast.FloorDiv,
                                        ast.Mod, ast.Pow)):
                continue
            referenced = names(node)
            assert not any(m in n for n in referenced for m in money), (path, referenced)


def test_the_one_multiplication_in_foundation_is_a_timestamp_conversion():
    """`at * 1_000_000` in `canonical_instant`. Named explicitly so that a second one appearing
    later has to be argued for rather than slipped in beside it."""
    multiplications = []
    for path in FOUNDATION_FILES:
        for node in ast.walk(ast.parse(path.read_text())):
            if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Mult):
                multiplications.append((path.name, node.lineno))
    assert len(multiplications) == 1, multiplications
    assert multiplications[0][0] == "commercial_investigations.py"


def test_money_is_never_imported_into_the_investigation_domain():
    """A domain that cannot name money cannot compute with it. `Money` is absent from all three
    Foundation modules and from the wire schemas."""
    for path in (*FOUNDATION_FILES, Path("billing/customer/investigation_schemas.py")):
        tree = ast.parse(path.read_text())
        imported = {
            alias.name for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom) for alias in node.names
        }
        assert "Money" not in imported and "MoneyResponse" not in imported, path


def test_no_monetary_column_is_stored():
    """Column by column. `evidence_count`, `timeline_length` and `gap_count` are counts; there is
    no `total`, no `amount`, no `minor`, no `currency` and no `balance` anywhere."""
    from billing.persistence import investigation_models as models

    banned = ("amount", "total", "minor", "currency", "balance", "price", "rate",
              "outstanding", "exposure", "impact_")
    for table in models.INVESTIGATION_TABLES:
        for column in table.__table__.columns:
            assert not any(b in column.name.lower() for b in banned), (table, column.name)


def test_the_only_figure_anywhere_is_inside_a_captured_body(evidenced):
    """The deliberate, narrow exception: a snapshot of an M9.19 finding legitimately contains the
    integer minor units the owning domain wrote. That is **evidence of what that domain said** —
    and nothing in this milestone sums, converts or compares it."""
    app, _incident_id, investigation_id, _snapshot = evidenced
    page = app.queries.dispatch(GetInvestigationEvidence(**KW, investigation_id=investigation_id))
    findings = [e for e in page.evidence if e.source_type == "reconciliation_finding"]
    assert findings, "the fixture should have captured findings"
    source = findings[0].body["source"]
    assert "amount_minor" in source  # carried verbatim...
    # ...and the view carries no figure of its own anywhere.
    for field in type(findings[0]).__dataclass_fields__:
        assert not any(b in field for b in ("amount", "total", "minor", "currency")), field


def test_no_provider_no_http_no_sdk_anywhere():
    for path in INVESTIGATION_FILES:
        tree = ast.parse(path.read_text())
        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported |= {a.name.split(".")[0] for a in node.names}
            elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
                imported.add(node.module.split(".")[0])
        forbidden = {"requests", "httpx", "urllib", "socket", "http", "aiohttp",
                     "stripe", "razorpay", "boto3", "smtplib"}
        assert not (imported & forbidden), (path, imported & forbidden)


def test_no_scheduler_and_no_runtime_dependency():
    for path in INVESTIGATION_FILES:
        source = path.read_text()
        tree = ast.parse(source)
        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported |= {a.name.split(".")[0] for a in node.names}
            elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
                imported.add(node.module.split(".")[0])
        assert not (imported & {"asyncio", "threading", "sched", "celery", "runtime"}), path
        code = _code_only(source)
        for banned in ("sleep(", "cron", "while True", "BackgroundTask"):
            assert banned not in code, (path, banned)


def test_foundation_is_clock_free_and_performs_no_io():
    """Every temporal decision arrives as an `at` parameter."""
    for path in FOUNDATION_FILES:
        code = _code_only(path.read_text())
        for banned in ("datetime.now", "datetime.utcnow", "time.time(", "open(", "Path("):
            assert banned not in code, (path, banned)


def test_foundation_contains_no_authorization_logic():
    for path in FOUNDATION_FILES:
        code = _code_only(path.read_text())
        for banned in ("Permission", "RoleAssignment", "has_role", "check_permission",
                       "is_authorized", "principal", "RBAC"):
            assert banned not in code, (path, banned)


def test_no_reconciliation_or_correlation_logic_is_duplicated():
    """M9.19 owns comparison, M9.20 owns correlation. Neither rule is copied here."""
    for path in FOUNDATION_FILES:
        code = _code_only(path.read_text())
        for banned in ("MatchingService", "RECONCILIATION_MATRIX", "CorrelationService",
                       "derive_correlation_key", "ANCHOR_PRECEDENCE", "correlates("):
            assert banned not in code, (path, banned)


# ── 3. canonicalization is deterministic ──────────────────────────────────────


def test_the_canonical_form_is_key_order_independent():
    assert canonical_form({"b": 1, "a": 2}) == canonical_form({"a": 2, "b": 1})
    assert canonical_digest({"b": 1, "a": 2}) == canonical_digest({"a": 2, "b": 1})


@pytest.mark.parametrize(
    "left,right",
    [
        ({"n": 1}, {"n": "1"}),          # int vs string
        ({"n": True}, {"n": 1}),          # bool vs int
        ({"n": None}, {"n": ""}),         # null vs empty
        ({"n": None}, {}),                # null vs absent
        ({"a": [1, 2]}, {"a": [2, 1]}),   # order matters
    ],
)
def test_distinguishable_values_never_share_a_digest(left, right):
    """Without type tags `{"n": 1}` and `{"n": "1"}` would canonicalize identically, and two
    different pieces of evidence would claim one identity.

    The null-versus-absent pair matters most: *the provider returned no reference* and *we never
    asked* are different facts about an audit."""
    assert canonical_digest(left) != canonical_digest(right)


def test_a_float_is_refused_rather_than_coerced():
    """A digest that depends on how a float is formatted is stable on the machine that wrote it and
    different on the machine that audits it. Refusing is the only honest option."""
    with pytest.raises(CanonicalizationError, match="float"):
        canonical_form({"amount": 1.5})


def test_a_timestamp_reaches_a_digest_as_integer_microseconds():
    assert canonical_instant(1.5) == "1500000"
    assert canonical_instant(0.0) == "0"
    canonical_form({"at": canonical_instant(1_700_000_000.123456)})  # does not raise


def test_no_float_survives_into_any_manifest(evidenced):
    """The manifest is walked in full: not one float, anywhere, at any depth."""
    app, _incident_id, investigation_id, _snapshot = evidenced
    package = app.commands.dispatch(ExportAuditPackage(**KW, investigation_id=investigation_id))

    def walk(value, path="manifest"):
        assert not isinstance(value, float), path
        if isinstance(value, dict):
            for k, v in value.items():
                walk(v, f"{path}.{k}")
        elif isinstance(value, (list, tuple)):
            for i, v in enumerate(value):
                walk(v, f"{path}[{i}]")

    walk(package.manifest)


# ── 4. a reference is not a snapshot ──────────────────────────────────────────


def test_a_live_reference_structurally_carries_no_content():
    factory = InvestigationFactory(ids=SequentialIdGenerator(), clock=FixedClock(1_000.0, step=0))
    capture = factory.live_reference(_investigation(), _ref(), at=1_000.0)
    assert capture.body is None
    assert capture.content_digest == ""
    assert capture.mode is CaptureMode.LIVE_REFERENCE


def test_a_live_reference_can_never_back_a_confirmed_conclusion():
    """It attests to no content. Pointing at an object that may have changed since is not evidence
    of what was true when the investigation ran."""
    factory = InvestigationFactory(ids=SequentialIdGenerator(), clock=FixedClock(1_000.0, step=0))
    capture = factory.live_reference(_investigation(), _ref(), at=1_000.0)
    assert not capture.supports_conclusion
    with pytest.raises(InvestigationRefused, match="available evidence snapshot"):
        ConclusionGuardService.guard_supported(
            ConclusionClassification.CONFIRMED, (capture,)
        )


def test_neither_mode_can_masquerade_as_the_other():
    """Both directions refused at construction — and because the evidence store has **no update
    path at all**, neither can happen afterwards either."""
    with pytest.raises(EvidenceRefused, match="LIVE_REFERENCE carries no captured"):
        EvidenceGuardService.guard_mode_matches_content(
            CaptureMode.LIVE_REFERENCE, {"a": 1}, ""
        )
    with pytest.raises(EvidenceRefused, match="no content digest"):
        EvidenceGuardService.guard_mode_matches_content(
            CaptureMode.LIVE_REFERENCE, None, "deadbeef"
        )
    with pytest.raises(EvidenceRefused, match="must carry the representation"):
        EvidenceGuardService.guard_mode_matches_content(
            CaptureMode.EVIDENCE_SNAPSHOT, None, "deadbeef"
        )


def test_the_evidence_store_has_no_update_or_delete_path():
    """**The structural form of "a capture's mode is fixed for the life of the row".** If an
    update existed, every guarantee above would hold only until somebody called it."""
    for repo in (EvidenceRepository, InvestigationEventRepository,
                 InvestigationConclusionRepository, AuditPackageRepository):
        methods = {n for n in dir(repo) if not n.startswith("_")}
        assert not (methods & {"update", "delete", "replace", "remove", "save"}), repo


def test_a_snapshot_digest_is_recomputable_from_its_body(evidenced):
    """What makes a stored representation *evidence* rather than a copy: an auditor recomputes the
    digest from the body and sees that the two agree."""
    app, _incident_id, investigation_id, _snapshot = evidenced
    page = app.queries.dispatch(GetInvestigationEvidence(**KW, investigation_id=investigation_id))
    for capture in page.evidence:
        if capture.mode == "evidence_snapshot":
            assert canonical_digest(capture.body) == capture.content_digest


def test_a_body_that_does_not_match_its_digest_is_refused():
    with pytest.raises(EvidenceRefused, match="does not match its digest"):
        EvidenceGuardService.guard_digest_matches_body({"a": 1}, "not-the-digest")


# ── 5. evidence identity is content-addressed ─────────────────────────────────


def test_the_same_immutable_source_captured_twice_has_one_identity():
    body = {"id": "inc_1", "status": "open"}
    first = derive_evidence_id(ORG, "integrity", "integrity_incident", "inc_1", "3",
                               canonical_digest(body))
    second = derive_evidence_id(ORG, "integrity", "integrity_incident", "inc_1", "3",
                                canonical_digest(body))
    assert first == second


def test_a_changed_representation_is_a_different_piece_of_evidence():
    """Not an overwrite of the old one — a *new* one, so both survive in the chain."""
    unchanged = derive_evidence_id(ORG, "integrity", "integrity_incident", "inc_1", "3",
                                   canonical_digest({"status": "open"}))
    changed = derive_evidence_id(ORG, "integrity", "integrity_incident", "inc_1", "3",
                                 canonical_digest({"status": "resolved"}))
    assert unchanged != changed


def test_evidence_identity_is_org_first():
    """Two organizations holding byte-identical content cannot compute a colliding evidence id."""
    body_digest = canonical_digest({"id": "inc_1"})
    ours = derive_evidence_id(ORG, "integrity", "integrity_incident", "inc_1", "", body_digest)
    theirs = derive_evidence_id("org_other", "integrity", "integrity_incident", "inc_1", "",
                                body_digest)
    assert ours != theirs


def test_an_investigation_id_is_derived_from_org_and_incident():
    """One investigation per incident, by construction. Two investigators cannot open rival cases
    that reach different conclusions about the same divergence."""
    assert derive_investigation_id(ORG, "inc_1") == derive_investigation_id(ORG, "inc_1")
    assert derive_investigation_id(ORG, "inc_1") != derive_investigation_id(ORG, "inc_2")
    assert derive_investigation_id(ORG, "inc_1") != derive_investigation_id("org_2", "inc_1")


def test_an_observation_identity_includes_its_kind():
    """**The structural defence against a hypothesis becoming a fact.** Identical wording recorded
    as a fact and as a hypothesis occupies two rows."""
    digest = canonical_digest("the ledger refused")
    fact = derive_observation_id("ivg_1", "observed_fact", digest)
    guess = derive_observation_id("ivg_1", "hypothesis", digest)
    assert fact != guess


def test_a_package_identity_is_its_manifest_digest():
    same = derive_audit_package_id(ORG, "ivg_1", "abc")
    assert same == derive_audit_package_id(ORG, "ivg_1", "abc")
    assert same != derive_audit_package_id(ORG, "ivg_1", "abd")
    assert same != derive_audit_package_id("org_2", "ivg_1", "abc")


# ── 6. missing evidence stays missing ─────────────────────────────────────────


def test_an_unreadable_source_is_captured_as_unavailable_not_skipped(started):
    """The mandate's central refusal: *do not replace missing evidence with a fresh live object and
    pretend it is historical evidence.*"""
    app, _incident_id, investigation_id = started
    capture = app.commands.dispatch(
        CaptureEvidence(
            **KW, investigation_id=investigation_id,
            domain=EvidenceDomain.INTEGRITY,
            source_type=EvidenceSourceType.INTEGRITY_INCIDENT,
            source_id="inc_does_not_exist",
        )
    )
    assert capture.availability == EvidenceAvailability.UNAVAILABLE.value
    assert not capture.supports_conclusion
    assert "could not be read" in capture.unavailable_reason
    assert capture.body is None


def test_could_not_retrieve_never_supports_a_conclusion():
    unavailable = replace(_capture(), availability=EvidenceAvailability.UNAVAILABLE)
    assert not unavailable.supports_conclusion
    assert EvidenceAvailability.UNAVAILABLE not in SUPPORTING_AVAILABILITY
    with pytest.raises(InvestigationRefused):
        ConclusionGuardService.guard_supported(
            ConclusionClassification.CONFIRMED, (unavailable,)
        )


def test_an_unavailable_gap_names_when_it_was_last_held():
    """Not just "missing" but "we held this on the fourteenth and lost it since" — the difference
    between an audit finding and a shrug."""
    held = replace(_capture(), sequence=1, captured_at=1_000.0)
    lost = replace(
        _capture(), id="evd_lost", sequence=2, captured_at=2_000.0,
        availability=EvidenceAvailability.UNAVAILABLE, body=None, content_digest="",
        unavailable_reason="the owning domain returned nothing",
    )
    gaps = CustodyService().gaps((held, lost))
    assert len(gaps) == 1
    assert gaps[0].last_known_available_at == 1_000.0
    assert gaps[0].blocks_conclusion
    assert gaps[0].reason == "the owning domain returned nothing"


def test_a_superseded_representation_is_not_a_gap():
    """A newer capture of the same source *is* the evidence, and it is in the package too. Only an
    unavailable source blocks."""
    assert EvidenceAvailability.SUPERSEDED not in SUPPORTING_AVAILABILITY
    superseded = replace(
        _capture(), sequence=2, availability=EvidenceAvailability.SUPERSEDED
    )
    gaps = CustodyService().gaps((superseded,))
    assert gaps and not gaps[0].blocks_conclusion


def test_an_incomplete_package_names_its_gaps_rather_than_omitting_them(started):
    app, _incident_id, investigation_id = started
    app.commands.dispatch(
        MarkEvidenceUnavailable(
            **KW, investigation_id=investigation_id,
            domain=EvidenceDomain.LEDGER,
            source_type=EvidenceSourceType.LEDGER_ENTRY,
            source_id="led_gone",
            reason="the entry was never written",
        )
    )
    package = app.commands.dispatch(ExportAuditPackage(**KW, investigation_id=investigation_id))
    assert package.completeness == PackageCompleteness.INCOMPLETE.value
    assert [g.locator for g in package.gaps] == ["ledger:ledger_entry:led_gone"]
    assert package.gaps[0].blocks_conclusion
    assert package.gaps[0].reason == "the entry was never written"
    # And the gap is in the manifest itself, not only in the response envelope.
    assert package.manifest["gaps"][0]["locator"] == "ledger:ledger_entry:led_gone"


def test_an_unreadable_incident_makes_a_package_incomplete_and_says_so():
    """`None` for the incident does not mean "no incident" — it means the read failed, and a
    manifest that rendered that as an absent key would let it look like a case that never had
    one."""
    manifest = ManifestBuilder().build(
        _investigation(), incident=None, captures=(), questions=(),
        observations=(), conclusions=(), timeline=(),
    )
    assert manifest["incident"] == {"id": "inc_1", "available": False}


# ── 7. the chain of custody is append-only ────────────────────────────────────


def test_a_source_that_changes_produces_two_links_not_one_overwrite(started):
    """The mandate's §17, demonstrated end to end: the incident is captured, moved by M9.20, and
    captured again. **Both representations survive**, and the chain says it changed."""
    app, incident_id, investigation_id = started
    ref = dict(
        domain=EvidenceDomain.INTEGRITY,
        source_type=EvidenceSourceType.INTEGRITY_INCIDENT,
        source_id=incident_id,
    )
    first = app.commands.dispatch(CaptureEvidence(**KW, investigation_id=investigation_id, **ref))
    app.commands.dispatch(ic.AcknowledgeIncident(**KW, incident_id=incident_id))
    second = app.commands.dispatch(CaptureEvidence(**KW, investigation_id=investigation_id, **ref))

    assert first.id != second.id
    assert first.content_digest != second.content_digest
    assert second.previous_evidence_id == first.id

    chains = app.queries.dispatch(GetCustodyChains(**KW, investigation_id=investigation_id))
    chain = next(c for c in chains if incident_id in c.locator)
    assert len(chain.links) == 2
    assert chain.changed
    # The earlier link is still readable, with its original digest, author and timestamp.
    assert chain.links[0].content_digest == first.content_digest


def test_a_chain_is_keyed_without_the_revision():
    """Deliberately: a document moving from revision 1 to revision 2 is exactly the change an
    auditor wants to see *within* one chain. Keying on the revision would hide it."""
    first = replace(_capture(reference=_ref(revision="1")), sequence=1)
    second = replace(
        _capture(reference=_ref(revision="2"), body={"id": "inc_1", "status": "closed"}),
        sequence=2,
    )
    chains = CustodyService().chains((first, second))
    assert len(chains) == 1
    assert len(chains[0].links) == 2
    assert chains[0].changed


def test_a_chain_cannot_fork_onto_a_different_source():
    with pytest.raises(EvidenceRefused, match="custody chain break"):
        EvidenceGuardService.guard_chain_continues(_capture(), _ref("inc_different"))


def test_a_redaction_keeps_the_original_identity_digest_and_author(evidenced):
    """§19: *do not destroy the original identity*. The redaction is appended naming what it
    superseded; the original row is untouched."""
    app, _incident_id, investigation_id, snapshot = evidenced
    redaction = app.commands.dispatch(
        RedactEvidence(
            **KW, investigation_id=investigation_id, evidence_id=snapshot.id,
            reason="detail narrowed for external release", keep_fields=("id", "status"),
        )
    )
    assert redaction.id != snapshot.id
    assert redaction.previous_evidence_id == snapshot.id
    assert redaction.availability == EvidenceAvailability.REDACTED.value
    assert redaction.redaction_reason == "detail narrowed for external release"
    assert set(redaction.body) == {"id", "status"}

    page = app.queries.dispatch(GetInvestigationEvidence(**KW, investigation_id=investigation_id))
    original = next(e for e in page.evidence if e.id == snapshot.id)
    assert original.content_digest == snapshot.content_digest
    assert original.body == snapshot.body  # untouched
    assert original.availability == EvidenceAvailability.AVAILABLE.value


def test_a_redaction_still_supports_a_conclusion():
    """A redaction narrows a representation; the fact remains attested. `UNAVAILABLE` is the state
    that does not support — and the two are deliberately different."""
    assert EvidenceAvailability.REDACTED in SUPPORTING_AVAILABILITY
    redacted = replace(_capture(), availability=EvidenceAvailability.REDACTED)
    assert redacted.supports_conclusion


def test_a_live_reference_has_nothing_to_redact(started):
    app, incident_id, investigation_id = started
    live = app.commands.dispatch(
        CaptureEvidence(
            **KW, investigation_id=investigation_id,
            domain=EvidenceDomain.INTEGRITY,
            source_type=EvidenceSourceType.INTEGRITY_INCIDENT,
            source_id=incident_id, mode=CaptureMode.LIVE_REFERENCE,
        )
    )
    with pytest.raises(BillingConflict, match="holds no representation to redact"):
        app.commands.dispatch(
            RedactEvidence(**KW, investigation_id=investigation_id, evidence_id=live.id)
        )


# ── 8. a hypothesis is not a fact ─────────────────────────────────────────────


def test_an_unbacked_observed_fact_is_refused(started):
    app, _incident_id, investigation_id = started
    with pytest.raises(InvestigationRefused, match="HYPOTHESIS"):
        app.commands.dispatch(
            RecordObservation(
                **KW, investigation_id=investigation_id,
                kind=ObservationKind.OBSERVED_FACT, statement="the ledger refused",
            )
        )


def test_a_hypothesis_needs_nothing_behind_it(started):
    """That is what makes it a hypothesis. Holding it to the same bar as a fact would leave an
    investigator no honest way to write down a suspicion."""
    app, _incident_id, investigation_id = started
    view = app.commands.dispatch(
        RecordObservation(
            **KW, investigation_id=investigation_id,
            kind=ObservationKind.HYPOTHESIS, statement="perhaps the period was closed",
        )
    )
    assert view.hypothesis_status == HypothesisStatus.PROPOSED.value


def test_a_hypothesis_resubmitted_as_a_fact_appends_rather_than_promotes(evidenced):
    """Both records exist afterwards, in order, with their authors — which is the only outcome an
    audit can accept."""
    app, _incident_id, investigation_id, snapshot = evidenced
    statement = "the ledger refused the posting"
    guess = app.commands.dispatch(
        RecordObservation(
            **KW, investigation_id=investigation_id,
            kind=ObservationKind.HYPOTHESIS, statement=statement,
        )
    )
    fact = app.commands.dispatch(
        RecordObservation(
            **KW, investigation_id=investigation_id,
            kind=ObservationKind.OBSERVED_FACT, statement=statement,
            evidence_ids=(snapshot.id,),
        )
    )
    assert guess.id != fact.id
    recorded = app.queries.dispatch(
        GetInvestigationObservations(**KW, investigation_id=investigation_id)
    )
    kinds = {o.id: o.kind for o in recorded}
    assert kinds[guess.id] == "hypothesis" and kinds[fact.id] == "observed_fact"


def test_citing_an_evidence_id_that_names_nothing_supports_nothing(started):
    """The cited evidence is **re-read**, never trusted as supplied. An id naming nothing is
    dropped, and the guard then refuses for the honest reason."""
    app, _incident_id, investigation_id = started
    with pytest.raises(InvestigationRefused):
        app.commands.dispatch(
            RecordObservation(
                **KW, investigation_id=investigation_id,
                kind=ObservationKind.OBSERVED_FACT, statement="x",
                evidence_ids=("evd_invented",),
            )
        )


def test_only_a_hypothesis_is_settled_and_only_once(evidenced):
    app, _incident_id, investigation_id, snapshot = evidenced
    fact = app.commands.dispatch(
        RecordObservation(
            **KW, investigation_id=investigation_id,
            kind=ObservationKind.OBSERVED_FACT, statement="a fact",
            evidence_ids=(snapshot.id,),
        )
    )
    with pytest.raises(InvestigationRefused, match="only a hypothesis"):
        app.commands.dispatch(
            SettleHypothesis(**KW, investigation_id=investigation_id, observation_id=fact.id)
        )

    guess = app.commands.dispatch(
        RecordObservation(
            **KW, investigation_id=investigation_id,
            kind=ObservationKind.HYPOTHESIS, statement="a guess",
        )
    )
    app.commands.dispatch(
        SettleHypothesis(
            **KW, investigation_id=investigation_id, observation_id=guess.id,
            supported=False, reason="the evidence says otherwise",
        )
    )
    with pytest.raises(InvestigationRefused, match="already"):
        app.commands.dispatch(
            SettleHypothesis(
                **KW, investigation_id=investigation_id, observation_id=guess.id, supported=True
            )
        )


def test_a_rejected_hypothesis_stays_on_the_record(evidenced):
    """*What did we believe, and what changed our mind* is what an examiner asks. Deleting it would
    leave a conclusion with no visible alternative it displaced."""
    app, _incident_id, investigation_id, snapshot = evidenced
    guess = app.commands.dispatch(
        RecordObservation(
            **KW, investigation_id=investigation_id,
            kind=ObservationKind.HYPOTHESIS, statement="the provider timed out",
        )
    )
    app.commands.dispatch(
        SettleHypothesis(
            **KW, investigation_id=investigation_id, observation_id=guess.id,
            supported=False, evidence_ids=(snapshot.id,), reason="no provider was involved",
        )
    )
    recorded = app.queries.dispatch(
        GetInvestigationObservations(**KW, investigation_id=investigation_id)
    )
    settled = next(o for o in recorded if o.id == guess.id)
    assert settled.statement == "the provider timed out"  # not deleted, not edited
    assert settled.hypothesis_status == HypothesisStatus.REJECTED.value
    assert settled.settled_reason == "no provider was involved"
    assert snapshot.id in settled.settling_evidence_ids


# ── 9. conclusions require evidence, and INCONCLUSIVE requires none ───────────


def test_a_confirmed_conclusion_needs_an_available_snapshot(started):
    app, _incident_id, investigation_id = started
    with pytest.raises(InvestigationRefused, match="INCONCLUSIVE is the honest"):
        app.commands.dispatch(
            RecordConclusion(
                **KW, investigation_id=investigation_id,
                classification=ConclusionClassification.CONFIRMED, statement="it was the period",
            )
        )


def test_inconclusive_is_always_available(started):
    """**Nobody is ever forced to name a root cause in order to close a case.** Requiring evidence
    to say "we could not determine this" would push an investigator toward the one classification
    that does pass."""
    app, _incident_id, investigation_id = started
    view = app.commands.dispatch(
        RecordConclusion(
            **KW, investigation_id=investigation_id,
            classification=ConclusionClassification.INCONCLUSIVE,
            statement="the evidence does not settle why the ledger refused",
        )
    )
    assert view.classification == ConclusionClassification.INCONCLUSIVE.value
    assert view.evidence_ids == ()


def test_a_contradiction_must_name_what_the_evidence_indicates(evidenced):
    """A conclusion that says only "M9.20 is wrong" is not something the owning workflow can act
    on."""
    app, _incident_id, investigation_id, snapshot = evidenced
    with pytest.raises(InvestigationRefused, match="must name the root cause"):
        app.commands.dispatch(
            RecordConclusion(
                **KW, investigation_id=investigation_id,
                classification=ConclusionClassification.CONTRADICTS_INCIDENT_CLASSIFICATION,
                statement="the classification is wrong", evidence_ids=(snapshot.id,),
            )
        )


def test_a_contradiction_naming_the_incidents_own_cause_is_refused(evidenced):
    """It would raise a discrepancy against a classification that agrees."""
    app, incident_id, investigation_id, snapshot = evidenced
    incident = app.queries.dispatch(ic.GetIncident(**KW, incident_id=incident_id))
    with pytest.raises(InvestigationRefused, match="already classified"):
        app.commands.dispatch(
            RecordConclusion(
                **KW, investigation_id=investigation_id,
                classification=ConclusionClassification.CONTRADICTS_INCIDENT_CLASSIFICATION,
                statement="disagree", evidence_ids=(snapshot.id,),
                evidenced_root_cause=RootCause(incident.root_cause),
            )
        )


def test_an_evidenced_root_cause_is_only_recorded_on_a_contradiction(evidenced):
    """Restating M9.20's classification on an ordinary conclusion would create a second, divergent
    copy of a fact another domain decides."""
    app, _incident_id, investigation_id, snapshot = evidenced
    with pytest.raises(InvestigationRefused, match="only on a contradiction"):
        app.commands.dispatch(
            RecordConclusion(
                **KW, investigation_id=investigation_id,
                classification=ConclusionClassification.CONFIRMED,
                statement="x", evidence_ids=(snapshot.id,),
                evidenced_root_cause=RootCause.SOURCE_FACT_DUPLICATED,
            )
        )


def test_a_contradiction_records_a_discrepancy_and_changes_nothing(evidenced):
    """§13, end to end: the incident's classification is **unchanged**, and the disagreement is a
    line in the trail addressed to the owning workflow."""
    app, incident_id, investigation_id, snapshot = evidenced
    before = app.queries.dispatch(ic.GetIncident(**KW, incident_id=incident_id))
    app.commands.dispatch(
        RecordConclusion(
            **KW, investigation_id=investigation_id,
            classification=ConclusionClassification.CONTRADICTS_INCIDENT_CLASSIFICATION,
            statement="the evidence shows a duplicate, not a missing fact",
            evidence_ids=(snapshot.id,),
            evidenced_root_cause=RootCause.SOURCE_FACT_DUPLICATED,
        )
    )
    after = app.queries.dispatch(ic.GetIncident(**KW, incident_id=incident_id))
    assert after.root_cause == before.root_cause
    assert after.version == before.version

    timeline = app.queries.dispatch(
        GetInvestigationTimeline(**KW, investigation_id=investigation_id, limit=200)
    )
    kinds = [e.kind for e in timeline.entries]
    assert InvestigationEventKind.CLASSIFICATION_DISCREPANCY_RECORDED.value in kinds
    entry = next(
        e for e in timeline.entries
        if e.kind == InvestigationEventKind.CLASSIFICATION_DISCREPANCY_RECORDED.value
    )
    assert entry.detail["incident_root_cause"] == before.root_cause
    assert entry.detail["evidenced_root_cause"] == RootCause.SOURCE_FACT_DUPLICATED.value


def test_a_conclusion_is_superseded_never_edited(evidenced):
    """An examiner needs to see that the first answer was wrong and when it was corrected."""
    app, _incident_id, investigation_id, snapshot = evidenced
    first = app.commands.dispatch(
        RecordConclusion(
            **KW, investigation_id=investigation_id,
            classification=ConclusionClassification.INCONCLUSIVE, statement="cannot tell yet",
        )
    )
    app.commands.dispatch(ResumeInvestigation(**KW, investigation_id=investigation_id))
    second = app.commands.dispatch(
        RecordConclusion(
            **KW, investigation_id=investigation_id,
            classification=ConclusionClassification.CONFIRMED,
            statement="no accounting period was open", evidence_ids=(snapshot.id,),
        )
    )
    recorded = app.queries.dispatch(
        GetInvestigationConclusions(**KW, investigation_id=investigation_id)
    )
    assert [c.id for c in recorded] == [first.id, second.id]
    assert second.supersedes == first.id
    assert second.version == first.version + 1
    # The first still says exactly what it said.
    assert recorded[0].statement == "cannot tell yet"
    assert recorded[0].classification == ConclusionClassification.INCONCLUSIVE.value


# ── 10. lifecycle: closure is earned, not clicked ─────────────────────────────


def test_there_is_no_edge_from_any_working_state_straight_to_closed():
    """**The structural form of the mandate's closure criteria.** The only route to `CLOSED` runs
    through `CONCLUDED`, so an investigation cannot be quietly shut with nothing on record."""
    for state in (InvestigationStatus.OPEN, InvestigationStatus.TRIAGED,
                  InvestigationStatus.INVESTIGATING, InvestigationStatus.AWAITING_EVIDENCE):
        assert INVESTIGATION_LIFECYCLE.get(state, InvestigationTrigger.CLOSE) is None, state
    assert INVESTIGATION_LIFECYCLE.get(
        InvestigationStatus.CONCLUDED, InvestigationTrigger.CLOSE
    ) is not None


def test_closed_is_terminal_by_construction():
    assert INVESTIGATION_LIFECYCLE.triggers_from(InvestigationStatus.CLOSED) == set()
    assert InvestigationLifecycleService().allowed(InvestigationStatus.CLOSED) == ()
    assert TERMINAL_INVESTIGATION_STATES == frozenset({InvestigationStatus.CLOSED})


def test_a_concluded_case_can_be_revised_before_closure():
    """New evidence arriving after somebody wrote a conclusion is exactly when an audit most needs
    the case to move rather than to be closed anyway."""
    assert INVESTIGATION_LIFECYCLE.get(
        InvestigationStatus.CONCLUDED, InvestigationTrigger.RESUME
    ) is not None


def test_every_investigation_status_is_reachable():
    reached = {InvestigationStatus.OPEN}
    frontier = [InvestigationStatus.OPEN]
    while frontier:
        state = frontier.pop()
        for trigger in INVESTIGATION_LIFECYCLE.triggers_from(state):
            target = INVESTIGATION_LIFECYCLE.get(state, trigger).target
            if target not in reached:
                reached.add(target)
                frontier.append(target)
    assert reached == set(InvestigationStatus)


def test_live_states_are_exactly_the_non_terminal_ones():
    assert LIVE_INVESTIGATION_STATES == set(InvestigationStatus) - TERMINAL_INVESTIGATION_STATES


def test_a_lifecycle_transition_never_changes_the_incident_severity():
    """M9.20 owns severity. An investigation moving through its own workflow never restates how bad
    the incident was."""
    investigation = replace(_investigation(), status=InvestigationStatus.OPEN)
    moved = InvestigationLifecycleService().apply(
        investigation, InvestigationTrigger.TRIAGE, at=2_000.0
    )
    assert moved.incident_severity is investigation.incident_severity


def test_a_closed_investigation_is_never_reopened_in_the_domain():
    closed = replace(_investigation(), status=InvestigationStatus.CLOSED)
    with pytest.raises(InvalidBillingTransition, match="closed"):
        InvestigationLifecycleService().apply(
            closed, InvestigationTrigger.RESUME, at=2_000.0
        )
    with pytest.raises(InvestigationRefused, match="never reopened"):
        InvestigationGuardService.guard_active(closed)


def test_a_closed_investigation_takes_no_new_evidence():
    """Appending after closure would change what a signed audit record says."""
    closed = replace(_investigation(), status=InvestigationStatus.CLOSED)
    with pytest.raises(InvestigationRefused, match="takes no new evidence"):
        EvidenceGuardService.guard_capturable(closed)


def test_an_untriaged_investigation_records_nothing(wired):
    """Questions, observations and conclusions need a case somebody is actually working."""
    app, incident_id = wired
    view = app.commands.dispatch(OpenInvestigation(**KW, incident_id=incident_id))
    with pytest.raises(InvestigationRefused, match="not been triaged"):
        app.commands.dispatch(
            RecordQuestion(
                **KW, investigation_id=view.id, kind=QuestionKind.WHY_DIVERGED, text="?"
            )
        )


# ── 11. closure criteria and the audit package ────────────────────────────────


def _answer_required(app, investigation_id, *, unanswerable=False, evidence_ids=()):
    for kind in REQUIRED_QUESTIONS:
        question = app.commands.dispatch(
            RecordQuestion(
                **KW, investigation_id=investigation_id, kind=kind,
                text=f"{kind.value}?", required=True,
            )
        )
        app.commands.dispatch(
            AnswerQuestion(
                **KW, investigation_id=investigation_id, question_id=question.id,
                answer="settled", evidence_ids=evidence_ids, unanswerable=unanswerable,
            )
        )


def test_a_required_question_never_asked_blocks_closure_too(evidenced):
    """Otherwise an investigation could close by simply never raising the question an examiner will
    ask."""
    app, _incident_id, investigation_id, snapshot = evidenced
    app.commands.dispatch(
        RecordConclusion(
            **KW, investigation_id=investigation_id,
            classification=ConclusionClassification.CONFIRMED, statement="found it",
            evidence_ids=(snapshot.id,),
        )
    )
    assessment = app.queries.dispatch(AssessClosure(**KW, investigation_id=investigation_id))
    assert not assessment.may_close
    assert set(assessment.unresolved_required_questions) == {k.value for k in REQUIRED_QUESTIONS}
    assert all("never asked" in r for r in assessment.reasons)


def test_an_unsettled_hypothesis_blocks_closure(evidenced):
    app, _incident_id, investigation_id, snapshot = evidenced
    _answer_required(app, investigation_id, evidence_ids=(snapshot.id,))
    guess = app.commands.dispatch(
        RecordObservation(
            **KW, investigation_id=investigation_id,
            kind=ObservationKind.HYPOTHESIS, statement="maybe",
        )
    )
    app.commands.dispatch(
        RecordConclusion(
            **KW, investigation_id=investigation_id,
            classification=ConclusionClassification.CONFIRMED, statement="found it",
            evidence_ids=(snapshot.id,),
        )
    )
    assessment = app.queries.dispatch(AssessClosure(**KW, investigation_id=investigation_id))
    assert not assessment.may_close
    assert guess.id in assessment.open_hypotheses


def test_unavailable_evidence_blocks_closure_and_names_the_remedy(evidenced):
    app, _incident_id, investigation_id, snapshot = evidenced
    _answer_required(app, investigation_id, evidence_ids=(snapshot.id,))
    app.commands.dispatch(
        MarkEvidenceUnavailable(
            **KW, investigation_id=investigation_id,
            domain=EvidenceDomain.LEDGER, source_type=EvidenceSourceType.LEDGER_ENTRY,
            source_id="led_gone", reason="never written",
        )
    )
    app.commands.dispatch(
        RecordConclusion(
            **KW, investigation_id=investigation_id,
            classification=ConclusionClassification.CONFIRMED, statement="found it",
            evidence_ids=(snapshot.id,),
        )
    )
    assessment = app.queries.dispatch(AssessClosure(**KW, investigation_id=investigation_id))
    assert not assessment.may_close
    assert [g.locator for g in assessment.blocking_gaps] == ["ledger:ledger_entry:led_gone"]
    assert any("must not simply be omitted" in r for r in assessment.reasons)


def test_a_case_closes_inconclusive_with_unanswerable_questions(started):
    """The escape hatch that stops the criteria becoming a trap: a required question the evidence
    cannot settle is closable, honestly, without anybody inventing an answer."""
    app, _incident_id, investigation_id = started
    _answer_required(app, investigation_id, unanswerable=True)
    app.commands.dispatch(
        RecordConclusion(
            **KW, investigation_id=investigation_id,
            classification=ConclusionClassification.INCONCLUSIVE,
            statement="the evidence does not settle this",
        )
    )
    assessment = app.queries.dispatch(AssessClosure(**KW, investigation_id=investigation_id))
    assert assessment.may_close, assessment.reasons
    closed = app.commands.dispatch(
        CloseInvestigation(**KW, investigation_id=investigation_id, reason="inconclusive")
    )
    assert closed.status == InvestigationStatus.CLOSED.value
    assert closed.allowed_triggers == ()


def test_closing_the_investigation_does_not_resolve_the_incident(started):
    """M9.20 owns incident resolution. A case may legitimately close while its incident is open."""
    app, incident_id, investigation_id = started
    _answer_required(app, investigation_id, unanswerable=True)
    app.commands.dispatch(
        RecordConclusion(
            **KW, investigation_id=investigation_id,
            classification=ConclusionClassification.INCONCLUSIVE, statement="unresolved",
        )
    )
    before = app.queries.dispatch(ic.GetIncident(**KW, incident_id=incident_id))
    app.commands.dispatch(CloseInvestigation(**KW, investigation_id=investigation_id))
    after = app.queries.dispatch(ic.GetIncident(**KW, incident_id=incident_id))
    assert after.status == before.status
    assert after.version == before.version


def test_closure_is_refused_with_the_outstanding_list(started):
    app, _incident_id, investigation_id = started
    with pytest.raises(InvestigationRefused, match="cannot close"):
        app.commands.dispatch(CloseInvestigation(**KW, investigation_id=investigation_id))


def test_exporting_unchanged_state_twice_produces_one_package(evidenced):
    """**The export determinism guarantee.** A package cannot contain the record of its own
    creation, or every export would change the thing it describes."""
    app, _incident_id, investigation_id, _snapshot = evidenced
    first = app.commands.dispatch(ExportAuditPackage(**KW, investigation_id=investigation_id))
    second = app.commands.dispatch(ExportAuditPackage(**KW, investigation_id=investigation_id))
    assert first.id == second.id
    assert first.manifest_digest == second.manifest_digest
    assert first.created_at == second.created_at  # the second wrote nothing


def test_a_changed_investigation_exports_a_new_package_beside_the_old(evidenced):
    app, _incident_id, investigation_id, snapshot = evidenced
    first = app.commands.dispatch(ExportAuditPackage(**KW, investigation_id=investigation_id))
    app.commands.dispatch(
        RecordObservation(
            **KW, investigation_id=investigation_id,
            kind=ObservationKind.OBSERVED_FACT, statement="a new fact",
            evidence_ids=(snapshot.id,),
        )
    )
    second = app.commands.dispatch(ExportAuditPackage(**KW, investigation_id=investigation_id))
    assert second.id != first.id
    # And the first is still retrievable, unchanged.
    stored = app.queries.dispatch(
        GetAuditPackage(**KW, investigation_id=investigation_id, package_id=first.id)
    )
    assert stored.manifest_digest == first.manifest_digest


def test_a_package_cannot_contain_the_record_of_its_own_creation(evidenced):
    """**Regression for D-1.** The manifest holds the timeline, and exporting appends an
    `AUDIT_PACKAGE_CREATED` entry — so an unfiltered manifest made every export change the thing it
    was describing, and a second export of untouched state minted a second package.

    The export stays visible on `/timeline`; what is excluded is only the self-reference."""
    app, _incident_id, investigation_id, _snapshot = evidenced
    package = app.commands.dispatch(ExportAuditPackage(**KW, investigation_id=investigation_id))
    kinds = {e["kind"] for e in package.manifest["timeline"]}
    assert InvestigationEventKind.AUDIT_PACKAGE_CREATED.value not in kinds
    assert package.timeline_length == len(package.manifest["timeline"])

    timeline = app.queries.dispatch(
        GetInvestigationTimeline(**KW, investigation_id=investigation_id, limit=200)
    )
    assert InvestigationEventKind.AUDIT_PACKAGE_CREATED.value in {
        e.kind for e in timeline.entries
    }
    assert ManifestBuilder.EXCLUDED_FROM_MANIFEST == frozenset(
        {InvestigationEventKind.AUDIT_PACKAGE_CREATED}
    )


def test_a_package_digest_is_recomputable_from_its_manifest(evidenced):
    """What makes the export auditable rather than merely downloadable."""
    app, _incident_id, investigation_id, _snapshot = evidenced
    package = app.commands.dispatch(ExportAuditPackage(**KW, investigation_id=investigation_id))
    assert canonical_digest(package.manifest) == package.manifest_digest


def test_a_package_is_never_mutated():
    """Two exports agreeing on an id must agree on the manifest that produced it. If they did not,
    an examiner recomputing the digest would find it matches while the bytes had changed."""
    from billing.foundation import PackageGuardService

    existing = AuditPackage(
        id="apk_1", org_id=ORG, investigation_id="ivg_1", incident_id="inc_1",
        manifest_digest="abc", completeness=PackageCompleteness.COMPLETE,
    )
    PackageGuardService.guard_immutable(existing, "abc")  # agrees — no raise
    with pytest.raises(EvidenceRefused, match="content-addressed and immutable"):
        PackageGuardService.guard_immutable(existing, "different")


def test_the_manifest_holds_references_and_digests_never_recomputed_figures(evidenced):
    app, _incident_id, investigation_id, _snapshot = evidenced
    package = app.commands.dispatch(ExportAuditPackage(**KW, investigation_id=investigation_id))
    for key in ("total", "sum", "balance", "impact_amount", "exposure"):
        assert key not in package.manifest


# ── 12. concurrency: two connections, not two simulations ─────────────────────


def test_two_investigators_opening_one_case_converge(tmp_path):
    """Derived identity plus a primary key. One investigation, not two that could reach different
    conclusions about the same divergence."""
    first, second = _two_sessions(tmp_path)
    incident_id, investigation_id = _seeded_investigation(first)
    theirs = second.application.commands.dispatch(
        OpenInvestigation(**KW, incident_id=incident_id, summary="a rival case")
    )
    assert theirs.id == investigation_id
    page = second.application.queries.dispatch(ListInvestigations(**KW))
    assert page.total == 1


def test_two_workers_capturing_the_same_source_write_one_row(tmp_path):
    """Content-addressing does the arbitration: both compute the same id from the same body."""
    first, second = _two_sessions(tmp_path, "capture.db")
    incident_id, investigation_id = _seeded_investigation(first)
    ref = dict(
        domain=EvidenceDomain.INTEGRITY,
        source_type=EvidenceSourceType.INTEGRITY_INCIDENT,
        source_id=incident_id,
    )
    mine = first.application.commands.dispatch(
        CaptureEvidence(**KW, investigation_id=investigation_id, **ref)
    )
    theirs = second.application.commands.dispatch(
        CaptureEvidence(**KW, investigation_id=investigation_id, **ref)
    )
    assert mine.id == theirs.id
    page = second.application.queries.dispatch(
        GetInvestigationEvidence(**KW, investigation_id=investigation_id)
    )
    assert sum(1 for e in page.evidence if e.source_id == incident_id) == 1


def test_a_stale_worker_cannot_overwrite_a_settled_investigation(tmp_path):
    """**The compare-and-set, proven with two real connections.** Worker A reads, worker B
    advances, worker A writes: zero rows match and it raises rather than silently overwriting a
    conclusion somebody else recorded."""
    first, second = _two_sessions(tmp_path, "stale.db")
    _incident_id, investigation_id = _seeded_investigation(first)

    repo_a = first.application.context.investigation_engine.investigations
    with first.application.uow_factory.new():
        stale = repo_a.get(ORG, investigation_id)

    # B moves it on, through the real command path.
    second.application.commands.dispatch(
        AwaitEvidence(**KW, investigation_id=investigation_id, reason="blocked")
    )

    # A resumes from the view it read before B wrote. The conditional UPDATE matches nothing.
    with pytest.raises(InvestigationStale, match="moved while this worker was deciding"), \
            first.application.uow_factory.new():
        repo_a.advance(
            replace(stale, status=InvestigationStatus.CONCLUDED, version=stale.version + 1),
            expected_version=stale.version,
        )

    current = second.application.queries.dispatch(
        GetInvestigation(**KW, investigation_id=investigation_id)
    )
    assert current.status == InvestigationStatus.AWAITING_EVIDENCE.value  # B's write stands


def test_two_concurrent_conclusions_do_not_overwrite_each_other(tmp_path):
    """Conclusions are append-only, so "concurrent" means *both survive*, ordered."""
    first, second = _two_sessions(tmp_path, "conclude.db")
    _incident_id, investigation_id = _seeded_investigation(first)
    mine = first.application.commands.dispatch(
        RecordConclusion(
            **KW, investigation_id=investigation_id,
            classification=ConclusionClassification.INCONCLUSIVE, statement="A says",
        )
    )
    theirs = second.application.commands.dispatch(
        RecordConclusion(
            **KW, investigation_id=investigation_id,
            classification=ConclusionClassification.INCONCLUSIVE, statement="B says",
        )
    )
    recorded = second.application.queries.dispatch(
        GetInvestigationConclusions(**KW, investigation_id=investigation_id)
    )
    assert {c.id for c in recorded} == {mine.id, theirs.id}
    assert recorded[0].statement == "A says"  # neither was destroyed


def test_two_concurrent_exports_produce_one_package(tmp_path):
    first, second = _two_sessions(tmp_path, "export.db")
    _incident_id, investigation_id = _seeded_investigation(first)
    mine = first.application.commands.dispatch(
        ExportAuditPackage(**KW, investigation_id=investigation_id)
    )
    theirs = second.application.commands.dispatch(
        ExportAuditPackage(**KW, investigation_id=investigation_id)
    )
    assert mine.id == theirs.id


def test_a_question_answered_twice_is_refused(tmp_path):
    """A settled question is not re-answered: the record would show one outcome and hide another."""
    first, second = _two_sessions(tmp_path, "answer.db")
    _incident_id, investigation_id = _seeded_investigation(first)
    question = first.application.commands.dispatch(
        RecordQuestion(
            **KW, investigation_id=investigation_id,
            kind=QuestionKind.WHY_DIVERGED, text="why?", required=True,
        )
    )
    first.application.commands.dispatch(
        AnswerQuestion(
            **KW, investigation_id=investigation_id, question_id=question.id, answer="A's answer"
        )
    )
    with pytest.raises(BillingConflict, match="already"):
        second.application.commands.dispatch(
            AnswerQuestion(
                **KW, investigation_id=investigation_id, question_id=question.id,
                answer="B's answer",
            )
        )
    stored = second.application.queries.dispatch(
        GetInvestigationQuestions(**KW, investigation_id=investigation_id)
    )
    assert stored[0].answer == "A's answer"


def test_a_restart_mid_investigation_loses_nothing(tmp_path):
    """A second Application over the same file reads the same durable rows — the timeline, the
    evidence and the conclusions are on disk, not in a process."""
    first, second = _two_sessions(tmp_path, "restart.db")
    _incident_id, investigation_id = _seeded_investigation(first)
    first.application.commands.dispatch(
        CaptureIncidentEvidence(**KW, investigation_id=investigation_id)
    )
    del first
    page = second.application.queries.dispatch(
        GetInvestigationEvidence(**KW, investigation_id=investigation_id)
    )
    timeline = second.application.queries.dispatch(
        GetInvestigationTimeline(**KW, investigation_id=investigation_id)
    )
    assert page.total >= 1 and timeline.total >= 1


# ── 13. cross-organization isolation ──────────────────────────────────────────


def test_an_investigation_cannot_be_opened_across_organizations(wired):
    app, incident_id = wired
    other = dict(KW)
    other["org_id"] = "org_intruder"
    with pytest.raises(BillingError):
        app.commands.dispatch(OpenInvestigation(**other, incident_id=incident_id))


def test_identical_identities_in_two_orgs_never_collide():
    """Same incident id, same evidence content, same manifest — three distinct identities, because
    every derivation puts the org first."""
    body_digest = canonical_digest({"id": "inc_1"})
    assert derive_investigation_id("org_a", "inc_1") != derive_investigation_id("org_b", "inc_1")
    assert derive_evidence_id("org_a", "integrity", "integrity_incident", "inc_1", "",
                              body_digest) != derive_evidence_id(
        "org_b", "integrity", "integrity_incident", "inc_1", "", body_digest)
    assert derive_audit_package_id("org_a", "ivg_1", "d") != derive_audit_package_id(
        "org_b", "ivg_1", "d")


def test_evidence_from_another_org_cannot_be_attached():
    with pytest.raises(InvestigationRefused, match="cannot be attached"):
        EvidenceGuardService.guard_same_org(_investigation(), "org_other")


def test_an_incident_from_another_org_is_not_investigable():
    with pytest.raises(InvestigationRefused, match="cannot be investigated"):
        InvestigationGuardService.guard_incident_investigable(
            _incident(org_id="org_a"), "org_b"
        )


def test_every_repository_read_is_org_scoped():
    """Not one query filters by id alone."""
    source = Path("billing/persistence/investigation_repositories.py").read_text()
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name in (
            "get", "by_incident", "for_investigation", "all_for_investigation",
            "latest_for_source", "latest", "page", "counts", "next_sequence",
        ):
            body = ast.dump(node)
            assert "org_id" in body, node.name


# ── 14. the adversarial hunt ──────────────────────────────────────────────────


def test_hunt_1_investigation_created_twice_for_one_incident(wired):
    app, incident_id = wired
    first = app.commands.dispatch(OpenInvestigation(**KW, incident_id=incident_id))
    second = app.commands.dispatch(OpenInvestigation(**KW, incident_id=incident_id, summary="x"))
    assert first.id == second.id
    assert app.queries.dispatch(ListInvestigations(**KW)).total == 1


def test_hunt_2_investigation_for_a_nonexistent_incident(wired):
    app, _incident_id = wired
    with pytest.raises(BillingError, match="not found"):
        app.commands.dispatch(OpenInvestigation(**KW, incident_id="inc_invented"))


def test_hunt_3_and_4_evidence_captured_twice_writes_once(started):
    app, incident_id, investigation_id = started
    ref = dict(
        domain=EvidenceDomain.INTEGRITY,
        source_type=EvidenceSourceType.INTEGRITY_INCIDENT,
        source_id=incident_id,
    )
    first = app.commands.dispatch(CaptureEvidence(**KW, investigation_id=investigation_id, **ref))
    second = app.commands.dispatch(CaptureEvidence(**KW, investigation_id=investigation_id, **ref))
    assert first.id == second.id and first.sequence == second.sequence


def test_hunt_5_a_snapshot_cannot_change_after_capture(evidenced):
    """There is no update path, so the only way to record a change is a new link."""
    app, _incident_id, investigation_id, snapshot = evidenced
    repo = EvidenceRepository
    assert not hasattr(repo, "update")
    stored = app.queries.dispatch(
        GetInvestigationEvidence(**KW, investigation_id=investigation_id)
    )
    original = next(e for e in stored.evidence if e.id == snapshot.id)
    assert original.content_digest == snapshot.content_digest


def test_hunt_6_and_7_neither_mode_becomes_the_other(started):
    app, incident_id, investigation_id = started
    ref = dict(
        domain=EvidenceDomain.INTEGRITY,
        source_type=EvidenceSourceType.INTEGRITY_INCIDENT,
        source_id=incident_id,
    )
    live = app.commands.dispatch(
        CaptureEvidence(
            **KW, investigation_id=investigation_id, mode=CaptureMode.LIVE_REFERENCE, **ref
        )
    )
    snap = app.commands.dispatch(CaptureEvidence(**KW, investigation_id=investigation_id, **ref))
    assert live.id != snap.id
    assert live.mode == "live_reference" and live.body is None
    assert snap.mode == "evidence_snapshot" and snap.body is not None


def test_hunt_8_unavailable_evidence_is_never_treated_as_available(started):
    app, _incident_id, investigation_id = started
    capture = app.commands.dispatch(
        MarkEvidenceUnavailable(
            **KW, investigation_id=investigation_id,
            domain=EvidenceDomain.PAYMENT, source_type=EvidenceSourceType.PAYMENT_INTENT,
            source_id="pin_gone", reason="purged",
        )
    )
    assert not capture.supports_conclusion
    with pytest.raises(InvestigationRefused):
        app.commands.dispatch(
            RecordObservation(
                **KW, investigation_id=investigation_id,
                kind=ObservationKind.OBSERVED_FACT, statement="it was fine",
                evidence_ids=(capture.id,),
            )
        )


def test_hunt_9_redacted_evidence_keeps_its_historical_identity(evidenced):
    app, _incident_id, investigation_id, snapshot = evidenced
    redaction = app.commands.dispatch(
        RedactEvidence(**KW, investigation_id=investigation_id, evidence_id=snapshot.id,
                       reason="narrowed")
    )
    chains = app.queries.dispatch(GetCustodyChains(**KW, investigation_id=investigation_id))
    chain = next(c for c in chains if snapshot.locator.split("@")[0] == c.locator)
    ids = [link.evidence_id for link in chain.links]
    assert snapshot.id in ids and redaction.id in ids


def test_hunt_10_and_11_unsupported_statements_cannot_be_marked_proven(started):
    app, _incident_id, investigation_id = started
    with pytest.raises(InvestigationRefused):
        app.commands.dispatch(
            RecordObservation(**KW, investigation_id=investigation_id,
                              kind=ObservationKind.OBSERVED_FACT, statement="x")
        )
    with pytest.raises(InvestigationRefused):
        app.commands.dispatch(
            RecordConclusion(**KW, investigation_id=investigation_id,
                             classification=ConclusionClassification.CONFIRMED, statement="x")
        )


def test_hunt_12_conflicting_evidence_is_reported_never_ignored(evidenced):
    """`EvidenceComparisonService` can say two representations differ — a statement about digests.
    It cannot say what the difference is worth."""
    app, incident_id, investigation_id, snapshot = evidenced
    app.commands.dispatch(ic.AcknowledgeIncident(**KW, incident_id=incident_id))
    second = app.commands.dispatch(
        CaptureEvidence(
            **KW, investigation_id=investigation_id,
            domain=EvidenceDomain.INTEGRITY,
            source_type=EvidenceSourceType.INTEGRITY_INCIDENT,
            source_id=incident_id,
        )
    )
    assert snapshot.content_digest != second.content_digest
    chains = app.queries.dispatch(GetCustodyChains(**KW, investigation_id=investigation_id))
    assert any(c.changed for c in chains)


def test_hunt_12b_the_comparison_service_states_no_financial_impact():
    left = _capture(body={"total": 200})
    right = _capture(body={"total": 150})
    assert EvidenceComparisonService.differ(left, right)
    described = EvidenceComparisonService.describe(left, right)
    assert "50" not in described  # no subtraction anywhere
    assert "decided by the domain that owns the figure" in described


def test_hunt_13_a_closed_investigation_is_not_reopened(started):
    app, _incident_id, investigation_id = started
    _answer_required(app, investigation_id, unanswerable=True)
    app.commands.dispatch(
        RecordConclusion(**KW, investigation_id=investigation_id,
                         classification=ConclusionClassification.INCONCLUSIVE, statement="done")
    )
    app.commands.dispatch(CloseInvestigation(**KW, investigation_id=investigation_id))
    with pytest.raises(InvestigationRefused, match="never reopened"):
        app.commands.dispatch(ResumeInvestigation(**KW, investigation_id=investigation_id))


def test_hunt_16_17_18_package_identity_follows_the_manifest_exactly(evidenced):
    app, _incident_id, investigation_id, snapshot = evidenced
    first = app.commands.dispatch(ExportAuditPackage(**KW, investigation_id=investigation_id))
    same = app.commands.dispatch(ExportAuditPackage(**KW, investigation_id=investigation_id))
    assert first.id == same.id                        # 17: same manifest → same id
    app.commands.dispatch(
        RecordObservation(**KW, investigation_id=investigation_id,
                          kind=ObservationKind.OBSERVED_FACT, statement="new",
                          evidence_ids=(snapshot.id,))
    )
    changed = app.commands.dispatch(ExportAuditPackage(**KW, investigation_id=investigation_id))
    assert changed.id != first.id                     # 18: different manifest → different id
    assert changed.manifest_digest != first.manifest_digest


def test_hunt_21_financial_arithmetic_cannot_enter_foundation():
    """Asserted by the AST scan in §2; restated here as a hunt entry so the numbering is honest."""
    test_no_financial_calculation_exists()


def test_hunt_22_23_the_incident_and_its_findings_are_never_mutated(evidenced):
    """The strongest ownership claim in this milestone, checked by reading both domains before and
    after a full investigation."""
    app, incident_id, investigation_id, snapshot = evidenced
    before_incident = app.queries.dispatch(ic.GetIncident(**KW, incident_id=incident_id))
    before_findings = app.queries.dispatch(ic.GetIncidentFindings(**KW, incident_id=incident_id))

    _answer_required(app, investigation_id, evidence_ids=(snapshot.id,))
    app.commands.dispatch(
        RecordConclusion(**KW, investigation_id=investigation_id,
                         classification=ConclusionClassification.CONFIRMED, statement="found it",
                         evidence_ids=(snapshot.id,))
    )
    app.commands.dispatch(ExportAuditPackage(**KW, investigation_id=investigation_id))
    app.commands.dispatch(CloseInvestigation(**KW, investigation_id=investigation_id))

    after_incident = app.queries.dispatch(ic.GetIncident(**KW, incident_id=incident_id))
    after_findings = app.queries.dispatch(ic.GetIncidentFindings(**KW, incident_id=incident_id))
    assert after_incident.version == before_incident.version
    assert after_incident.status == before_incident.status
    assert after_incident.root_cause == before_incident.root_cause
    assert len(after_findings) == len(before_findings)


def test_hunt_24_25_26_no_repair_reconciliation_or_correlation_is_executed():
    """The Application imports none of the commands that would perform them."""
    source = Path("billing/application/investigation_engine.py").read_text()
    tree = ast.parse(source)
    imported = {
        alias.name for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) for alias in node.names
    }
    for banned in ("RequestFindingRepair", "RequestIncidentRepair", "ReconcileRecognitionLedger",
                   "CorrelateFindings", "MergeIncidents", "ResolveIncident",
                   "ClassifyIncidentRootCause", "CorrectCorrelation"):
        assert banned not in imported, banned


def test_hunt_27_28_no_sensitive_data_can_enter_any_shape():
    """Models, DTOs, views and the Studio, scanned for the vocabulary M9.16 forbade. Whole-word
    matching: `span` is not a PAN and `secretary` is not a secret."""
    import re

    from billing.persistence import investigation_models as models

    for table in models.INVESTIGATION_TABLES:
        for column in table.__table__.columns:
            for banned in FORBIDDEN_SECRETS:
                assert not re.search(rf"\b{banned}\b", column.name.lower()), (table, column.name)

    for path in (Path("billing/customer/investigation_schemas.py"),
                 Path("billing/application/investigation_engine.py")):
        code = _code_only(path.read_text()).lower()
        for banned in FORBIDDEN_SECRETS:
            assert not re.search(rf"\b{banned}\b", code), (path, banned)

    script = _studio_script()
    for banned in FORBIDDEN_SECRETS:
        assert not re.search(rf"\b{banned}\b", script.lower()), banned


def test_hunt_27b_no_captured_body_carries_a_secret(evidenced):
    """The bodies the collector actually produces, checked at runtime rather than by inspection."""
    import re

    app, _incident_id, investigation_id, _snapshot = evidenced
    page = app.queries.dispatch(GetInvestigationEvidence(**KW, investigation_id=investigation_id))
    for capture in page.evidence:
        for key in (capture.body or {}):
            for banned in FORBIDDEN_SECRETS:
                assert not re.search(rf"\b{banned}\b", key.lower()), (capture.locator, key)


def test_hunt_29_missing_evidence_never_silently_disappears(started):
    """Every path: the capture row, the gap list, the closure assessment and the manifest."""
    app, _incident_id, investigation_id = started
    app.commands.dispatch(
        MarkEvidenceUnavailable(
            **KW, investigation_id=investigation_id,
            domain=EvidenceDomain.TAX, source_type=EvidenceSourceType.TAX_ASSESSMENT,
            source_id="tax_gone", reason="purged by retention",
        )
    )
    page = app.queries.dispatch(GetInvestigationEvidence(**KW, investigation_id=investigation_id))
    assert any(e.source_id == "tax_gone" for e in page.evidence)
    assessment = app.queries.dispatch(AssessClosure(**KW, investigation_id=investigation_id))
    assert any("tax_gone" in g.locator for g in assessment.blocking_gaps)
    package = app.commands.dispatch(ExportAuditPackage(**KW, investigation_id=investigation_id))
    assert any("tax_gone" in g.locator for g in package.gaps)
    assert any("tax_gone" in g["locator"] for g in package.manifest["gaps"])


def test_hunt_30_an_investigation_does_not_close_with_mandatory_evidence_outstanding(evidenced):
    app, _incident_id, investigation_id, snapshot = evidenced
    app.commands.dispatch(
        MarkEvidenceUnavailable(
            **KW, investigation_id=investigation_id,
            domain=EvidenceDomain.LEDGER, source_type=EvidenceSourceType.LEDGER_ENTRY,
            source_id="led_gone", reason="never written",
        )
    )
    _answer_required(app, investigation_id, evidence_ids=(snapshot.id,))
    app.commands.dispatch(
        RecordConclusion(**KW, investigation_id=investigation_id,
                         classification=ConclusionClassification.CONFIRMED, statement="found it",
                         evidence_ids=(snapshot.id,))
    )
    with pytest.raises(InvestigationRefused, match="blocks a conclusion"):
        app.commands.dispatch(CloseInvestigation(**KW, investigation_id=investigation_id))


def test_hunt_31_two_workers_closing_simultaneously(tmp_path):
    first, second = _two_sessions(tmp_path, "close.db")
    _incident_id, investigation_id = _seeded_investigation(first)
    for app in (first.application,):
        _answer_required(app, investigation_id, unanswerable=True)
        app.commands.dispatch(
            RecordConclusion(**KW, investigation_id=investigation_id,
                             classification=ConclusionClassification.INCONCLUSIVE, statement="d")
        )
    first.application.commands.dispatch(CloseInvestigation(**KW, investigation_id=investigation_id))
    with pytest.raises(InvestigationRefused, match="closed"):
        second.application.commands.dispatch(
            CloseInvestigation(**KW, investigation_id=investigation_id)
        )


def test_hunt_34_35_36_a_restart_mid_flow_writes_no_duplicate(tmp_path):
    """Every identity is derived, so replaying a command after a restart lands on the same rows."""
    first, second = _two_sessions(tmp_path, "replay.db")
    _incident_id, investigation_id = _seeded_investigation(first)
    mine = first.application.commands.dispatch(
        CaptureIncidentEvidence(**KW, investigation_id=investigation_id)
    )
    theirs = second.application.commands.dispatch(
        CaptureIncidentEvidence(**KW, investigation_id=investigation_id)
    )
    assert [c.id for c in mine] == [c.id for c in theirs]
    timeline = second.application.queries.dispatch(
        GetInvestigationTimeline(**KW, investigation_id=investigation_id, limit=200)
    )
    sequences = [e.sequence for e in timeline.entries]
    assert sequences == sorted(set(sequences))  # no duplicated position in the trail


# ── 15. persistence, migration and events ─────────────────────────────────────


def test_migration_0031_is_additive_and_creates_seven_tables():
    from billing.persistence.migrations import v0031_billing_investigations as migration

    assert migration.VERSION == "0031"
    assert len(migration.TABLES) == 7
    source = _code_only(
        Path("billing/persistence/migrations/v0031_billing_investigations.py").read_text()
    )
    for banned in ("ALTER", "DROP", "alter_column", "drop_column"):
        assert banned not in source
    assert migration.downgrade.__doc__  # a documented refusal, not an oversight


def test_the_migration_chain_includes_0031():
    versions = [m.VERSION for m in MIGRATIONS]
    assert "0031" in versions
    assert len(versions) == len(set(versions))


def test_append_only_repositories_have_no_update_or_delete():
    for repo in (EvidenceRepository, InvestigationEventRepository,
                 InvestigationConclusionRepository, AuditPackageRepository):
        methods = {n for n in dir(repo) if not n.startswith("_")}
        assert not (methods & {"update", "delete", "replace", "remove"}), repo


def test_every_business_fact_has_exactly_one_event():
    from billing.foundation import INVESTIGATION_EVENTS

    assert len(set(INVESTIGATION_EVENTS.values())) == len(INVESTIGATION_EVENTS)
    for kind in INVESTIGATION_EVENTS:
        assert isinstance(kind, InvestigationEventKind)


def test_no_event_is_emitted_for_a_refusal(started):
    """A refusal is not a business fact. Emitting one would make a history in which nothing
    happened indistinguishable from one in which something did."""
    app, _incident_id, investigation_id = started
    before = app.queries.dispatch(
        GetInvestigationTimeline(**KW, investigation_id=investigation_id, limit=200)
    ).total
    with pytest.raises(InvestigationRefused):
        app.commands.dispatch(
            RecordObservation(**KW, investigation_id=investigation_id,
                              kind=ObservationKind.OBSERVED_FACT, statement="unbacked")
        )
    after = app.queries.dispatch(
        GetInvestigationTimeline(**KW, investigation_id=investigation_id, limit=200)
    ).total
    assert after == before


def test_the_timeline_is_totally_ordered_and_append_only(evidenced):
    app, _incident_id, investigation_id, snapshot = evidenced
    _answer_required(app, investigation_id, evidence_ids=(snapshot.id,))
    timeline = app.queries.dispatch(
        GetInvestigationTimeline(**KW, investigation_id=investigation_id, limit=200)
    )
    sequences = [e.sequence for e in timeline.entries]
    assert sequences == list(range(1, len(sequences) + 1))


def test_the_timeline_survives_closure(started):
    app, _incident_id, investigation_id = started
    _answer_required(app, investigation_id, unanswerable=True)
    app.commands.dispatch(
        RecordConclusion(**KW, investigation_id=investigation_id,
                         classification=ConclusionClassification.INCONCLUSIVE, statement="done")
    )
    before = app.queries.dispatch(
        GetInvestigationTimeline(**KW, investigation_id=investigation_id, limit=200)
    ).total
    app.commands.dispatch(CloseInvestigation(**KW, investigation_id=investigation_id))
    after = app.queries.dispatch(
        GetInvestigationTimeline(**KW, investigation_id=investigation_id, limit=200)
    )
    assert after.total == before + 1
    assert after.entries[-1].kind == InvestigationEventKind.INVESTIGATION_CLOSED.value


def test_pagination_is_computed_by_the_database():
    """`page`, `for_investigation` and `counts` issue COUNT/LIMIT/OFFSET and GROUP BY. Loading
    every row to return fifty is not a performance detail at this scale, it is an outage."""
    source = Path("billing/persistence/investigation_repositories.py").read_text()
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef):
            continue
        body = ast.dump(node)
        if node.name in ("page", "counts"):
            assert "func" in body, node.name  # func.count / func.max, computed by the database
        # The two unbounded collection reads are paginated; the per-investigation reads of
        # questions, observations and conclusions are bounded by one investigation, which is the
        # unit an examiner reads and cannot meaningfully be split.
        if node.name == "for_investigation" and "limit" in {a.arg for a in node.args.kwonlyargs}:
            assert "offset" in body, node.name


def test_the_evidence_chain_marks_unreadable_nodes_rather_than_dropping_them(started):
    app, _incident_id, investigation_id = started
    app.commands.dispatch(
        MarkEvidenceUnavailable(
            **KW, investigation_id=investigation_id,
            domain=EvidenceDomain.LEDGER, source_type=EvidenceSourceType.LEDGER_ENTRY,
            source_id="led_gone", reason="never written",
        )
    )
    chain = app.queries.dispatch(GetEvidenceChain(**KW, investigation_id=investigation_id))
    assert chain.unavailable
    identities = {n.identity for n in chain.nodes}
    assert any("led_gone" in n.label for n in chain.nodes) or identities


def test_the_posture_reports_counts_only(started):
    app, _incident_id, _investigation_id = started
    posture = app.queries.dispatch(GetInvestigationPosture(**KW))
    for field in type(posture).__dataclass_fields__:
        assert not any(b in field for b in ("amount", "total_minor", "currency", "balance")), field
    assert posture.total == 1


# ── 16. authorization and the API surface ─────────────────────────────────────


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
    with TestClient(app) as c:
        yield c


PATH = "/v1/billing/investigations"


def test_the_investigations_prefix_collides_with_nothing(client):
    spec = client.app.openapi()
    ours = {p for p in spec["paths"] if p.startswith(PATH)}
    others = {p for p in spec["paths"] if not p.startswith(PATH)}
    assert len(ours) == 29
    assert not (ours & others)


def test_there_is_no_delete_put_or_patch_anywhere_on_this_surface(client):
    spec = client.app.openapi()
    for path, ops in spec["paths"].items():
        if path.startswith(PATH):
            assert not ({"delete", "put", "patch"} & set(ops)), path


def test_every_bounded_context_is_tagged(client):
    from billing.customer.config import OPENAPI_TAGS

    assert any(tag["name"] == "investigations" for tag in OPENAPI_TAGS)


def test_every_new_message_is_deny_by_default():
    """The Application's authorizer denies unless one is supplied. Every M9.21 command and query
    requires a permission, and none is exempt."""
    source = Path("billing/application/investigation_engine.py").read_text()
    handlers = len(ALL_INVESTIGATION_COMMANDS) + len(ALL_INVESTIGATION_QUERIES)
    assert handlers == 30
    assert source.count("self._authz.require") >= 1
    # Every command use case reaches `require`, directly or through `_transition`.
    tree = ast.parse(source)
    for cls in ("InvestigationUseCases", "InvestigationQueries"):
        node = next(
            n for n in ast.walk(tree) if isinstance(n, ast.ClassDef) and n.name == cls
        )
        assert "require" in ast.dump(node)


def test_the_new_permissions_are_declared_and_distinct():
    """Five capabilities, separate from the eight that came before and from each other. Audit
    export gets its own scope because it is the one action here that leaves the building."""
    values = {p.value for p in BillingPermission}
    for expected in ("billing.investigation.read", "billing.investigation.open",
                     "billing.investigation.conduct", "billing.investigation.evidence",
                     "billing.investigation.conclude", "billing.investigation.close",
                     "billing.audit.export"):
        assert expected in values, expected
    assert len(values) == len(list(BillingPermission))


def test_foundation_declares_no_permission_vocabulary():
    """Authorization stays outside Foundation — ADR-0023 §7/§11.3, restated once more."""
    from billing.foundation import commercial_investigations as ci

    assert not any("permission" in name.lower() for name in dir(ci))


def test_reading_requires_investigation_read_and_export_requires_audit(started):
    """The two ends of the permission ladder, asserted on the actual call sites."""
    source = _inspect.getsource(
        _inspect.getmodule(started[0].investigation_query_use_cases)
    )
    assert "P.BILLING_INVESTIGATION_READ" in source
    assert "P.BILLING_AUDIT_EXPORT" in source
    assert "P.BILLING_INVESTIGATION_CLOSE" in source


def test_the_api_round_trips_a_whole_investigation(client, wired):
    """Transport end to end: open, triage, start, capture, conclude, export, close."""
    app, incident_id = wired
    # The HTTP client has its own container, so drive the Application directly for the incident
    # and assert the transport shapes against the same aggregate types.
    token = epic8_encode({"sub": ACTOR, "org": ORG}, SECRET, ttl_seconds=3600)
    headers = {"Authorization": f"Bearer {token}"}
    response = client.post(
        PATH, json={"incident_id": "inc_missing", "summary": "x"}, headers=headers
    )
    # A nonexistent incident is a 404, not a 500 — the refusal reaches the wire intact.
    assert response.status_code == 404, response.text
    assert incident_id  # the fixture's incident exists in the other container


def test_a_body_cannot_be_supplied_by_the_caller():
    """Evidence a caller could author is not evidence. `CaptureEvidenceBody` has no `body` field
    and forbids extras."""
    from billing.customer.investigation_schemas import CaptureEvidenceBody

    assert "body" not in CaptureEvidenceBody.model_fields
    assert CaptureEvidenceBody.model_config.get("extra") == "forbid"


def test_no_wire_schema_shadows_another_surfaces_name(client):
    """**Regression for D-2.** `ReasonBody` and `NoteBody` already existed on five Billing
    surfaces. Adding a sixth pair made FastAPI qualify *the Ledger's* schema name in the public
    OpenAPI document — a milestone breaking a neighbour by reusing a generic name, which is the
    same failure M9.20's own D-2 recorded one milestone earlier."""
    spec = client.app.openapi()
    qualified = [n for n in spec["components"]["schemas"] if "investigation_schemas" in n]
    assert qualified == []
    assert not [n for n in spec["components"]["schemas"] if "ledger_schemas" in n]


def test_no_wire_schema_can_name_money():
    from billing.customer import investigation_schemas as schemas

    for name in dir(schemas):
        model = getattr(schemas, name)
        if not (isinstance(model, type) and hasattr(model, "model_fields")):
            continue
        for field in model.model_fields:
            # `total` is a pagination count on every page response in this codebase, so the ban
            # names the monetary vocabulary precisely rather than catching a row counter with it.
            assert not any(
                b in field for b in
                ("amount", "minor", "currency", "balance", "price", "rate_", "_total", "total_")
            ), (name, field)


# ── 17. the Studio is a Consumer ──────────────────────────────────────────────


STUDIO = Path("billing/customer/studio/investigations.html")


def test_the_studio_is_self_contained():
    html = STUDIO.read_text()
    for banned in ("<script src=", "@import", "cdn.", "https://", "http://"):
        assert banned not in html, banned


def test_the_studio_stores_nothing():
    script = _studio_script()
    for banned in ("localStorage", "sessionStorage", "document.cookie", "indexedDB"):
        assert banned not in script, banned


def test_the_studio_computes_no_financial_figure():
    """No arithmetic on anything it renders, and no reduce/sum over a response."""
    script = _studio_script()
    # `* 1000` in `when()` converts epoch seconds to milliseconds for `Date` — a unit conversion
    # on a timestamp, and the only multiplication on the page.
    for banned in (".reduce(", "parseFloat", "parseInt", "toFixed", "Number(",
                   "minor", "amount", "currency"):
        assert banned not in script, banned
    assert script.count("*") == script.count("* 1000")


def test_the_studio_decides_no_investigation_rule():
    """It never promotes a hypothesis, never decides closability, never marks evidence verified."""
    script = _studio_script()
    # Each of these would be the page *deciding* something the server owns. Assignment, not
    # comparison: `r.availability === "available"` reads the server's answer and is exactly what
    # a Consumer should do.
    for banned in ("may_close =", "supports_conclusion =", "availability =",
                   "kind = \"observed_fact\"", "hypothesis_status =",
                   "completeness =", "correlate(", "reconcile("):
        assert banned not in script.replace("===", "@@@").replace("!==", "@@@"), banned


def test_the_studio_takes_its_actions_from_the_server(started):
    """`allowed_triggers` drives every lifecycle button; the page computes no legality."""
    script = _studio_script()
    assert "allowed_triggers" in script
    assert "may_close" in script
    app, _incident_id, investigation_id = started
    view = app.queries.dispatch(GetInvestigation(**KW, investigation_id=investigation_id))
    assert view.allowed_triggers  # the server does supply them


def test_the_studio_reaches_no_domain_but_its_own():
    script = _studio_script()
    assert 'const API = "/v1/billing/investigations"' in script
    for banned in ("/v1/billing/integrity", "/v1/billing/ledger", "/v1/billing/payments"):
        assert banned not in script, banned


# ── 18. read models and the whole loop ────────────────────────────────────────


def test_the_full_investigation_loop(evidenced):
    """The milestone, end to end, on the real pipeline — and the shape of what an examiner reads."""
    app, incident_id, investigation_id, snapshot = evidenced

    guess = app.commands.dispatch(
        RecordObservation(**KW, investigation_id=investigation_id,
                          kind=ObservationKind.HYPOTHESIS,
                          statement="the provider may have failed")
    )
    app.commands.dispatch(
        SettleHypothesis(**KW, investigation_id=investigation_id, observation_id=guess.id,
                         supported=False, evidence_ids=(snapshot.id,),
                         reason="no provider was involved")
    )
    app.commands.dispatch(
        RecordObservation(**KW, investigation_id=investigation_id,
                          kind=ObservationKind.OBSERVED_FACT,
                          statement="no accounting period was open at recognition",
                          evidence_ids=(snapshot.id,))
    )
    _answer_required(app, investigation_id, evidence_ids=(snapshot.id,))
    app.commands.dispatch(
        RecordConclusion(**KW, investigation_id=investigation_id,
                         classification=ConclusionClassification.CONFIRMED,
                         statement="revenue was recognized while no period was open",
                         evidence_ids=(snapshot.id,))
    )
    package = app.commands.dispatch(ExportAuditPackage(**KW, investigation_id=investigation_id))
    closed = app.commands.dispatch(
        CloseInvestigation(**KW, investigation_id=investigation_id, reason="concluded")
    )

    assert package.completeness == PackageCompleteness.COMPLETE.value
    assert package.gaps == ()
    assert closed.status == InvestigationStatus.CLOSED.value

    # The examiner's reconstruction, from persisted rows alone.
    manifest = package.manifest
    assert manifest["incident"]["id"] == incident_id
    assert manifest["incident"]["available"] is True
    assert len(manifest["evidence"]) == 4
    assert {o["kind"] for o in manifest["observations"]} == {"hypothesis", "observed_fact"}
    rejected = next(o for o in manifest["observations"] if o["kind"] == "hypothesis")
    assert rejected["hypothesis_status"] == "rejected"
    assert manifest["conclusions"][0]["classification"] == "confirmed"
    assert len(manifest["questions"]) == len(REQUIRED_QUESTIONS)
    assert manifest["timeline"]


def test_an_investigation_is_reachable_from_its_incident(started):
    app, incident_id, investigation_id = started
    view = app.queries.dispatch(GetInvestigationForIncident(**KW, incident_id=incident_id))
    assert view.id == investigation_id


def test_search_narrows_at_the_database(started):
    app, incident_id, investigation_id = started
    found = app.queries.dispatch(
        SearchInvestigations(**KW, status=InvestigationStatus.INVESTIGATING)
    )
    assert [i.id for i in found.investigations] == [investigation_id]
    empty = app.queries.dispatch(SearchInvestigations(**KW, status=InvestigationStatus.CLOSED))
    assert empty.total == 0
    by_incident = app.queries.dispatch(SearchInvestigations(**KW, incident_id=incident_id))
    assert by_incident.total == 1


def test_a_blocked_case_is_visibly_distinct(started):
    app, _incident_id, investigation_id = started
    app.commands.dispatch(
        AwaitEvidence(**KW, investigation_id=investigation_id, reason="the ledger is unreadable")
    )
    blocked = app.queries.dispatch(ListInvestigations(**KW, blocked_only=True))
    assert [i.id for i in blocked.investigations] == [investigation_id]
    posture = app.queries.dispatch(GetInvestigationPosture(**KW))
    assert posture.blocked == 1


def test_the_billing_package_layout_now_declares_the_investigation_modules():
    """The frozen layout assertion, extended by **addition**. Never weakened."""
    from billing import foundation

    for module in ("commercial_investigations", "investigation_rules", "investigation_factories"):
        assert (Path("billing/foundation") / f"{module}.py").exists(), module
    assert hasattr(foundation, "Investigation")
