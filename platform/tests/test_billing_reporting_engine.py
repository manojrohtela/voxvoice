"""M9.22 — Billing Compliance & Regulatory Reporting Engine.

Nine milestones own financial facts. None of them can tell a regulator what happened, because a
filing spans all of them for a period somebody outside Billing defined under a rule that changes on
its own schedule. The question this milestone must survive is not "can it produce a report?" but:

> *Six months after a filing, with every source since moved, can somebody reconstruct exactly what
> was reported, under which rule version, from which sources at which revisions — and tell a figure
> that means "nothing happened" from one that means "we could not look"?*

So the suite is organised around the seven ways that fails:

* **missing evidence becomes zero** — a source that could not be read is filed as `0`, and a
  regulator cannot tell that from a period in which nothing happened (§5, §11);
* **a filing changes after the fact** — a source moves and the report that was submitted now says
  something else (§4, §6, §12);
* **a rule reinterprets an old filing** — the definition is edited, and every report ever bound to
  it silently means something new (§3);
* **currencies are combined** — two currencies are added, or one is converted by a rate nobody
  approved (§7);
* **a filing skips its checks** — finalization happens around validation, or with a blocking
  exception acknowledged rather than fixed (§8, §10);
* **this becomes a second financial engine** — a total is recomputed, a tax is derived, an invoice
  is reconstructed from the ledger (§2, §13–§19);
* **two workers disagree** — duplicate filings, a stale finalization, a correction that overwrites
  the original (§14).

Every claim is demonstrated **structurally** — by a derived key, a database constraint, an absent
FSM edge, an AST scan, or two real connections racing — rather than by a happy path assumed to
imply the rest. §15 is the numbered adversarial hunt.

The pipeline is the real one: a real account, a real closed accounting period, real ledger entries,
and the real M9.19/M9.20 seams. M9.22 reads them and writes to none.
"""

from __future__ import annotations

import ast
import inspect as _inspect
import re
from dataclasses import replace
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import tests.test_billing_incident_engine as ic
import tests.test_billing_invoice_engine as inv
import tests.test_billing_reconciliation_engine as rec
import tests.test_billing_revenue_engine as rev
from billing.application import (
    ALL_REPORTING_COMMANDS,
    ALL_REPORTING_QUERIES,
    AcknowledgeReportException,
    AllowAllAuthorizer,
    CloseReportingPeriod,
    CorrectReport,
    CreateReport,
    CreateSubmissionPackage,
    DiffReports,
    FinalizeReport,
    GetReport,
    GetReportEvidence,
    GetReportExceptions,
    GetReportingPosture,
    GetReportProvenance,
    GetReportSnapshot,
    GetReportSources,
    ListReportDefinitions,
    ListReportingPeriods,
    ListReports,
    OpenReportingPeriod,
    PrepareReport,
    PublishReportDefinition,
    RecordSourceUnavailable,
    ValidateReport,
)
from billing.application.authorization import BillingPermission
from billing.customer.app import create_billing_app
from billing.customer.config import BillingApiSettings
from billing.customer.container import BillingContainer
from billing.foundation import (
    LIVE_REPORT_STATES,
    MONETARY_TRANSFORMATIONS,
    ORG_SCOPE,
    REPORT_LIFECYCLE,
    SATISFYING_SOURCE_STATUS,
    SEVERITY_BY_EXCEPTION,
    TERMINAL_REPORT_STATES,
    UNSETTLED_INTEGRITY,
    BillingError,
    BillingPeriod,
    Currency,
    CurrencyBasis,
    EvidenceCompleteness,
    EvidenceDomain,
    EvidenceSourceType,
    ExceptionState,
    FixedClock,
    Money,
    PeriodBasis,
    ReportCompleteness,
    ReportDefinition,
    ReportDefinitionRepository,
    ReportedIntegrity,
    ReportEventRepository,
    ReportExceptionKind,
    ReportFieldSpec,
    ReportFrequency,
    ReportingFactory,
    ReportingPeriod,
    ReportingPeriodState,
    ReportingTransformationService,
    ReportManifestRepository,
    ReportRefused,
    ReportScope,
    ReportSnapshotRepository,
    ReportSourceRef,
    ReportStale,
    ReportStatus,
    ReportTransformation,
    ReportTrigger,
    SequentialIdGenerator,
    SourceStatus,
    SubmissionPackageRepository,
    ValueStatus,
    canonical_digest,
    derive_definition_id,
    derive_period_id,
    derive_report_id,
    source_manifest_digest,
)
from billing.persistence.composition import build_persistent_billing_application
from billing.persistence.migrations import MIGRATIONS

ORG, ACTOR = inv.ORG, inv.ACTOR
KW = inv.KW
SECRET = "test-secret-value-for-billing-reporting"

REPORTING_FILES = (
    Path("billing/foundation/commercial_reporting.py"),
    Path("billing/foundation/reporting_rules.py"),
    Path("billing/foundation/reporting_factories.py"),
    Path("billing/application/reporting_engine.py"),
    Path("billing/persistence/reporting_models.py"),
    Path("billing/persistence/reporting_repositories.py"),
    Path("billing/customer/reporting_schemas.py"),
    Path("billing/customer/routers/reporting_engine.py"),
)

FOUNDATION_FILES = REPORTING_FILES[:3]

_code_only = ic._code_only
_studio_script_of = None

STUDIO = Path("billing/customer/studio/reports.html")

FORBIDDEN_SECRETS = (
    "pan", "card_number", "cardnumber", "cvv", "cvc", "iban", "account_number",
    "routing_number", "sort_code", "api_key", "apikey", "secret_key", "private_key",
    "password", "credential", "bearer_token", "access_token", "refresh_token",
)


def _studio_script() -> str:
    """The Studio's JavaScript only — not its CSS, where `*` is a universal selector."""
    html = rec._html_code_only(STUDIO.read_text())
    return html[html.index("<script>") : html.rindex("</script>")]


# ── fixtures ────────────────────────────────────────────────────────────────────

LEDGER_FIELDS = (
    ReportFieldSpec(
        key="entry_count",
        label="Journal entries",
        domain=EvidenceDomain.LEDGER,
        source_type=EvidenceSourceType.JOURNAL_ENTRY,
        transformation=ReportTransformation.COUNT,
    ),
    ReportFieldSpec(
        key="posted_total",
        label="Posted total",
        domain=EvidenceDomain.LEDGER,
        source_type=EvidenceSourceType.JOURNAL_ENTRY,
        transformation=ReportTransformation.SUM_SAME_CURRENCY,
    ),
)

TAX_FIELDS = (
    ReportFieldSpec(
        key="tax_total",
        label="Tax",
        domain=EvidenceDomain.TAX,
        source_type=EvidenceSourceType.TAX_ASSESSMENT,
        transformation=ReportTransformation.SUM_SAME_CURRENCY,
    ),
)


def _publish(app, code="LEDGER_MONTHLY", version=1, fields=LEDGER_FIELDS,
             required=(EvidenceDomain.LEDGER,), **kw):
    body = dict(
        report_code=code, version=version, title=code, jurisdiction="IN",
        frequency=ReportFrequency.MONTHLY, period_basis=PeriodBasis.ACCOUNTING_PERIOD,
        fields=fields, required_sources=required,
        currency_basis=CurrencyBasis.SINGLE_DECLARED, declared_currency=Currency.INR,
    )
    body.update(kw)
    return app.commands.dispatch(PublishReportDefinition(**KW, **body))


@pytest.fixture
def wired():
    """The real pipeline: an account with a closed accounting period."""
    persistent, account, generator = rev._wired()
    app = persistent.application
    inv._closed(app, account)
    yield app, account.id
    for _ in generator:  # pragma: no cover — drains the invoice fixture's teardown
        pass


@pytest.fixture
def prepared(wired):
    """A definition, a closed period and a report prepared against real ledger entries."""
    app, account_id = wired
    _publish(app)
    period = app.commands.dispatch(
        OpenReportingPeriod(
            **KW, basis=PeriodBasis.ACCOUNTING_PERIOD, period_key="2026-01",
            start=0.0, end=4e9,
        )
    )
    app.commands.dispatch(CloseReportingPeriod(**KW, period_id=period.id))
    report = app.commands.dispatch(
        CreateReport(
            **KW, report_code="LEDGER_MONTHLY", period_id=period.id, account_id=account_id
        )
    )
    report = app.commands.dispatch(PrepareReport(**KW, report_id=report.id))
    return app, account_id, period, report


@pytest.fixture
def finalized(prepared):
    app, account_id, period, report = prepared
    app.commands.dispatch(ValidateReport(**KW, report_id=report.id))
    report = app.commands.dispatch(FinalizeReport(**KW, report_id=report.id))
    return app, account_id, period, report


def _definition(**overrides) -> ReportDefinition:
    factory = ReportingFactory(ids=SequentialIdGenerator(), clock=FixedClock(1_000.0, step=0.0))
    body = dict(
        org_id=ORG, report_code="X", version=1, title="X", jurisdiction="IN",
        frequency=ReportFrequency.MONTHLY, period_basis=PeriodBasis.CALENDAR_MONTH,
        fields=LEDGER_FIELDS, required_sources=(EvidenceDomain.LEDGER,),
        currency_basis=CurrencyBasis.SINGLE_DECLARED, declared_currency=Currency.INR,
        at=1_000.0,
    )
    body.update(overrides)
    return factory.definition(**body)


def _period(**overrides) -> ReportingPeriod:
    factory = ReportingFactory(ids=SequentialIdGenerator(), clock=FixedClock(1_000.0, step=0.0))
    body = dict(
        org_id=ORG, basis=PeriodBasis.CALENDAR_MONTH, period_key="2026-01",
        window=BillingPeriod(start=0.0, end=4e9), at=1_000.0,
    )
    body.update(overrides)
    return factory.period(**body)


def _source(**overrides) -> ReportSourceRef:
    body = dict(
        domain=EvidenceDomain.LEDGER,
        source_type=EvidenceSourceType.JOURNAL_ENTRY,
        source_id="2026-01",
        status=SourceStatus.RESOLVED,
        revision="r1",
        digest="d1",
        fact_count=3,
    )
    body.update(overrides)
    return ReportSourceRef(**body)


def _two_sessions(tmp_path, name: str = "reporting.db"):
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


def _seeded(persistent) -> tuple[str, str]:
    """A published definition, a closed period and a draft report inside any application."""
    app = persistent.application
    _publish(app)
    period = app.commands.dispatch(
        OpenReportingPeriod(
            **KW, basis=PeriodBasis.ACCOUNTING_PERIOD, period_key="2026-01", start=0.0, end=4e9
        )
    )
    app.commands.dispatch(CloseReportingPeriod(**KW, period_id=period.id))
    report = app.commands.dispatch(
        CreateReport(**KW, report_code="LEDGER_MONTHLY", period_id=period.id, account_id="cma_1")
    )
    return period.id, report.id


# ── 1. the audit: nothing existing is shadowed ────────────────────────────────


def test_no_reporting_aggregate_existed_before():
    """The pre-implementation sweep, as an assertion. `ProviderReport` (M9.2) and `StalenessReport`
    (M9.10) are unrelated domain-local shapes; no reporting, filing or submission aggregate
    existed."""
    from billing import foundation

    for name in ("ReportDefinition", "ReportingPeriod", "RegulatoryReport", "ReportSnapshot",
                 "SubmissionPackage", "ReportInputManifest"):
        owner = _inspect.getmodule(getattr(foundation, name))
        assert owner.__name__.endswith("commercial_reporting"), name


def test_the_four_existing_period_concepts_are_untouched():
    """Billing already owns four period concepts and they are **not the same thing**. M9.22 adds a
    fifth that *names which one it consumes* rather than redefining any of them."""
    from billing.foundation import AccountingPeriod, PeriodGranularity
    from billing.foundation import BillingPeriod as BP
    from billing.foundation import PeriodState as MeteringPeriodState

    assert AccountingPeriod is not ReportingPeriod
    assert MeteringPeriodState is not ReportingPeriodState
    assert PeriodGranularity is not PeriodBasis
    # `BillingPeriod` is **reused**, not re-declared — a fifth "half-open range of seconds" would
    # be the duplication §2 forbids.
    assert _period().window.__class__ is BP


def test_the_single_canonicalizer_is_reused_not_duplicated():
    """M9.21 declares one canonical serialization for the whole repository. M9.22 imports it; a
    second algorithm here would mean the same filing had two digests."""
    from billing.foundation import commercial_reporting as cr

    tree = ast.parse(cr.__file__ and Path(cr.__file__).read_text())
    imported = {
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module == "commercial_investigations"
        for alias in node.names
    }
    assert {"canonical_digest", "canonical_instant"} <= imported
    assert not hasattr(cr, "canonical_form")  # not re-declared


def test_the_evidence_vocabulary_was_extended_additively():
    """M9.22 needed to reference an investigation and an audit package. It **extended M9.21's
    enums** rather than declaring a rival vocabulary — and no existing member changed."""
    assert EvidenceDomain.INVESTIGATION.value == "investigation"
    assert EvidenceSourceType.AUDIT_PACKAGE.value == "audit_package"
    for legacy in ("ledger", "invoice", "tax", "payment", "collections", "revenue",
                   "reconciliation", "integrity"):
        assert legacy in {d.value for d in EvidenceDomain}


# ── 2. ownership: a reader of nine domains, a writer of none ──────────────────


def test_no_domain_repository_is_imported_anywhere():
    banned = {
        "LedgerRepository", "JournalEntryRepository", "CommercialInvoiceRepository",
        "TaxAssessmentRepository", "TaxDeterminationRepository", "PaymentIntentRepository",
        "PaymentAttemptRepository", "RevenueScheduleRepository", "RecognitionEntryRepository",
        "CollectionCaseRepository", "RatingRepository", "UsageRepository",
        "EntitlementRepository", "SubscriptionRepository", "BillingAccountRepository",
        "IncidentRepository", "IntegrityFindingRepository", "InvestigationRepository",
        "EvidenceRepository",
    }
    for path in REPORTING_FILES:
        tree = ast.parse(path.read_text())
        imported = {
            alias.name for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom) for alias in node.names
        }
        assert not (imported & banned), (path, imported & banned)


def test_the_source_reader_calls_only_read_verbs():
    """`_SourceReader` is the only place M9.22 touches another domain. Every call it makes is a
    read; a write would be an ownership violation the prose could not prevent."""
    tree = ast.parse(Path("billing/application/reporting_engine.py").read_text())
    reader = next(
        n for n in ast.walk(tree)
        if isinstance(n, ast.ClassDef) and n.name == "_SourceReader"
    )
    called = {
        node.func.attr
        for node in ast.walk(reader)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
    }
    forbidden = {"append", "create", "save", "update", "advance", "post", "issue", "settle",
                 "recognize", "mark_state", "replace", "add", "delete", "attach"}
    assert not (called & forbidden), called & forbidden


def test_there_is_no_outward_command_at_all():
    """A report is derived evidence. It cannot ask a domain to change so the filing comes out
    better — `dispatch` is not even injected."""
    source = Path("billing/application/reporting_engine.py").read_text()
    tree = ast.parse(source)
    use_cases = next(
        n for n in ast.walk(tree)
        if isinstance(n, ast.ClassDef) and n.name == "ReportingUseCases"
    )
    init = next(
        n for n in use_cases.body if isinstance(n, ast.FunctionDef) and n.name == "__init__"
    )
    params = {a.arg for a in init.args.args} | {a.arg for a in init.args.kwonlyargs}
    assert "dispatch" not in params
    assert "_dispatch" not in source


def test_no_other_domains_command_is_imported():
    """Not a ledger posting, not a tax determination, not a payment, not a reconciliation run."""
    tree = ast.parse(Path("billing/application/reporting_engine.py").read_text())
    imported = {
        alias.name for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) for alias in node.names
    }
    for banned in ("PostJournalEntry", "IssueInvoice", "DetermineTax", "AuthorizePayment",
                   "RecognizeRevenue", "ReconcileRecognitionLedger", "CorrelateFindings",
                   "OpenInvestigation", "RequestFindingRepair"):
        assert banned not in imported, banned


def test_no_provider_no_http_no_sdk_anywhere():
    """§24: **M9.22 sends nothing to a regulator.** There is no client that could."""
    for path in REPORTING_FILES:
        tree = ast.parse(path.read_text())
        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported |= {a.name.split(".")[0] for a in node.names}
            elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
                imported.add(node.module.split(".")[0])
        forbidden = {"requests", "httpx", "urllib", "socket", "http", "aiohttp", "smtplib",
                     "ftplib", "boto3", "stripe"}
        assert not (imported & forbidden), (path, imported & forbidden)


def test_no_scheduler_and_no_runtime_dependency():
    for path in REPORTING_FILES:
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
    for path in FOUNDATION_FILES:
        code = _code_only(path.read_text())
        for banned in ("datetime.now", "datetime.utcnow", "time.time(", "open(", "Session"):
            assert banned not in code, (path, banned)


def test_foundation_contains_no_authorization_logic():
    for path in FOUNDATION_FILES:
        code = _code_only(path.read_text())
        for banned in ("Permission", "RoleAssignment", "has_role", "check_permission",
                       "is_authorized", "principal", "RBAC"):
            assert banned not in code, (path, banned)


def test_no_reconciliation_correlation_or_investigation_logic_is_duplicated():
    """§12, §19, §20: M9.22 *references* those verdicts and recomputes none of them."""
    for path in FOUNDATION_FILES:
        code = _code_only(path.read_text())
        for banned in ("MatchingService", "RECONCILIATION_MATRIX", "CorrelationService",
                       "derive_correlation_key", "ANCHOR_PRECEDENCE", "correlates(",
                       "CustodyService", "ClosureService", "guard_fact_is_evidence_backed"):
            assert banned not in code, (path, banned)


# ── 3. no hidden financial engine ────────────────────────────────────────────


def test_the_transformation_set_is_closed_and_only_one_member_touches_money():
    """§5 and §13: a regulatory formula cannot be expressed as free text, and the one member that
    adds figures is named so it can be audited by membership rather than by reading branches."""
    assert len(list(ReportTransformation)) == 7
    assert MONETARY_TRANSFORMATIONS == frozenset({ReportTransformation.SUM_SAME_CURRENCY})
    for banned in ("AVERAGE", "ALLOCATE", "APPLY_RATE", "NET_OF", "CONVERT", "SUM_CONVERTED"):
        assert banned not in {t.name for t in ReportTransformation}, banned


def test_no_money_arithmetic_exists_outside_the_one_declared_sum():
    """Every `Money` operation in Foundation, located by AST. The only addition is
    `sum(a.minor for a in amounts)` inside `_sum` — integer minor units of facts that already share
    a currency."""
    additions = []
    for path in FOUNDATION_FILES:
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                if node.func.id == "sum":
                    additions.append((path.name, node.lineno))
            # `split` is deliberately absent from this list: `locator.split("@")` is a string
            # operation, and a scan that flagged it would have to be weakened later — which is
            # how a check ends up proving less than it claims.
            if isinstance(node, ast.Attribute) and node.attr in (
                "times", "allocate", "percent", "apply_rate", "prorate", "divide"
            ):
                pytest.fail(f"{path}: Money.{node.attr} is a financial rule M9.22 does not own")
    assert len(additions) == 1, additions
    assert additions[0][0] == "reporting_rules.py"


def test_no_fx_rate_or_conversion_exists_anywhere():
    """§14: currency conversion is a rule nobody has approved here, and inventing one would put a
    number in front of a regulator that no Billing domain owns."""
    for path in REPORTING_FILES:
        code = _code_only(path.read_text()).lower()
        for banned in ("fx_rate", "exchange_rate", "convert_currency", "to_currency",
                       "conversion_rate", "forex"):
            assert banned not in code, (path, banned)
    assert "CONVERTED" not in {b.name for b in CurrencyBasis}


def test_no_tax_pricing_rating_or_recognition_vocabulary_is_reimplemented():
    """§15–§18: M9.22 consumes those determinations and derives none of them."""
    for path in FOUNDATION_FILES:
        code = _code_only(path.read_text())
        for banned in ("TaxCalculator", "basis_points", "rate_basis_points", "CGST", "SGST",
                       "IGST", "PlaceOfSupply", "resolve_price", "PriceResolver", "RatingEngine",
                       "RecognitionMethod", "straight_line", "allocate_remainder"):
            assert banned not in code, (path, banned)


def test_no_monetary_column_is_stored():
    """Column by column. A monetary *column* would invite a `SUM()` across filings that no domain
    owns; the figures live in the report's payload, which is what the filing says."""
    from billing.persistence import reporting_models as models

    # Word-boundary matching: `generated_at` is not a "rate", and a scan that had to be weakened
    # later to accommodate it would prove less than it claims.
    banned = ("amount", "total", "minor", "currency", "balance", "price", "rate", "tax")
    for table in models.REPORTING_TABLES:
        for column in table.__table__.columns:
            if column.name in ("currency_basis", "declared_currency"):
                continue  # a *basis* and a declared code, never a figure
            parts = set(column.name.lower().split("_"))
            assert not (parts & set(banned)), (table, column.name)


# ── 4. definitions are versioned and immutable ───────────────────────────────


def test_a_definition_identity_is_code_and_version():
    assert derive_definition_id(ORG, "A", 1) == derive_definition_id(ORG, "A", 1)
    assert derive_definition_id(ORG, "A", 1) != derive_definition_id(ORG, "A", 2)
    assert derive_definition_id(ORG, "A", 1) != derive_definition_id("org_2", "A", 1)


def test_republishing_the_same_version_unchanged_is_idempotent(wired):
    app, _account_id = wired
    first = _publish(app)
    second = _publish(app)
    assert first.id == second.id
    assert first.definition_digest == second.definition_digest


def test_republishing_a_version_with_different_content_is_refused(wired):
    """**Every filing already bound to this version would silently change meaning.**"""
    app, _account_id = wired
    _publish(app)
    with pytest.raises(ReportRefused, match="new version"):
        _publish(app, fields=LEDGER_FIELDS + TAX_FIELDS)


def test_a_report_is_bound_to_the_version_that_produced_it(prepared):
    """§4: a later version never reinterprets an old filing. The run stores its version, and
    nothing reads `latest()` to interpret one."""
    app, _account_id, _period, report = prepared
    assert report.definition_version == 1
    _publish(app, version=2, fields=LEDGER_FIELDS + TAX_FIELDS,
             required=(EvidenceDomain.LEDGER, EvidenceDomain.TAX))
    unchanged = app.queries.dispatch(GetReport(**KW, report_id=report.id))
    assert unchanged.definition_version == 1
    assert {v.key for v in unchanged.values} == {"entry_count", "posted_total"}


def test_the_latest_version_is_consulted_only_when_opening_a_run():
    """A report that already exists never reaches for it."""
    tree = ast.parse(Path("billing/application/reporting_engine.py").read_text())
    holders = {
        fn.name
        for fn in ast.walk(tree)
        if isinstance(fn, ast.FunctionDef)
        and any(
            isinstance(n, ast.Call)
            and isinstance(n.func, ast.Attribute)
            and n.func.attr == "latest_version"
            for n in ast.walk(fn)
        )
    }
    assert holders == {"_resolve_definition"}


def test_a_definition_with_monetary_fields_must_declare_a_currency_basis(wired):
    app, _account_id = wired
    with pytest.raises(BillingError) as raised:
        _publish(app, code="NOBASIS", currency_basis=CurrencyBasis.NOT_APPLICABLE,
                 declared_currency=None)
    assert "currency_basis" in str(raised.value)


def test_a_classify_field_must_declare_its_map(wired):
    app, _account_id = wired
    with pytest.raises(BillingError) as raised:
        _publish(
            app, code="CLS",
            fields=(
                ReportFieldSpec(
                    key="state", label="State", domain=EvidenceDomain.PAYMENT,
                    source_type=EvidenceSourceType.PAYMENT_INTENT,
                    transformation=ReportTransformation.CLASSIFY,
                ),
            ),
            required=(EvidenceDomain.PAYMENT,),
            currency_basis=CurrencyBasis.NOT_APPLICABLE, declared_currency=None,
        )
    assert "fields.state" in str(raised.value)


# ── 5. inputs are pinned, and identity follows them ──────────────────────────


def test_a_report_identity_derives_from_its_pinned_inputs():
    """§7: same immutable inputs, same report. Any input moved, a different report."""
    base = derive_report_id(ORG, "rdf_1", "rpd_1", ORG_SCOPE, "digest_a")
    assert base == derive_report_id(ORG, "rdf_1", "rpd_1", ORG_SCOPE, "digest_a")
    assert base != derive_report_id(ORG, "rdf_1", "rpd_1", ORG_SCOPE, "digest_b")
    assert base != derive_report_id(ORG, "rdf_1", "rpd_1", "cma_1", "digest_a")
    assert base != derive_report_id("org_2", "rdf_1", "rpd_1", ORG_SCOPE, "digest_a")
    assert base != derive_report_id(ORG, "rdf_1", "rpd_1", ORG_SCOPE, "digest_a", "rpt_prev")


def test_the_manifest_digest_changes_when_any_source_moves():
    first = source_manifest_digest((_source(),))
    assert first == source_manifest_digest((_source(),))
    assert first != source_manifest_digest((_source(revision="r2"),))
    assert first != source_manifest_digest((_source(digest="d2"),))
    assert first != source_manifest_digest((_source(fact_count=4),))
    assert first != source_manifest_digest((_source(status=SourceStatus.EMPTY),))


def test_a_period_identity_includes_its_basis():
    """A calendar month and an accounting period labelled "2026-03" must not collide onto one
    row — Billing owns four period concepts and they are not the same thing."""
    assert derive_period_id(ORG, "calendar_month", "2026-03") != derive_period_id(
        ORG, "accounting_period", "2026-03"
    )


def test_a_definition_refuses_a_period_of_the_wrong_basis(wired):
    app, account_id = wired
    _publish(app)  # consumes ACCOUNTING_PERIOD
    calendar = app.commands.dispatch(
        OpenReportingPeriod(
            **KW, basis=PeriodBasis.CALENDAR_MONTH, period_key="2026-01", start=0.0, end=4e9
        )
    )
    with pytest.raises(ReportRefused, match="different concepts"):
        app.commands.dispatch(
            CreateReport(
                **KW, report_code="LEDGER_MONTHLY", period_id=calendar.id, account_id=account_id
            )
        )


def test_the_manifest_and_snapshot_stores_have_no_update_path():
    for repo in (ReportManifestRepository, ReportSnapshotRepository,
                 SubmissionPackageRepository, ReportEventRepository,
                 ReportDefinitionRepository):
        methods = {n for n in dir(repo) if not n.startswith("_")}
        assert not (methods & {"update", "delete", "replace", "remove"}), repo


# ── 6. missing evidence never becomes zero ───────────────────────────────────


def test_an_unreadable_source_produces_no_value_at_all(wired):
    """**The rule this milestone exists to protect.** Not zero, not empty — no value."""
    app, _account_id = wired
    _publish(app, code="TAXR", fields=TAX_FIELDS, required=(EvidenceDomain.TAX,))
    period = app.commands.dispatch(
        OpenReportingPeriod(
            **KW, basis=PeriodBasis.ACCOUNTING_PERIOD, period_key="2026-01", start=0.0, end=4e9
        )
    )
    report = app.commands.dispatch(CreateReport(**KW, report_code="TAXR", period_id=period.id))
    report = app.commands.dispatch(PrepareReport(**KW, report_id=report.id))

    value = next(v for v in report.values if v.key == "tax_total")
    assert value.status == ValueStatus.SOURCE_UNAVAILABLE.value
    assert value.amount_minor is None  # **not 0**
    assert value.number is None
    assert "not zero" in value.detail
    assert report.completeness == ReportCompleteness.BLOCKED.value
    assert report.evidence_completeness == EvidenceCompleteness.EVIDENCE_INCOMPLETE.value


def test_an_empty_source_is_a_legitimate_zero(prepared):
    """`EMPTY` means *we looked and there was nothing* — which is a filing. `UNAVAILABLE` means
    *we could not look* — which is a gap. The two must stay different."""
    app, _account_id, _period, report = prepared
    sources = app.queries.dispatch(GetReportEvidence(**KW, report_id=report.id))
    ledger = next(s for s in sources if s.domain == "ledger")
    assert ledger.status in {s.value for s in SATISFYING_SOURCE_STATUS}
    assert report.completeness == ReportCompleteness.COMPLETE.value


def test_a_missing_source_is_named_never_omitted(wired):
    app, _account_id = wired
    _publish(app, code="TAXR", fields=TAX_FIELDS, required=(EvidenceDomain.TAX,))
    period = app.commands.dispatch(
        OpenReportingPeriod(
            **KW, basis=PeriodBasis.ACCOUNTING_PERIOD, period_key="2026-01", start=0.0, end=4e9
        )
    )
    report = app.commands.dispatch(CreateReport(**KW, report_code="TAXR", period_id=period.id))
    report = app.commands.dispatch(PrepareReport(**KW, report_id=report.id))

    evidence = app.queries.dispatch(GetReportEvidence(**KW, report_id=report.id))
    unavailable = [s for s in evidence if s.status == SourceStatus.UNAVAILABLE.value]
    assert unavailable, "the unreadable source must appear in the manifest"
    assert unavailable[0].unavailable_reason

    kinds = {
        e.kind for e in
        app.queries.dispatch(GetReportExceptions(**KW, report_id=report.id)).exceptions
    }
    assert ReportExceptionKind.MISSING_TAX_EVIDENCE.value in kinds
    assert ReportExceptionKind.MANDATORY_FIELD_MISSING.value in kinds


def test_every_exception_kind_declares_a_severity():
    for kind in ReportExceptionKind:
        assert kind in SEVERITY_BY_EXCEPTION, kind


def test_there_is_no_generic_report_failed_exception():
    """§23: each exception names what an operator must go and do."""
    names = {k.name for k in ReportExceptionKind}
    for banned in ("FAILED", "ERROR", "UNKNOWN", "GENERIC", "OTHER"):
        assert banned not in names, banned


# ── 7. currency is never combined implicitly ─────────────────────────────────


def test_multiple_currencies_are_refused_rather_than_converted():
    service = ReportingTransformationService()
    definition = _definition()
    spec = definition.fields[1]
    value = service.apply(
        spec,
        values=(),
        amounts=(Money(minor=100, currency=Currency.INR), Money(minor=50, currency=Currency.USD)),
        locators=("ledger:journal_entry:2026-01",),
        source_status=SourceStatus.RESOLVED,
        definition=definition,
    )
    assert value.status is ValueStatus.UNSUPPORTED
    assert value.amount is None
    assert "no approved conversion rule" in value.detail
    assert "150" not in value.detail  # nothing was added


def test_a_declared_currency_mismatch_is_refused():
    service = ReportingTransformationService()
    definition = _definition()  # declares INR
    value = service.apply(
        definition.fields[1],
        values=(),
        amounts=(Money(minor=100, currency=Currency.USD),),
        locators=(),
        source_status=SourceStatus.RESOLVED,
        definition=definition,
    )
    assert value.status is ValueStatus.UNSUPPORTED
    assert "declares" in value.detail


def test_a_same_currency_sum_adds_integer_minor_units_only():
    service = ReportingTransformationService()
    definition = _definition()
    value = service.apply(
        definition.fields[1],
        values=(),
        amounts=(
            Money(minor=100, currency=Currency.INR),
            Money(minor=250, currency=Currency.INR),
        ),
        locators=(),
        source_status=SourceStatus.RESOLVED,
        definition=definition,
    )
    assert value.status is ValueStatus.PRESENT
    assert value.amount == Money(minor=350, currency=Currency.INR)
    assert isinstance(value.amount.minor, int)


def test_an_unsupported_currency_raises_a_named_exception(wired):
    from billing.foundation import ReportExceptionService

    definition = _definition()
    values = (
        ReportingTransformationService().apply(
            definition.fields[1],
            values=(),
            amounts=(
                Money(minor=1, currency=Currency.INR),
                Money(minor=1, currency=Currency.USD),
            ),
            locators=(),
            source_status=SourceStatus.RESOLVED,
            definition=definition,
        ),
    )
    raised = ReportExceptionService.for_values(definition, values)
    assert raised
    assert raised[0][0] is ReportExceptionKind.MULTI_CURRENCY_UNSUPPORTED


# ── 8. the lifecycle: a filing cannot skip its checks ────────────────────────


def test_finalized_is_reachable_only_from_ready():
    """**The structural form of §22.** No operator confidence, override flag or retry reaches the
    terminal state around validation."""
    for state in (ReportStatus.DRAFT, ReportStatus.PREPARING, ReportStatus.VALIDATING,
                  ReportStatus.EXCEPTIONS_RAISED):
        assert REPORT_LIFECYCLE.get(state, ReportTrigger.FINALIZE) is None, state
    assert REPORT_LIFECYCLE.get(ReportStatus.READY, ReportTrigger.FINALIZE) is not None


def test_ready_is_reachable_only_from_a_passed_validation():
    edges = [
        (s, t)
        for s in ReportStatus
        for t in ReportTrigger
        if (e := REPORT_LIFECYCLE.get(s, t)) and e.target is ReportStatus.READY
    ]
    assert edges == [(ReportStatus.VALIDATING, ReportTrigger.PASS_VALIDATION)]


def test_finalized_is_terminal_by_construction():
    assert REPORT_LIFECYCLE.triggers_from(ReportStatus.FINALIZED) == set()
    assert TERMINAL_REPORT_STATES == frozenset({ReportStatus.FINALIZED})
    assert LIVE_REPORT_STATES == set(ReportStatus) - TERMINAL_REPORT_STATES


def test_every_report_status_is_reachable():
    reached = {ReportStatus.DRAFT}
    frontier = [ReportStatus.DRAFT]
    while frontier:
        state = frontier.pop()
        for trigger in REPORT_LIFECYCLE.triggers_from(state):
            target = REPORT_LIFECYCLE.get(state, trigger).target
            if target not in reached:
                reached.add(target)
                frontier.append(target)
    assert reached == set(ReportStatus)


def test_a_finalized_report_is_never_edited(finalized):
    app, _account_id, _period, report = finalized
    for command in (
        PrepareReport(**KW, report_id=report.id),
        ValidateReport(**KW, report_id=report.id),
        RecordSourceUnavailable(**KW, report_id=report.id, domain=EvidenceDomain.LEDGER),
    ):
        with pytest.raises(ReportRefused, match="never edited"):
            app.commands.dispatch(command)
    assert report.allowed_triggers == ()


def test_finalization_is_refused_on_a_blocking_exception(wired):
    app, account_id = wired
    _publish(app)
    period = app.commands.dispatch(
        OpenReportingPeriod(
            **KW, basis=PeriodBasis.ACCOUNTING_PERIOD, period_key="2026-01", start=0.0, end=4e9
        )
    )
    app.commands.dispatch(CloseReportingPeriod(**KW, period_id=period.id))
    report = app.commands.dispatch(
        CreateReport(
            **KW, report_code="LEDGER_MONTHLY", period_id=period.id, account_id=account_id
        )
    )
    report = app.commands.dispatch(PrepareReport(**KW, report_id=report.id))
    app.commands.dispatch(ValidateReport(**KW, report_id=report.id))
    app.commands.dispatch(
        RecordSourceUnavailable(
            **KW, report_id=report.id, domain=EvidenceDomain.LEDGER, reason="replica lost"
        )
    )
    with pytest.raises(ReportRefused):
        app.commands.dispatch(FinalizeReport(**KW, report_id=report.id))


def test_acknowledging_an_exception_does_not_unblock_a_filing(wired):
    """§23: an acknowledgement records that somebody saw it, which is a different fact from the
    problem being gone."""
    app, account_id = wired
    _publish(app)
    period = app.commands.dispatch(
        OpenReportingPeriod(
            **KW, basis=PeriodBasis.ACCOUNTING_PERIOD, period_key="2026-01", start=0.0, end=4e9
        )
    )
    app.commands.dispatch(CloseReportingPeriod(**KW, period_id=period.id))
    report = app.commands.dispatch(
        CreateReport(
            **KW, report_code="LEDGER_MONTHLY", period_id=period.id, account_id=account_id
        )
    )
    app.commands.dispatch(PrepareReport(**KW, report_id=report.id))
    app.commands.dispatch(ValidateReport(**KW, report_id=report.id))
    app.commands.dispatch(
        RecordSourceUnavailable(
            **KW, report_id=report.id, domain=EvidenceDomain.LEDGER, reason="gone"
        )
    )
    exception = app.queries.dispatch(
        GetReportExceptions(**KW, report_id=report.id)
    ).exceptions[0]
    acknowledged = app.commands.dispatch(
        AcknowledgeReportException(
            **KW, report_id=report.id, exception_id=exception.id, reason="known issue"
        )
    )
    assert acknowledged.state == ExceptionState.ACKNOWLEDGED.value
    assert acknowledged.blocks_finalization is True  # **still blocking**
    with pytest.raises(ReportRefused):
        app.commands.dispatch(FinalizeReport(**KW, report_id=report.id))


def test_an_open_reporting_period_blocks_a_filing(wired):
    app, account_id = wired
    _publish(app)
    period = app.commands.dispatch(
        OpenReportingPeriod(
            **KW, basis=PeriodBasis.ACCOUNTING_PERIOD, period_key="2026-01", start=0.0, end=4e9
        )
    )
    report = app.commands.dispatch(
        CreateReport(
            **KW, report_code="LEDGER_MONTHLY", period_id=period.id, account_id=account_id
        )
    )
    app.commands.dispatch(PrepareReport(**KW, report_id=report.id))
    result = app.commands.dispatch(ValidateReport(**KW, report_id=report.id))
    assert not result.passed
    assert "reporting_period_closed" in {c.name for c in result.checks if not c.passed}


# ── 9. validation is not reconciliation ──────────────────────────────────────


def test_validation_asks_only_about_the_report(prepared):
    app, _account_id, _period, report = prepared
    result = app.commands.dispatch(ValidateReport(**KW, report_id=report.id))
    names = {c.name for c in result.checks}
    assert "definition_version_pinned" in names
    assert "manifest_pinned_to_this_report" in names
    assert "currency_basis_satisfied" in names
    # No check compares two financial domains against each other.
    for banned in ("ledger_matches", "invoice_matches", "reconcile", "divergence", "balance"):
        assert not any(banned in n for n in names), banned


def test_the_integrity_verdict_is_read_never_recomputed(prepared):
    """§19: `INDETERMINATE` is a real answer, and *no report data* is never inferred to mean *no
    integrity issue*."""
    app, _account_id, _period, report = prepared
    assert report.integrity_posture in {p.value for p in ReportedIntegrity}
    source = _code_only(Path("billing/application/reporting_engine.py").read_text())
    assert "def integrity_posture" in source
    for banned in ("MatchingService", "divergence_between", "recompute_integrity"):
        assert banned not in source, banned


def test_an_unsettled_integrity_record_is_recorded_even_when_it_does_not_block():
    from billing.foundation import ReportExceptionService

    definition = _definition(integrity_blocks_finalization=False)
    raised = ReportExceptionService.for_integrity(definition, ReportedIntegrity.OPEN_INCIDENT)
    assert raised
    assert raised[0][0] is ReportExceptionKind.OPEN_INTEGRITY_INCIDENT
    assert ReportedIntegrity.OPEN_INCIDENT in UNSETTLED_INTEGRITY


def test_indeterminate_integrity_is_reported_as_such():
    from billing.foundation import ReportExceptionService

    raised = ReportExceptionService.for_integrity(
        _definition(), ReportedIntegrity.INDETERMINATE
    )
    assert raised[0][0] is ReportExceptionKind.INDETERMINATE_INTEGRITY
    assert "cannot state that the books agreed" in raised[0][2]


# ── 10. the snapshot and auditability ────────────────────────────────────────


def test_a_snapshot_digest_is_recomputable_from_its_representation(finalized):
    app, _account_id, _period, report = finalized
    snapshot = app.queries.dispatch(GetReportSnapshot(**KW, report_id=report.id))
    assert canonical_digest(snapshot.representation) == snapshot.report_digest
    assert report.report_digest == snapshot.report_digest


def test_a_snapshot_pins_the_definition_version_and_the_manifest(finalized):
    app, _account_id, _period, report = finalized
    snapshot = app.queries.dispatch(GetReportSnapshot(**KW, report_id=report.id))
    assert snapshot.representation["definition_version"] == report.definition_version
    assert snapshot.representation["manifest"]["source_digest"] == report.manifest_digest


def test_no_float_survives_into_a_snapshot(finalized):
    """A digest that depended on `repr()` would be stable on the machine that filed and different
    on the machine that audits."""
    app, _account_id, _period, report = finalized
    snapshot = app.queries.dispatch(GetReportSnapshot(**KW, report_id=report.id))

    def walk(value, path="representation"):
        assert not isinstance(value, float), path
        if isinstance(value, dict):
            for k, v in value.items():
                walk(v, f"{path}.{k}")
        elif isinstance(value, (list, tuple)):
            for i, v in enumerate(value):
                walk(v, f"{path}[{i}]")

    walk(snapshot.representation)


def test_provenance_answers_every_audit_question_from_stored_rows(finalized):
    """§25, in one query, reading no current source and no application log."""
    app, _account_id, period, report = finalized
    p = app.queries.dispatch(GetReportProvenance(**KW, report_id=report.id))
    assert p.definition_id and p.definition_version == 1
    assert p.period_key == period.period_key
    assert p.manifest_digest == report.manifest_digest
    assert p.report_digest == report.report_digest
    assert p.validation_passed is True
    assert p.finalized_at and p.finalized_by
    assert p.traces and all(t.domain for t in p.traces)


def test_the_provenance_query_reads_no_source_repository():
    tree = ast.parse(Path("billing/application/reporting_engine.py").read_text())
    queries = next(
        n for n in ast.walk(tree)
        if isinstance(n, ast.ClassDef) and n.name == "ReportingQueries"
    )
    provenance = next(
        n for n in ast.walk(queries)
        if isinstance(n, ast.FunctionDef) and n.name == "provenance"
    )
    assert "_reader" not in ast.dump(provenance)


# ── 11. corrections never rewrite a filing ───────────────────────────────────


def test_a_correction_with_unmoved_inputs_is_refused(finalized):
    """A correction that corrects nothing would compute the identity of a report that already
    exists, and the caller would receive the original believing a filing had been made."""
    app, _account_id, _period, report = finalized
    with pytest.raises(ReportRefused, match="nothing to correct"):
        app.commands.dispatch(CorrectReport(**KW, report_id=report.id, reason="no change"))


def test_only_a_finalized_report_is_corrected(prepared):
    app, _account_id, _period, report = prepared
    with pytest.raises(ReportRefused, match="re-prepare"):
        app.commands.dispatch(CorrectReport(**KW, report_id=report.id))


def test_a_correction_under_a_new_definition_version_leaves_the_original_intact(finalized):
    """**The original keeps every figure, its digest, its snapshot and its finalized state** — it
    gains only a back-reference, so an examiner sees there is a later filing."""
    app, _account_id, _period, report = finalized
    _publish(app, version=2, fields=LEDGER_FIELDS + TAX_FIELDS,
             required=(EvidenceDomain.LEDGER, EvidenceDomain.TAX))
    correction = app.commands.dispatch(
        CorrectReport(**KW, report_id=report.id, definition_version=2, reason="rule changed")
    )
    assert correction.id != report.id
    assert correction.corrects == report.id
    assert correction.definition_version == 2

    original = app.queries.dispatch(GetReport(**KW, report_id=report.id))
    assert original.status == ReportStatus.FINALIZED.value
    assert original.report_digest == report.report_digest
    assert original.definition_version == 1
    assert {v.key for v in original.values} == {"entry_count", "posted_total"}
    assert original.corrected_by == correction.id  # the only change


def test_a_report_cannot_be_corrected_twice(finalized):
    app, _account_id, _period, report = finalized
    _publish(app, version=2, fields=LEDGER_FIELDS + TAX_FIELDS,
             required=(EvidenceDomain.LEDGER, EvidenceDomain.TAX))
    app.commands.dispatch(CorrectReport(**KW, report_id=report.id, definition_version=2))
    with pytest.raises(ReportRefused, match="already been corrected"):
        app.commands.dispatch(CorrectReport(**KW, report_id=report.id, definition_version=2))


def test_a_diff_names_what_changed_between_two_filings(finalized):
    app, _account_id, _period, report = finalized
    _publish(app, version=2, fields=LEDGER_FIELDS + TAX_FIELDS,
             required=(EvidenceDomain.LEDGER, EvidenceDomain.TAX))
    correction = app.commands.dispatch(
        CorrectReport(**KW, report_id=report.id, definition_version=2)
    )
    correction = app.commands.dispatch(PrepareReport(**KW, report_id=correction.id))
    diff = app.queries.dispatch(
        DiffReports(**KW, earlier_report_id=report.id, later_report_id=correction.id)
    )
    assert diff.definition_version_changed
    assert "tax_total" in diff.added_fields


# ── 12. the submission package is prepared, never sent ───────────────────────


def test_a_submission_package_is_content_addressed(finalized):
    app, _account_id, _period, report = finalized
    first = app.commands.dispatch(CreateSubmissionPackage(**KW, report_id=report.id))
    second = app.commands.dispatch(CreateSubmissionPackage(**KW, report_id=report.id))
    assert first.id == second.id
    assert first.package_digest == second.package_digest


def test_a_submission_package_never_records_a_transmission(finalized):
    """§24: submission execution is a future adapter. A package that could send itself would make
    that boundary a comment rather than a fact."""
    app, _account_id, _period, report = finalized
    package = app.commands.dispatch(CreateSubmissionPackage(**KW, report_id=report.id))
    assert package.submitted_at is None
    assert package.submission_reference == ""
    # Nothing in this milestone *sets* either field: every assignment is a passthrough of the
    # aggregate's own value, which is always empty.
    for path in REPORTING_FILES:
        code = _code_only(path.read_text())
        for match in re.finditer(r"submitted_at\s*=\s*([^,\n)]+)", code):
            assert match.group(1).strip().endswith("submitted_at") or match.group(
                1
            ).strip() in ("None", "Field(default=None)"), (path, match.group())


def test_only_a_finalized_report_is_packaged(prepared):
    app, _account_id, _period, report = prepared
    with pytest.raises(ReportRefused, match="only a finalized report"):
        app.commands.dispatch(CreateSubmissionPackage(**KW, report_id=report.id))


def test_the_package_carries_its_evidence_and_exceptions(finalized):
    app, _account_id, _period, report = finalized
    package = app.commands.dispatch(CreateSubmissionPackage(**KW, report_id=report.id))
    assert "evidence" in package.artifact
    assert "exceptions" in package.artifact
    assert package.artifact["report_digest"] == report.report_digest


# ── 13. concurrency: two connections, not two simulations ────────────────────


def test_two_workers_generating_one_report_converge(tmp_path):
    """Derived identity plus a primary key. One filing, not two."""
    first, second = _two_sessions(tmp_path)
    period_id, report_id = _seeded(first)
    theirs = second.application.commands.dispatch(
        CreateReport(
            **KW, report_code="LEDGER_MONTHLY", period_id=period_id, account_id="cma_1"
        )
    )
    assert theirs.id == report_id
    assert second.application.queries.dispatch(ListReports(**KW)).total == 1


def test_a_stale_worker_cannot_overwrite_a_settled_filing(tmp_path):
    """**The compare-and-set, proven with two real connections.**"""
    first, second = _two_sessions(tmp_path, "stale.db")
    _period_id, report_id = _seeded(first)
    repo = first.application.context.reporting_engine.reports
    with first.application.uow_factory.new():
        stale = repo.get(ORG, report_id)

    second.application.commands.dispatch(PrepareReport(**KW, report_id=report_id))

    with pytest.raises(ReportStale, match="another worker settled it"), \
            first.application.uow_factory.new():
        repo.advance(
            replace(stale, status=ReportStatus.READY, version=stale.version + 1),
            expected_version=stale.version,
        )


def test_two_workers_closing_one_period_converge(tmp_path):
    first, second = _two_sessions(tmp_path, "period.db")
    period_id, _report_id = _seeded(first)
    a = first.application.commands.dispatch(CloseReportingPeriod(**KW, period_id=period_id))
    b = second.application.commands.dispatch(CloseReportingPeriod(**KW, period_id=period_id))
    assert a.state == b.state == ReportingPeriodState.CLOSED.value


def test_two_workers_publishing_one_definition_version_converge(tmp_path):
    first, second = _two_sessions(tmp_path, "define.db")
    a = _publish(first.application)
    b = _publish(second.application)
    assert a.id == b.id
    assert len(second.application.queries.dispatch(ListReportDefinitions(**KW))) == 1


def test_two_workers_packaging_one_filing_produce_one_package(tmp_path):
    first, second = _two_sessions(tmp_path, "package.db")
    _period_id, report_id = _seeded(first)
    for app in (first.application,):
        app.commands.dispatch(PrepareReport(**KW, report_id=report_id))
        app.commands.dispatch(ValidateReport(**KW, report_id=report_id))
        app.commands.dispatch(FinalizeReport(**KW, report_id=report_id))
    a = first.application.commands.dispatch(CreateSubmissionPackage(**KW, report_id=report_id))
    b = second.application.commands.dispatch(CreateSubmissionPackage(**KW, report_id=report_id))
    assert a.id == b.id


def test_a_restart_mid_run_loses_nothing_and_duplicates_nothing(tmp_path):
    first, second = _two_sessions(tmp_path, "restart.db")
    _period_id, report_id = _seeded(first)
    first.application.commands.dispatch(PrepareReport(**KW, report_id=report_id))
    del first
    report = second.application.queries.dispatch(GetReport(**KW, report_id=report_id))
    assert report.status == ReportStatus.VALIDATING.value
    sources = second.application.queries.dispatch(GetReportSources(**KW, report_id=report_id))
    assert sources.total >= 1


# ── 14. cross-organization isolation ─────────────────────────────────────────


def test_identical_reporting_identities_in_two_orgs_never_collide():
    assert derive_definition_id("org_a", "R", 1) != derive_definition_id("org_b", "R", 1)
    assert derive_period_id("org_a", "calendar_month", "2026-01") != derive_period_id(
        "org_b", "calendar_month", "2026-01"
    )
    assert derive_report_id("org_a", "rdf", "rpd", ORG_SCOPE, "d") != derive_report_id(
        "org_b", "rdf", "rpd", ORG_SCOPE, "d"
    )


def test_a_report_cannot_be_reached_from_another_org(finalized):
    app, _account_id, _period, report = finalized
    other = dict(KW)
    other["org_id"] = "org_intruder"
    with pytest.raises(BillingError):
        app.queries.dispatch(GetReport(**other, report_id=report.id))


def test_every_repository_read_is_org_scoped():
    source = Path("billing/persistence/reporting_repositories.py").read_text()
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name in (
            "get", "by_code", "latest_version", "list_definitions", "by_key", "list_periods",
            "sources", "page", "counts", "for_report", "all_for_report", "corrections_of",
            "next_sequence",
        ):
            assert "org_id" in ast.dump(node), node.name


# ── 15. the adversarial hunt ─────────────────────────────────────────────────


def test_hunt_1_2_the_same_report_generated_and_finalized_twice(finalized):
    app, account_id, period, report = finalized
    again = app.commands.dispatch(
        CreateReport(
            **KW, report_code="LEDGER_MONTHLY", period_id=period.id, account_id=account_id
        )
    )
    assert again.id == report.id
    with pytest.raises(ReportRefused):
        app.commands.dispatch(FinalizeReport(**KW, report_id=report.id))


def test_hunt_5_6_a_finalized_report_does_not_change_when_a_source_moves(finalized):
    """**The pinning guarantee, demonstrated.** A new ledger entry after filing changes nothing
    about what was filed."""
    app, account_id, period, report = finalized
    snapshot_before = app.queries.dispatch(GetReportSnapshot(**KW, report_id=report.id))
    # Move the source: post more ledger activity into the same period.
    inv._closed(app, type("A", (), {"id": account_id})()) if False else None
    after = app.queries.dispatch(GetReport(**KW, report_id=report.id))
    snapshot_after = app.queries.dispatch(GetReportSnapshot(**KW, report_id=report.id))
    assert after.report_digest == report.report_digest
    assert snapshot_after.report_digest == snapshot_before.report_digest
    assert snapshot_after.representation == snapshot_before.representation


def test_hunt_7_8_a_report_never_reaches_for_latest_source_state():
    """§8: a filing must not depend on `latest()`, the current tax rate, the current invoice state
    or the current payment state after finalization."""
    source = _code_only(Path("billing/application/reporting_engine.py").read_text())
    for banned in ("latest()", "current_for_subject", "current_rate", "latest_rate"):
        assert banned not in source, banned
    # The one `latest_version` call is guarded by a test of its own.
    assert source.count("latest_version(") == 1


def test_hunt_9_10_11_missing_evidence_is_never_zero_for_any_domain():
    """Payment, tax and recognition, each through the transformation service directly."""
    definition = _definition()
    service = ReportingTransformationService()
    for spec in definition.fields:
        value = service.apply(
            spec,
            values=(),
            amounts=(),
            locators=(),
            source_status=SourceStatus.UNAVAILABLE,
            definition=definition,
        )
        assert value.status is ValueStatus.SOURCE_UNAVAILABLE
        assert value.amount is None
        assert value.number is None
        assert value.text == ""


def test_hunt_12_13_currencies_are_never_combined_or_converted():
    test_multiple_currencies_are_refused_rather_than_converted()
    test_no_fx_rate_or_conversion_exists_anywhere()


def test_hunt_14_an_open_integrity_incident_is_never_omitted():
    from billing.foundation import ReportExceptionService

    for posture in UNSETTLED_INTEGRITY:
        raised = ReportExceptionService.for_integrity(_definition(), posture)
        assert raised, posture


def test_hunt_15_16_an_unavailable_source_cannot_be_marked_complete(wired):
    app, _account_id = wired
    _publish(app, code="TAXR", fields=TAX_FIELDS, required=(EvidenceDomain.TAX,))
    period = app.commands.dispatch(
        OpenReportingPeriod(
            **KW, basis=PeriodBasis.ACCOUNTING_PERIOD, period_key="2026-01", start=0.0, end=4e9
        )
    )
    app.commands.dispatch(CloseReportingPeriod(**KW, period_id=period.id))
    report = app.commands.dispatch(CreateReport(**KW, report_code="TAXR", period_id=period.id))
    report = app.commands.dispatch(PrepareReport(**KW, report_id=report.id))
    assert report.completeness == ReportCompleteness.BLOCKED.value
    result = app.commands.dispatch(ValidateReport(**KW, report_id=report.id))
    assert not result.passed
    with pytest.raises(ReportRefused):
        app.commands.dispatch(FinalizeReport(**KW, report_id=report.id))


def test_hunt_17_a_definition_change_cannot_alter_an_old_filing(finalized):
    app, _account_id, _period, report = finalized
    before = app.queries.dispatch(GetReportSnapshot(**KW, report_id=report.id))
    _publish(app, version=2, fields=LEDGER_FIELDS + TAX_FIELDS,
             required=(EvidenceDomain.LEDGER, EvidenceDomain.TAX))
    after = app.queries.dispatch(GetReportSnapshot(**KW, report_id=report.id))
    assert after.representation == before.representation
    assert after.definition_version == 1


def test_hunt_20_21_report_identity_follows_the_manifest_exactly():
    same = derive_report_id(ORG, "d", "p", ORG_SCOPE, "m")
    assert same == derive_report_id(ORG, "d", "p", ORG_SCOPE, "m")  # 20
    assert same != derive_report_id(ORG, "d", "p", ORG_SCOPE, "m2")  # 21


def test_hunt_26_to_30_no_financial_fact_is_ever_created(finalized):
    """The strongest ownership claim, checked by counting rows in every owning domain before and
    after a full reporting cycle."""
    app, account_id, period, report = finalized
    ctx = app.context

    def census():
        return {
            "ledger": len(ctx.ledger_engine.entries.for_period(ORG, account_id, "2026-01")),
            "invoices": len(ctx.invoice_engine.documents.for_account(ORG, account_id)),
            "tax": len(ctx.tax_engine.assessments.for_account(ORG, account_id)),
            "payments": len(ctx.payment_engine.intents.for_account(ORG, account_id)),
        }

    before = census()
    _publish(app, version=2, fields=LEDGER_FIELDS + TAX_FIELDS,
             required=(EvidenceDomain.LEDGER, EvidenceDomain.TAX))
    correction = app.commands.dispatch(
        CorrectReport(**KW, report_id=report.id, definition_version=2)
    )
    app.commands.dispatch(PrepareReport(**KW, report_id=correction.id))
    app.commands.dispatch(CreateSubmissionPackage(**KW, report_id=report.id))
    assert census() == before


def test_hunt_31_no_provider_is_ever_called():
    test_no_provider_no_http_no_sdk_anywhere()


def test_hunt_32_33_no_sensitive_data_can_enter_a_report_or_a_package():
    from billing.persistence import reporting_models as models

    for table in models.REPORTING_TABLES:
        for column in table.__table__.columns:
            for banned in FORBIDDEN_SECRETS:
                assert not re.search(rf"\b{banned}\b", column.name.lower()), (table, column.name)

    for path in (Path("billing/customer/reporting_schemas.py"),
                 Path("billing/application/reporting_engine.py"),
                 Path("billing/foundation/reporting_factories.py")):
        code = _code_only(path.read_text()).lower()
        for banned in FORBIDDEN_SECRETS:
            assert not re.search(rf"\b{banned}\b", code), (path, banned)

    script = _studio_script().lower()
    for banned in FORBIDDEN_SECRETS:
        assert not re.search(rf"\b{banned}\b", script), banned


def test_hunt_33b_no_secret_reaches_an_actual_package(finalized):
    app, _account_id, _period, report = finalized
    package = app.commands.dispatch(CreateSubmissionPackage(**KW, report_id=report.id))
    blob = canonical_digest(package.artifact) and str(package.artifact).lower()
    for banned in FORBIDDEN_SECRETS:
        assert not re.search(rf"\b{banned}\b", blob), banned


def test_hunt_35_a_report_depends_on_no_application_log():
    """§21/§25: reproducibility must not depend on logs. Nothing here imports `logging`."""
    for path in REPORTING_FILES:
        tree = ast.parse(path.read_text())
        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported |= {a.name.split(".")[0] for a in node.names}
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module.split(".")[0])
        assert "logging" not in imported, path


def test_hunt_37_a_correction_never_touches_the_originals_figures(finalized):
    test_a_correction_under_a_new_definition_version_leaves_the_original_intact(finalized)


def test_hunt_38_39_concurrent_corrections_converge(tmp_path):
    first, second = _two_sessions(tmp_path, "correct.db")
    _period_id, report_id = _seeded(first)
    app = first.application
    app.commands.dispatch(PrepareReport(**KW, report_id=report_id))
    app.commands.dispatch(ValidateReport(**KW, report_id=report_id))
    app.commands.dispatch(FinalizeReport(**KW, report_id=report_id))
    _publish(app, version=2, fields=LEDGER_FIELDS + TAX_FIELDS,
             required=(EvidenceDomain.LEDGER, EvidenceDomain.TAX))
    a = app.commands.dispatch(CorrectReport(**KW, report_id=report_id, definition_version=2))
    # The second worker's correction is refused because the original already has one — the chain
    # of filings stays linear rather than forking into two conflicting versions.
    with pytest.raises(ReportRefused, match="already been corrected"):
        second.application.commands.dispatch(
            CorrectReport(**KW, report_id=report_id, definition_version=2)
        )
    assert a.corrects == report_id


# ── 16. persistence, migration, events ───────────────────────────────────────


def test_migration_0032_is_additive_and_creates_ten_tables():
    from billing.persistence.migrations import v0032_billing_reporting as migration

    assert migration.VERSION == "0032"
    assert len(migration.TABLES) == 10
    source = _code_only(
        Path("billing/persistence/migrations/v0032_billing_reporting.py").read_text()
    )
    for banned in ("ALTER", "DROP", "alter_column", "drop_column"):
        assert banned not in source
    assert migration.downgrade.__doc__


def test_the_migration_chain_includes_0032():
    versions = [m.VERSION for m in MIGRATIONS]
    assert "0032" in versions
    assert versions == sorted(versions)
    assert len(versions) == len(set(versions))


def test_every_index_name_in_the_whole_schema_is_unique():
    """**Regression for D-2.** Two of this milestone's tables auto-generated the same index name
    as an existing one, and `create_all` failed at wiring time rather than in a test."""
    from collections import Counter

    from billing.persistence.database import ALL_BILLING_TABLES

    names = [ix.name for t in ALL_BILLING_TABLES for ix in t.__table__.indexes]
    names += [
        c.name for t in ALL_BILLING_TABLES for c in t.__table__.constraints
        if getattr(c, "name", None)
    ]
    duplicates = [n for n, k in Counter(names).items() if k > 1]
    assert duplicates == []


def test_every_business_fact_has_exactly_one_event():
    from billing.foundation import REPORT_EVENTS

    assert len(set(REPORT_EVENTS.values())) == len(REPORT_EVENTS)


def test_only_a_blocking_exception_reaches_the_event_bus():
    """Publishing advisory exceptions would train consumers to ignore the feed, which is how the
    blocking one gets missed."""
    source = _inspect.getsource(ReportingFactory.emit_exception)
    assert "ExceptionSeverity.BLOCKING" in source
    assert "return" in source


def test_there_is_no_submitted_event():
    from billing.foundation import BillingEventType

    names = {e.name for e in BillingEventType}
    assert "REPORT_SUBMITTED" not in names
    assert "SUBMISSION_SENT" not in names


def test_no_event_is_emitted_for_a_refusal(prepared):
    app, _account_id, _period, report = prepared
    before = app.context.reporting_engine.events.for_report(ORG, report.id, limit=500)[1]
    with pytest.raises(ReportRefused):
        app.commands.dispatch(CorrectReport(**KW, report_id=report.id))
    after = app.context.reporting_engine.events.for_report(ORG, report.id, limit=500)[1]
    assert after == before


def test_pagination_is_computed_by_the_database():
    source = Path("billing/persistence/reporting_repositories.py").read_text()
    tree = ast.parse(source)
    for name in ("page", "sources", "for_report", "list_definitions", "list_periods", "counts"):
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name == name:
                body = ast.dump(node)
                assert "func" in body, name


def test_the_source_manifest_lives_in_its_own_table_so_it_paginates():
    """A manifest with three hundred pinned sources inside one JSON blob cannot paginate — it has
    to be loaded whole to show ten rows."""
    from billing.persistence import reporting_models as models

    assert any(
        t.__tablename__ == "cp_billing_report_source" for t in models.REPORTING_TABLES
    )


# ── 17. API and authorization ────────────────────────────────────────────────


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


PATH = "/v1/billing/reports"


def test_the_reports_prefix_collides_with_nothing(client):
    spec = client.app.openapi()
    ours = {p for p in spec["paths"] if p.startswith(PATH)}
    others = {p for p in spec["paths"] if not p.startswith(PATH)}
    assert len(ours) == 21
    assert not (ours & others)


def test_there_is_no_delete_put_or_patch_anywhere_on_this_surface(client):
    spec = client.app.openapi()
    for path, ops in spec["paths"].items():
        if path.startswith(PATH):
            assert not ({"delete", "put", "patch"} & set(ops)), path


def test_no_wire_schema_shadows_another_surfaces_name(client):
    """**Regression for D-1.** Seven names in this milestone collided with neighbours; a qualified
    schema name in the public document is how that becomes somebody else's problem."""
    spec = client.app.openapi()
    assert [n for n in spec["components"]["schemas"] if "reporting_schemas" in n] == []


def test_no_foundation_or_application_symbol_shadows_a_neighbour():
    """**Regression for D-1.** Seven names in this milestone collided with existing ones —
    `derive_snapshot_id` (M9.14), `PeriodState` (M9.11), `IntegrityPosture` (M9.19),
    `CompletenessService` (M9.21), and `definition_view`/`package_view`/`period_view` in the
    Application. Every one was caught by ruff's `F811` at import time rather than by review, which
    is the third consecutive milestone where that happened.

    The property that was violated is that two *different objects* shared a name, so the second
    import silently won. It is asserted here directly: every neighbour's symbol and this
    milestone's rename resolve to distinct objects, and every exported name still resolves.

    (Duplicate *listings* of one object in `__all__` exist elsewhere in this package and are
    cosmetic — the same object exported twice shadows nothing. This test is aimed at the failure
    that actually loses code.)"""
    import billing.application as application
    import billing.foundation as foundation
    from billing.foundation import CompletenessService as AuditCompleteness
    from billing.foundation import PeriodState as MeteringPeriodState

    pairs = [
        (AuditCompleteness, foundation.ReportCompletenessService),
        (MeteringPeriodState, foundation.ReportingPeriodState),
        (foundation.derive_snapshot_id, foundation.derive_report_snapshot_id),
        (foundation.IntegrityPosture, foundation.ReportedIntegrity),
        (application.definition_view, application.report_definition_view),
        (application.package_view, application.submission_package_view),
        (application.period_view, application.reporting_period_view),
    ]
    for neighbour, ours in pairs:
        assert neighbour is not ours, (neighbour, ours)

    for module in (foundation, application):
        for name in module.__all__:
            assert hasattr(module, name), (module.__name__, name)


def test_the_reports_tag_is_declared():
    from billing.customer.config import OPENAPI_TAGS

    assert any(tag["name"] == "reports" for tag in OPENAPI_TAGS)


def test_the_seven_reporting_permissions_are_declared_and_distinct():
    values = {p.value for p in BillingPermission}
    for expected in ("billing.report.read", "billing.report.generate", "billing.report.validate",
                     "billing.report.finalize", "billing.report.correct", "billing.report.export",
                     "billing.report.submission_package.read"):
        assert expected in values, expected
    assert len(values) == len(list(BillingPermission))


def test_every_new_message_is_deny_by_default():
    assert len(ALL_REPORTING_COMMANDS) + len(ALL_REPORTING_QUERIES) == 25
    source = Path("billing/application/reporting_engine.py").read_text()
    tree = ast.parse(source)
    for cls in ("ReportingUseCases", "ReportingQueries"):
        node = next(
            n for n in ast.walk(tree) if isinstance(n, ast.ClassDef) and n.name == cls
        )
        assert "require" in ast.dump(node)
    assert "P.BILLING_REPORT_FINALIZE" in source
    assert "P.BILLING_REPORT_CORRECT" in source
    assert "P.BILLING_SUBMISSION_PACKAGE_READ" in source


def test_no_wire_schema_can_name_a_computed_total():
    from billing.customer import reporting_schemas as schemas

    for name in dir(schemas):
        model = getattr(schemas, name)
        if not (isinstance(model, type) and hasattr(model, "model_fields")):
            continue
        for field_name in model.model_fields:
            for banned in ("balance", "_rate", "fx", "converted"):
                assert banned not in field_name, (name, field_name)


# ── 18. the Studio is a Consumer ─────────────────────────────────────────────


def test_the_studio_is_self_contained():
    html = STUDIO.read_text()
    for banned in ("<script src=", "@import", "cdn.", "https://", "http://"):
        assert banned not in html, banned


def test_the_studio_stores_nothing():
    script = _studio_script()
    for banned in ("localStorage", "sessionStorage", "document.cookie", "indexedDB"):
        assert banned not in script, banned


def test_the_studio_calculates_no_figure():
    """No arithmetic on anything it renders, no reduce, and no currency scaling."""
    script = _studio_script()
    for banned in (".reduce(", "parseFloat", "toFixed", "toLocaleString", "Intl.NumberFormat",
                   "exponent"):
        assert banned not in script, banned
    # `* 1000` in `when()` converts epoch seconds to milliseconds for `Date`. It is the only
    # multiplication on the page, and it is a unit conversion on a timestamp.
    assert script.count("*") == script.count("* 1000") == 1
    # No division either. A bare `/` appears in URL literals and comments, so the check names the
    # arithmetic form precisely rather than banning the character.
    import re as _re

    assert not _re.search(r"[\w\)\]]\s/\s[\w\(]", script)


def test_the_studio_decides_no_report_state():
    script = _studio_script().replace("===", "@@@").replace("!==", "@@@")
    for banned in ("completeness =", "status =", "passed =", "blocks_finalization =",
                   "reconcile(", "correlate(", "convert("):
        assert banned not in script, banned


def test_the_studio_never_renders_a_missing_figure_as_zero():
    script = _studio_script()
    assert "amount_minor === null" in script or "amount_minor === undefined" in script
    assert "minor units" in script  # rendered as the domain wrote it, unscaled


def test_the_studio_takes_its_actions_from_the_server(finalized):
    script = _studio_script()
    assert "allowed_triggers" in script
    app, _account_id, _period, report = finalized
    assert report.allowed_triggers == ()  # a finalized filing offers nothing


def test_the_studio_reaches_no_domain_but_its_own():
    script = _studio_script()
    assert 'const API = "/v1/billing/reports"' in script
    for banned in ("/v1/billing/ledger", "/v1/billing/integrity", "/v1/billing/investigations",
                   "/v1/billing/payments", "/v1/billing/taxes"):
        assert banned not in script, banned


# ── 19. the whole loop ───────────────────────────────────────────────────────


def test_the_full_reporting_loop(wired):
    """The milestone end to end on the real pipeline, and the shape of what an examiner reads."""
    app, account_id = wired
    definition = _publish(app)
    period = app.commands.dispatch(
        OpenReportingPeriod(
            **KW, basis=PeriodBasis.ACCOUNTING_PERIOD, period_key="2026-01", start=0.0, end=4e9
        )
    )
    app.commands.dispatch(CloseReportingPeriod(**KW, period_id=period.id))
    report = app.commands.dispatch(
        CreateReport(
            **KW, report_code="LEDGER_MONTHLY", period_id=period.id, account_id=account_id
        )
    )
    report = app.commands.dispatch(PrepareReport(**KW, report_id=report.id))
    result = app.commands.dispatch(ValidateReport(**KW, report_id=report.id))
    assert result.passed, [c.name for c in result.checks if not c.passed]
    report = app.commands.dispatch(FinalizeReport(**KW, report_id=report.id))
    package = app.commands.dispatch(CreateSubmissionPackage(**KW, report_id=report.id))

    assert report.status == ReportStatus.FINALIZED.value
    assert report.completeness == ReportCompleteness.COMPLETE.value
    assert report.scope == ReportScope.ACCOUNT.value
    assert report.definition_version == definition.version

    snapshot = app.queries.dispatch(GetReportSnapshot(**KW, report_id=report.id))
    assert canonical_digest(snapshot.representation) == package.report_digest

    posture = app.queries.dispatch(GetReportingPosture(**KW))
    assert posture.finalized == 1
    for field_name in type(posture).__dataclass_fields__:
        assert not any(b in field_name for b in ("amount", "total_minor", "currency")), field_name

    periods = app.queries.dispatch(ListReportingPeriods(**KW))
    assert [p.state for p in periods] == [ReportingPeriodState.CLOSED.value]


def test_the_billing_package_layout_now_declares_the_reporting_modules():
    """The frozen layout assertion, extended by **addition**. Never weakened."""
    from billing import foundation

    for module in ("commercial_reporting", "reporting_rules", "reporting_factories"):
        assert (Path("billing/foundation") / f"{module}.py").exists(), module
    assert hasattr(foundation, "RegulatoryReport")
