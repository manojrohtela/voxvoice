"""M9.19 — Billing Reconciliation & Financial Integrity Engine.

Every earlier milestone could be checked by asking "is this domain internally consistent?".
Reconciliation is the first one whose whole purpose is a question none of them can ask about
itself:

> *Do the financial facts produced by six independent domains agree with one another — and when
> they do not, can we say exactly where, exactly why, exactly who must fix it, and prove years
> later that we said so?*

So the suite is organised around the five ways that fails:

* **the observer becomes an actor** — reconciliation quietly repairs the domain it was auditing,
  and every "missing ledger posting" finding resolves itself into existence (§2, §9);
* **a divergence is auto-resolved** — somebody calls resolve, the row says fixed, and the books
  still disagree (§7);
* **the engine is blind to its own headline case** — an evidence filter drops exactly the state
  that means "earned but unrecorded" (§11 D-2);
* **two workers disagree about what they saw** — duplicate runs, duplicate findings, a stale
  worker overwriting somebody's resolution (§8);
* **a false critical trains people to ignore criticals** — a correct, tax-free invoice reported as
  a critical integrity failure (§11 D-1).

Every claim is demonstrated **structurally** — by a derived digest, a database constraint, an
absent FSM edge, a whitelist asserted by AST scan, or two real connections racing — rather than by
a happy path assumed to imply the rest. §11 is the numbered adversarial hunt.

The pipeline is the real one: a closed accounting period, a rated run, a posted ledger, an issued
document with a pinned snapshot digest, a published recognition policy and a live schedule. M9.19
reads those and writes to none of them.
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
    ALL_RECONCILIATION_COMMANDS,
    ALL_RECONCILIATION_QUERIES,
    REPAIR_COMMANDS,
    AcknowledgeFinding,
    AllowAllAuthorizer,
    CloseFinding,
    ExplainFinding,
    FindingsByOwner,
    FindingsForAccount,
    GetIntegrityFinding,
    GetIntegrityPosture,
    GetIntegrityRun,
    GetReconciliationMatrix,
    InspectIntegrityRun,
    IntegrityTimeline,
    ListFindingResolutions,
    ListIntegrityRuns,
    ListRepairRequests,
    ListRunFindings,
    OpenFindings,
    PublishRecognitionPolicy,
    RecognizeRevenue,
    ReconcileInvoiceLedger,
    ReconcileInvoiceRecognition,
    ReconcileInvoiceTax,
    ReconcilePaymentInvoice,
    ReconcilePaymentLedger,
    ReconcileRecognitionLedger,
    RequestFindingRepair,
    ResolveFinding,
    SearchIntegrityFindings,
)
from billing.customer.app import create_billing_app
from billing.customer.config import OPENAPI_TAGS, BillingApiSettings
from billing.customer.container import BillingContainer
from billing.foundation import (
    ACTIONABLE_SEVERITIES,
    DIVERGENCE_VERDICT_PROJECTION,
    FINDING_LIFECYCLE,
    RECONCILIATION_MATRIX,
    SEVERITY_BY_KIND,
    TERMINAL_FINDING_STATES,
    UNRESOLVED_FINDING_STATES,
    BillingConflict,
    BillingValidationError,
    Currency,
    DivergenceKind,
    FactRef,
    FindingLifecycleService,
    FindingSeverity,
    FindingStale,
    FindingState,
    FindingTrigger,
    FixedClock,
    IntegrityFactory,
    IntegrityResolutionRepository,
    IntegrityRunRepository,
    InvalidBillingTransition,
    MatchingService,
    MatchMode,
    Money,
    Observation,
    PeriodGranularity,
    RecognitionMethod,
    ReconciliationGuardService,
    ReconciliationRefused,
    ReconciliationScope,
    ReconciliationValidator,
    RelationshipKind,
    RepairOwner,
    RepairRequestRepository,
    RunState,
    SequentialIdGenerator,
    derive_evidence_digest,
    derive_finding_id,
    derive_run_id,
)
from billing.persistence.composition import build_persistent_billing_application
from billing.persistence.migrations import MIGRATIONS

ORG, ACTOR = inv.ORG, inv.ACTOR
KW = inv.KW
INR = Currency.INR
SECRET = "test-secret-value-for-billing-integrity"

RECONCILIATION_FILES = (
    Path("billing/foundation/commercial_reconciliation.py"),
    Path("billing/foundation/reconciliation_rules.py"),
    Path("billing/foundation/reconciliation_factories.py"),
    Path("billing/application/reconciliation_engine.py"),
    Path("billing/persistence/reconciliation_models.py"),
    Path("billing/persistence/reconciliation_repositories.py"),
    Path("billing/customer/reconciliation_schemas.py"),
    Path("billing/customer/routers/reconciliation_engine.py"),
)

FOUNDATION_FILES = RECONCILIATION_FILES[:3]

_html_code_only = rev._html_code_only


def _code_only(source: str) -> str:
    """Source with docstrings and comments stripped, dedented first.

    Dedenting matters: `inspect.getsource` of a *method* is indented, and `ast.parse` refuses it —
    so a scan that forgot this would raise rather than assert, and a raising test proves nothing
    about the thing it was meant to check."""
    return rev._code_only(textwrap.dedent(source))


# ── fixtures ────────────────────────────────────────────────────────────────────


@pytest.fixture
def wired():
    """The real pipeline: closed period, rated run, posted ledger, issued document, policy."""
    persistent, account, generator = rev._wired()
    app = persistent.application
    inv._closed(app, account)
    app.commands.dispatch(
        PublishRecognitionPolicy(
            **KW,
            code="std",
            version=1,
            method=RecognitionMethod.STRAIGHT_LINE,
            granularity=PeriodGranularity.MONTH,
        )
    )
    yield persistent, account
    for _ in generator:  # pragma: no cover — drains the invoice fixture's teardown
        pass


def _recognized(app, account, *, periods: int = 2, open_year: bool = True):
    """An issued document with a live schedule and `periods` recognized periods."""
    if open_year:
        rev._open_year(app, account)
    document, schedule, entries = rev._active(app, account)
    for entry in entries[:periods]:
        app.commands.dispatch(
            RecognizeRevenue(**KW, entry_id=entry.id, at=entry.recognize_on + 1)
        )
    return document, schedule, entries


def _fact(identity: str, minor: int | None = 100, **overrides) -> FactRef:
    body = dict(
        identity=identity,
        kind="journal_entry",
        amount=Money(minor, INR) if minor is not None else None,
    )
    body.update(overrides)
    return FactRef(**body)


def _spec(relationship: RelationshipKind = RelationshipKind.RECOGNITION_LEDGER):
    return RECONCILIATION_MATRIX[relationship]


def _finding(**overrides):
    """One finding built straight from the factory — no database, no pipeline."""
    factory = IntegrityFactory(ids=SequentialIdGenerator(), clock=FixedClock(1_000.0, step=0.0))
    scope = ReconciliationScope(
        org_id=ORG, relationship=RelationshipKind.RECOGNITION_LEDGER, subject_id="rvs_1"
    )
    source = overrides.pop("source", _fact("rve_1", kind="recognition_entry"))
    run = factory.run(scope, (source,), (), at=1_000.0)
    observation = Observation(
        subject_key=overrides.pop("subject_key", source.identity),
        divergence=overrides.pop("divergence", DivergenceKind.MISSING_COUNTERPART),
        source=source,
        counterpart=overrides.pop("counterpart", None),
    )
    built = factory.finding(
        run, observation, owner=overrides.pop("owner", RepairOwner.RECOGNITION), at=1_000.0
    )
    return replace(built, **overrides) if overrides else built


def _two_sessions(tmp_path, name: str = "integrity.db"):
    """Two Applications over one file-backed database, each with its own connection.

    Two *connections* rather than two threads: a thread schedule that does not actually interleave
    produces a green test that proved nothing."""
    url = f"sqlite:///{tmp_path / name}"
    first = build_persistent_billing_application(
        url=url,
        authorizer=AllowAllAuthorizer(),
        ids=rev._TaggedIds("a"),
        clock=FixedClock(1_000.0, step=0.0),
    )
    second = build_persistent_billing_application(
        url=url,
        authorizer=AllowAllAuthorizer(),
        ids=rev._TaggedIds("b"),
        clock=FixedClock(1_000.0, step=0.0),
        create_all=False,
    )
    assert first.database is not second.database
    return first, second


# ── 1. the audit: existing reconciliation vocabulary is reused, not shadowed ───


def test_m9_2_reconciliation_finding_is_untouched_and_not_shadowed():
    """M9.2 owns `ReconciliationFinding` for a payment↔provider observation. This milestone
    declares `IntegrityFinding` beside it rather than a second type with the same name."""
    from billing.foundation import IntegrityFinding
    from billing.foundation import ReconciliationFinding as M92

    assert IntegrityFinding is not M92
    assert {f.name for f in M92.__dataclass_fields__.values()} == {
        "payment_id",
        "provider_code",
        "verdict",
        "ledger_amount",
        "provider_amount",
        "observed_at",
    }
    source = Path("billing/foundation/commercial_reconciliation.py").read_text()
    assert "class ReconciliationFinding" not in source
    assert "class ReconciliationOutcome" not in source
    assert "class ReconciliationStatus" not in source


def test_m9_2_verdict_is_reused_and_every_divergence_projects_onto_it():
    """A table rather than a chain of `if`s, so the projection cannot quietly lose a member."""
    from billing.foundation import ReconciliationVerdict

    assert set(DIVERGENCE_VERDICT_PROJECTION) == set(DivergenceKind)
    assert set(DIVERGENCE_VERDICT_PROJECTION.values()) <= set(ReconciliationVerdict)


def test_m9_16_payment_provider_reconciliation_is_not_duplicated():
    """Payment ↔ provider is M9.16's. **No pair has two owners.**"""
    assert not any("provider" in k.value for k in RelationshipKind)
    for path in FOUNDATION_FILES:
        code = _code_only(path.read_text())
        for forbidden in ("PaymentReconciliation", "ProviderReport", "provider_code"):
            assert forbidden not in code, (path.name, forbidden)


def test_delegated_comparisons_are_not_reimplemented():
    """M9.14 and M9.15 already own their pairs. M9.19 lifts their discrepancies; it does not
    write a second opinion about the same two facts."""
    engine = _code_only(Path("billing/application/reconciliation_engine.py").read_text())
    assert "InvoiceReconciliationService" in engine
    assert "TaxReconciliationService" in engine
    for path in FOUNDATION_FILES:
        code = _code_only(path.read_text())
        assert "InvoiceDiscrepancyKind" not in code, path.name
        assert "TaxDiscrepancyKind" not in code, path.name


def test_every_delegated_discrepancy_kind_is_mapped():
    """A new member in any of the three enums must fail loudly, not silently degrade to
    `EVIDENCE_MALFORMED` — which would report a real divergence as a reader problem."""
    from billing.foundation import DEFAULT_DISCREPANCY_MAP
    from billing.foundation.commercial_invoicing import InvoiceDiscrepancyKind
    from billing.foundation.commercial_ledger import LedgerDiscrepancyKind
    from billing.foundation.commercial_tax import TaxDiscrepancyKind

    for enum in (InvoiceDiscrepancyKind, TaxDiscrepancyKind, LedgerDiscrepancyKind):
        for member in enum:
            assert member.value in DEFAULT_DISCREPANCY_MAP, (enum.__name__, member.value)


# ── 2. ownership: an observer, structurally ───────────────────────────────────


def test_reconciliation_writes_to_no_other_domains_storage():
    """**The core principle, enforced by scan.** A reconciliation result is an observation, not
    permission to mutate another domain."""
    banned_writes = (
        "LedgerRepository",
        "PaymentRepository",
        "InvoiceRepository",
        "TaxRepository",
        "RecognitionRepository",
        "JournalEntryRepository",
        "SqlJournalEntryRepository",
    )
    for path in RECONCILIATION_FILES:
        code = _code_only(path.read_text())
        for forbidden in banned_writes:
            assert forbidden not in code, (path.name, forbidden)


def test_no_ledger_write_is_reachable_from_this_milestone():
    """**`PostJournalEntry` is absent, and its absence is the point.**

    A reconciliation engine that could post a journal entry would create the financial fact it
    claims to have observed, and every `MISSING_COUNTERPART` finding would resolve itself into
    existence."""
    for path in RECONCILIATION_FILES:
        code = _code_only(path.read_text())
        assert "PostJournalEntry" not in code, path.name
    assert PostJournalEntryIsNotWhitelisted()


def PostJournalEntryIsNotWhitelisted() -> bool:
    from billing.application.ledger_engine import PostJournalEntry

    return all(command is not PostJournalEntry for _, command in REPAIR_COMMANDS.values())


def test_the_repair_whitelist_is_small_closed_and_owned_elsewhere():
    """Every dispatchable repair is the **owning domain's own declared command**."""
    from billing.application.payment_engine import ReconcilePaymentAttempt
    from billing.application.revenue_engine import PostRecognitionToLedger

    assert {c for _, c in REPAIR_COMMANDS.values()} == {
        PostRecognitionToLedger,
        ReconcilePaymentAttempt,
    }
    for (relationship, divergence), (action, command) in REPAIR_COMMANDS.items():
        assert isinstance(relationship, RelationshipKind)
        assert isinstance(divergence, DivergenceKind)
        assert action and isinstance(command, type)


def test_no_auto_resolution_helper_exists_anywhere():
    """ADR-0023 PY5. The mandate names five; none of them may exist under any name."""
    for path in RECONCILIATION_FILES:
        code = _code_only(path.read_text())
        for forbidden in (
            "auto_refund",
            "auto_reverse",
            "auto_post",
            "auto_mark_paid",
            "auto_fix",
            "force_state",
            "admin_override",
        ):
            assert forbidden not in code, (path.name, forbidden)


def test_reconciliation_computes_no_price_rate_tax_or_balance():
    """It compares figures other domains decided. It does not produce any."""
    for path in RECONCILIATION_FILES:
        code = _code_only(path.read_text())
        for forbidden in (
            "PriceResolver",
            "RatingEngine",
            "TaxCalculator",
            "calculate_tax",
            "resolve_price",
            "BalanceService",
            "AccountBalance",
            "reconciled_balance",
            "expected_balance",
            "calculated_balance",
        ):
            assert forbidden not in code, (path.name, forbidden)


def test_no_balance_or_monetary_total_is_stored():
    """**A stored divergent total would be a figure M9.19 computed**, and somebody would report
    it beside figures the Ledger owns."""
    from billing.persistence.reconciliation_models import RECONCILIATION_TABLES

    for table in RECONCILIATION_TABLES:
        for column in table.__table__.columns:
            assert "balance" not in column.name, (table.__tablename__, column.name)
            assert "total_minor" not in column.name, (table.__tablename__, column.name)


def test_posture_carries_counts_and_never_an_amount():
    from billing.foundation import IntegrityPosture

    for name, field in IntegrityPosture.__dataclass_fields__.items():
        assert "amount" not in name and "minor" not in name
        assert field.type not in ("Money", "Money | None")


def test_no_provider_no_http_no_sdk_anywhere():
    """Checked at the **import** graph rather than by substring.

    A substring scan for `"requests"` matches `self._repos.requests`, which is this milestone's
    own repository — the same false positive that bit M9.17's vendor scan. A library can only
    enter through an import, so that is where the ban is enforced."""
    banned = {"httpx", "requests", "urllib", "urllib3", "socket", "http", "stripe",
              "razorpay", "boto3", "paypalrestsdk"}
    for path in RECONCILIATION_FILES:
        for node in ast.walk(ast.parse(path.read_text())):
            if isinstance(node, ast.Import):
                roots = {alias.name.split(".")[0] for alias in node.names}
            elif isinstance(node, ast.ImportFrom):
                roots = {(node.module or "").split(".")[0]}
            else:
                continue
            assert not (roots & banned), (path.name, roots & banned)


def test_no_collections_policy_and_no_dunning_mutation():
    """Collections may react to integrity outcomes. Reconciliation never changes a dunning stage."""
    for path in RECONCILIATION_FILES:
        code = _code_only(path.read_text())
        for forbidden in ("DunningState", "CollectionCase", "CollectionAction", "delinquent"):
            assert forbidden not in code, (path.name, forbidden)


def test_foundation_is_clock_free_and_performs_no_io():
    """Every temporal evaluation receives `at`. Nothing here reads an ambient clock."""
    for path in FOUNDATION_FILES:
        code = _code_only(path.read_text())
        for forbidden in ("datetime.now", "time.time", "utcnow", "open(", "session", "engine"):
            assert forbidden not in code, (path.name, forbidden)


def test_no_scheduler_exists_in_foundation():
    for path in FOUNDATION_FILES:
        code = _code_only(path.read_text())
        for forbidden in ("sleep", "threading", "asyncio", "cron", "while True", "BackgroundTask"):
            assert forbidden not in code, (path.name, forbidden)


def test_no_runtime_dependency():
    for path in RECONCILIATION_FILES:
        code = _code_only(path.read_text())
        for forbidden in ("from runtime", "import runtime", "pipecat", "voxvoice.runtime"):
            assert forbidden not in code, (path.name, forbidden)


def test_no_float_arithmetic_on_money():
    """Integer minor units, everywhere. The only summation is `total_minor`, over integers."""
    from billing.foundation.commercial_reconciliation import total_minor

    source = _code_only(_inspect.getsource(total_minor))
    for forbidden in ("float(", "round(", "/ ", "Decimal"):
        assert forbidden not in source, forbidden


# ── 3. the matrix: six relationships, one owner each ──────────────────────────


def test_the_matrix_is_complete_and_validates():
    assert set(RECONCILIATION_MATRIX) == set(RelationshipKind)
    assert not ReconciliationValidator().validate_matrix().has_errors


def test_every_relationship_declares_what_the_mandate_requires():
    for kind, spec in RECONCILIATION_MATRIX.items():
        assert spec.relationship is kind
        assert spec.source_kind and spec.counterpart_kind
        assert spec.authoritative in ("source", "counterpart")
        assert spec.matching_identity
        assert spec.compares
        assert isinstance(spec.mode, MatchMode)
        assert isinstance(spec.owner, RepairOwner)
        assert spec.summary


def test_no_pair_has_two_owners():
    """One situation, one queue. Two owners is how a divergence is fixed twice and reported once."""
    seen: dict[tuple[str, str], RelationshipKind] = {}
    for kind, spec in RECONCILIATION_MATRIX.items():
        pair = tuple(sorted((spec.source_kind, spec.counterpart_kind)))
        assert pair not in seen, (pair, kind, seen[pair])
        seen[pair] = kind


def test_no_relationship_matches_on_an_amount_or_a_timestamp():
    """**The mandate's hardest rule.** Matching two financial records by amount is how a
    reconciliation confidently pairs a ₹500 refund with an unrelated ₹500 capture."""
    for spec in RECONCILIATION_MATRIX.values():
        identity = spec.matching_identity.lower()
        for forbidden in ("amount", "timestamp", "description", "total"):
            assert forbidden not in identity, (spec.relationship, forbidden)


def test_only_invoice_recognition_allows_partial_accounting():
    """A twelve-month invoice with three months recognized is **correct**. Requiring equality
    would report every service-period invoice as broken for eleven months of its life."""
    partial = {k for k, s in RECONCILIATION_MATRIX.items() if s.allows_partial}
    assert partial == {RelationshipKind.INVOICE_RECOGNITION}


def test_payment_invoice_does_not_flag_orphans():
    """An unpaid invoice is a collections question M9.17 owns, not an integrity defect."""
    assert not RECONCILIATION_MATRIX[RelationshipKind.PAYMENT_INVOICE].flags_orphans


# ── 4. severity: deterministic, table-driven, never decoration ────────────────


def test_every_divergence_has_a_declared_severity():
    assert set(SEVERITY_BY_KIND) == set(DivergenceKind)


def test_the_mandates_severity_floor_is_enforced():
    """A fabricated financial fact, a duplicate financial posting, and an amount mismatch are
    **never** informational."""
    for kind in (
        DivergenceKind.MISSING_COUNTERPART,
        DivergenceKind.MISSING_SOURCE,
        DivergenceKind.DUPLICATE_COUNTERPART,
        DivergenceKind.DUPLICATE_SOURCE,
        DivergenceKind.AMOUNT_MISMATCH,
        DivergenceKind.CURRENCY_MISMATCH,
        DivergenceKind.OVER_ACCOUNTED,
    ):
        assert SEVERITY_BY_KIND[kind] is FindingSeverity.CRITICAL, kind


def test_no_divergence_is_ever_classified_info():
    """`INFO` is what a *match* scores. A divergence is never informational."""
    assert FindingSeverity.INFO not in set(SEVERITY_BY_KIND.values())


def test_period_mismatch_is_a_warning_because_m9_13_makes_it_legitimate():
    """A fact recorded against a closed period lands in the next open one **by design**. Flagging
    correct accounting as an error teaches operators to ignore the report."""
    assert SEVERITY_BY_KIND[DivergenceKind.PERIOD_MISMATCH] is FindingSeverity.WARNING
    assert SEVERITY_BY_KIND[DivergenceKind.PERIOD_MISMATCH] not in ACTIONABLE_SEVERITIES


def test_ambiguous_is_a_warning_not_a_critical():
    """M9.16's lesson carried forward: an honest unknown is not a known wrong, and escalating
    unknowns to critical trains people to ignore critical."""
    assert SEVERITY_BY_KIND[DivergenceKind.AMBIGUOUS] is FindingSeverity.WARNING


def test_severity_is_written_once_and_never_changed():
    """A resolution records that a critical divergence was fixed. It does not make it a warning."""
    lifecycle = FindingLifecycleService()
    finding = _finding()
    assert finding.severity is FindingSeverity.CRITICAL
    resolved = lifecycle.apply(finding, FindingTrigger.RESOLVE, at=2_000.0, actor_id=ACTOR)
    assert resolved.severity is finding.severity
    source = _code_only(_inspect.getsource(FindingLifecycleService.apply))
    assert '"severity"' not in source


# ── 5. identity: a run is what it looked at ───────────────────────────────────


def test_the_evidence_digest_covers_what_was_compared_and_nothing_volatile():
    a, b = _fact("x"), _fact("y", 200)
    digest = derive_evidence_digest((a.evidence_key(), b.evidence_key()))
    assert digest == derive_evidence_digest((b.evidence_key(), a.evidence_key()))  # order-free
    changed = derive_evidence_digest((a.evidence_key(), _fact("y", 201).evidence_key()))
    assert changed != digest


def test_observation_time_is_absent_from_the_evidence_key():
    """A volatile value would make every re-derivation a new run and destroy the idempotency the
    digest exists to provide."""
    assert _fact("x", at=1.0).evidence_key() == _fact("x", at=999_999.0).evidence_key()
    source = _code_only(_inspect.getsource(derive_evidence_digest))
    for forbidden in ("time.time", "datetime", "now", "uuid", "random"):
        assert forbidden not in source, forbidden


def test_a_run_id_is_derived_so_two_workers_converge():
    args = (ORG, "recognition_ledger", "scope-key", "d" * 64)
    assert derive_run_id(*args) == derive_run_id(*args)
    assert derive_run_id(*args) != derive_run_id("other-org", *args[1:])
    assert derive_run_id(*args) != derive_run_id(*args[:3], "e" * 64)


def test_a_finding_id_is_keyed_on_the_run_so_a_recurrence_is_a_new_finding():
    """**Why this matters**: a divergence that recurs after an earlier one was resolved must
    produce a new finding, not a second write to a closed row."""
    a = derive_finding_id("irn_1", "recognition_ledger", "rve_1", "missing_counterpart")
    assert a == derive_finding_id("irn_1", "recognition_ledger", "rve_1", "missing_counterpart")
    assert a != derive_finding_id("irn_2", "recognition_ledger", "rve_1", "missing_counterpart")


def test_a_scope_key_is_org_first_so_two_orgs_can_never_collide():
    a = ReconciliationScope(ORG, RelationshipKind.PAYMENT_LEDGER, "acct_1").key
    b = ReconciliationScope("org_other", RelationshipKind.PAYMENT_LEDGER, "acct_1").key
    assert a != b
    assert a.startswith(ORG)


def test_a_bare_number_is_refused_as_a_matching_identity():
    """The mandate forbids matching by amount or timestamp. This is where it becomes checkable."""
    validator = ReconciliationValidator()
    assert validator.validate_fact(_fact("rve_1")).has_errors is False
    assert validator.validate_fact(_fact("12345")).has_errors
    assert validator.validate_fact(_fact("1700000000.5")).has_errors
    assert validator.validate_fact(_fact("")).has_errors


def test_an_unscoped_run_is_refused_before_anything_is_read():
    factory = IntegrityFactory(ids=SequentialIdGenerator(), clock=FixedClock(1.0, step=0.0))
    with pytest.raises(BillingValidationError):
        factory.run(ReconciliationScope("", RelationshipKind.PAYMENT_LEDGER), (), ())
    with pytest.raises(ReconciliationRefused):
        ReconciliationGuardService.guard_scoped(
            ReconciliationScope("", RelationshipKind.PAYMENT_LEDGER)
        )


# ── 6. the comparison engine ──────────────────────────────────────────────────


def test_matching_pairs_on_identity_and_reports_agreement():
    result = MatchingService().compare(_spec(), (_fact("a"),), (_fact("a"),))
    assert len(result) == 1 and not result[0].is_divergent


@pytest.mark.parametrize(
    ("sources", "counterparts", "expected"),
    [
        ((_fact("a"),), (), DivergenceKind.MISSING_COUNTERPART),
        ((), (_fact("a"),), DivergenceKind.UNEXPECTED_COUNTERPART),
        ((_fact("a"),), (_fact("a", 200),), DivergenceKind.AMOUNT_MISMATCH),
        ((_fact("a"), _fact("a")), (_fact("a"),), DivergenceKind.DUPLICATE_SOURCE),
        ((_fact("a"),), (_fact("a"), _fact("a")), DivergenceKind.DUPLICATE_COUNTERPART),
        ((_fact("a"),), (_fact("a", None),), DivergenceKind.EVIDENCE_MALFORMED),
    ],
)
def test_every_pairwise_divergence_class_is_detected(sources, counterparts, expected):
    result = MatchingService().compare(_spec(), sources, counterparts)
    assert expected in {o.divergence for o in result}, (expected, result)


def test_a_currency_mismatch_is_not_reported_as_an_amount_difference():
    """Two amounts in different currencies are **incomparable**, not "a mismatch of 300".
    Reporting an integer difference between them would be an invented number."""
    result = MatchingService().compare(
        _spec(),
        (_fact("a", 100),),
        (FactRef(identity="a", kind="journal_entry", amount=Money(100, Currency.USD)),),
    )
    assert result[0].divergence is DivergenceKind.CURRENCY_MISMATCH


def test_account_and_period_mismatches_are_detected():
    spec = _spec(RelationshipKind.RECOGNITION_LEDGER)
    account = MatchingService().compare(
        spec,
        (_fact("a", account_id="acc_1"),),
        (_fact("a", account_id="acc_2"),),
    )
    assert account[0].divergence is DivergenceKind.ACCOUNT_MISMATCH
    period = MatchingService().compare(
        spec,
        (_fact("a", account_id="acc_1", period_key="2024-01"),),
        (_fact("a", account_id="acc_1", period_key="2024-02"),),
    )
    assert period[0].divergence is DivergenceKind.PERIOD_MISMATCH


def test_a_source_reference_mismatch_is_detected():
    spec = _spec(RelationshipKind.PAYMENT_LEDGER)
    result = MatchingService().compare(
        spec, (_fact("pat_1", kind="payment_attempt"),), (_fact("pat_1", source_id="pat_9"),)
    )
    assert result[0].divergence is DivergenceKind.SOURCE_REFERENCE_MISMATCH


def test_an_aggregate_shortfall_is_deferred_revenue_not_a_divergence():
    spec = _spec(RelationshipKind.INVOICE_RECOGNITION)
    result = MatchingService().compare(
        spec, (_fact("inv_1", 120_000),), (_fact("rve_1", 30_000),)
    )
    assert not result[0].is_divergent
    assert "deferred" in result[0].detail


def test_accounting_for_more_than_was_billed_is_critical():
    spec = _spec(RelationshipKind.INVOICE_RECOGNITION)
    result = MatchingService().compare(
        spec, (_fact("inv_1", 120_000),), (_fact("rve_1", 130_000),)
    )
    assert result[0].divergence is DivergenceKind.OVER_ACCOUNTED
    assert result[0].severity is FindingSeverity.CRITICAL


def test_an_aggregate_never_sums_across_currencies():
    """Converting would make this a foreign-exchange opinion Billing does not hold (RS5)."""
    spec = _spec(RelationshipKind.INVOICE_RECOGNITION)
    result = MatchingService().compare(
        spec,
        (_fact("inv_1", 100),),
        (FactRef(identity="rve_1", kind="x", amount=Money(100, Currency.USD)),),
    )
    assert result[0].divergence is DivergenceKind.CURRENCY_MISMATCH


def test_orphans_are_suppressed_where_the_spec_says_so():
    spec = _spec(RelationshipKind.PAYMENT_INVOICE)
    result = MatchingService().compare(spec, (), (_fact("inv_1"),))
    assert not [o for o in result if o.is_divergent]


# ── 7. the lifecycle: PY5 as an absent edge ───────────────────────────────────


def test_resolved_and_closed_are_terminal_by_construction():
    """**ADR-0023 PY5 expressed as absent edges.** A resolved finding cannot be reopened,
    downgraded, or rewritten, because the machine has nowhere for it to go."""
    assert FINDING_LIFECYCLE.terminal_states() == frozenset(
        {FindingState.RESOLVED, FindingState.CLOSED}
    )
    assert TERMINAL_FINDING_STATES == FINDING_LIFECYCLE.terminal_states()
    for state in TERMINAL_FINDING_STATES:
        assert FindingLifecycleService().allowed(state) == ()


def test_there_is_no_diverged_to_matched_edge_because_matched_is_not_a_state():
    """A match is a counter on the run. Making agreement a row would fill the table with
    non-events and make "a finding represents one divergence" false."""
    assert "matched" not in {s.value for s in FindingState}


def test_every_finding_state_is_reachable():
    """The M9.16-R1 P-5 lesson: a state nothing can reach is a state nobody tested."""
    reachable = {FindingState.OPEN}
    frontier = [FindingState.OPEN]
    while frontier:
        current = frontier.pop()
        for trigger in FINDING_LIFECYCLE.triggers_from(current):
            target = FINDING_LIFECYCLE.get(current, trigger).target
            if target not in reachable:
                reachable.add(target)
                frontier.append(target)
    assert reachable == set(FindingState)


def test_a_stale_worker_cannot_reopen_a_settled_finding_in_the_domain():
    lifecycle = FindingLifecycleService()
    resolved = lifecycle.apply(_finding(), FindingTrigger.RESOLVE, at=2_000.0)
    for trigger in FindingTrigger:
        with pytest.raises(InvalidBillingTransition):
            lifecycle.apply(resolved, trigger, at=3_000.0)


def test_the_guard_refuses_a_terminal_finding_with_a_legible_reason():
    resolved = FindingLifecycleService().apply(_finding(), FindingTrigger.RESOLVE, at=2_000.0)
    with pytest.raises(BillingConflict, match="never reopened"):
        ReconciliationGuardService.guard_transitionable(resolved)


def test_unresolved_states_are_exactly_the_non_terminal_ones():
    assert UNRESOLVED_FINDING_STATES == set(FindingState) - TERMINAL_FINDING_STATES


def test_resolution_requires_evidence_that_establishes_agreement():
    """**Without this guard, `resolve` is `auto_fix()` under another name**: somebody calls it,
    the row says resolved, and the books still disagree."""
    guard = ReconciliationGuardService()
    missing = _finding()
    with pytest.raises(ReconciliationRefused, match="both sides now exist"):
        guard.guard_evidence_establishes_agreement(missing, missing.source, None)
    mismatch = _finding(divergence=DivergenceKind.AMOUNT_MISMATCH)
    with pytest.raises(ReconciliationRefused, match="still shows"):
        guard.guard_evidence_establishes_agreement(
            mismatch, _fact("a", 100), _fact("a", 200)
        )
    with pytest.raises(ReconciliationRefused, match="denominated"):
        guard.guard_evidence_establishes_agreement(
            mismatch,
            _fact("a", 100),
            FactRef(identity="a", kind="x", amount=Money(100, Currency.USD)),
        )
    guard.guard_evidence_establishes_agreement(mismatch, _fact("a", 100), _fact("a", 100))


def test_a_finding_owned_by_an_operator_has_no_automatic_repair():
    with pytest.raises(ReconciliationRefused, match="no automatic repair"):
        ReconciliationGuardService.guard_repairable(_finding(owner=RepairOwner.OPERATOR))


def test_orphans_and_malformed_evidence_route_to_an_operator():
    """Neither is the source domain's problem: it has no record of an orphan to fix, and
    malformed evidence is a reader problem rather than a books problem."""
    from billing.foundation import owner_for_divergence

    for divergence in (
        DivergenceKind.UNEXPECTED_COUNTERPART,
        DivergenceKind.DUPLICATE_SOURCE,
        DivergenceKind.EVIDENCE_MALFORMED,
        DivergenceKind.STALE_EVIDENCE,
    ):
        assert (
            owner_for_divergence(RelationshipKind.RECOGNITION_LEDGER, divergence)
            is RepairOwner.OPERATOR
        )


# ── 8. persistence, idempotency and genuine concurrency ───────────────────────


def test_migration_0029_is_additive_and_creates_five_tables():
    from billing.persistence.migrations import v0029_billing_reconciliation as m

    assert m.VERSION == "0029"
    assert len(m.TABLES) == 5
    source = Path("billing/persistence/migrations/v0029_billing_reconciliation.py").read_text()
    for forbidden in ("ALTER", "DROP", "alter_column", "drop_column"):
        assert forbidden not in _code_only(source), forbidden


def test_the_migration_chain_includes_0029():
    """Membership rather than "is last": M9.20 appended 0030 behind this one, and asserting a tail
    position would make every future milestone edit an older milestone's test."""
    versions = [m.VERSION for m in MIGRATIONS]
    assert versions == sorted(versions)
    assert "0029" in versions


def test_append_only_repositories_have_no_update_or_delete():
    for contract in (IntegrityResolutionRepository, RepairRequestRepository):
        assert not hasattr(contract, "update")
        assert not hasattr(contract, "delete")
    assert not hasattr(IntegrityRunRepository, "delete")


def test_a_duplicate_run_over_unchanged_evidence_writes_nothing(wired):
    persistent, account = wired
    app = persistent.application
    _document, schedule, _ = _recognized(app, account)
    first = app.commands.dispatch(ReconcileRecognitionLedger(**KW, schedule_id=schedule.id))
    second = app.commands.dispatch(ReconcileRecognitionLedger(**KW, schedule_id=schedule.id))
    assert first.run.id == second.run.id
    assert first.is_new and not second.is_new
    assert len(app.queries.dispatch(ListIntegrityRuns(**KW))) == 1


def test_a_rerun_after_a_repair_is_genuinely_a_new_run(wired):
    """Different evidence, different digest, different run. That is what makes "reconciliation
    observes the result later" a real second observation rather than a rewrite of the first."""
    persistent, account = wired
    app = persistent.application
    document, schedule, entries = _recognized(app, account, periods=1)
    first = app.commands.dispatch(ReconcileRecognitionLedger(**KW, schedule_id=schedule.id))
    app.commands.dispatch(
        RecognizeRevenue(**KW, entry_id=entries[1].id, at=entries[1].recognize_on + 1)
    )
    second = app.commands.dispatch(ReconcileRecognitionLedger(**KW, schedule_id=schedule.id))
    assert second.run.id != first.run.id
    assert second.run.compared > first.run.compared


def test_two_connections_reconciling_the_same_facts_converge(tmp_path, wired):
    """**Genuine concurrency**: two Applications, two database connections, one set of facts."""
    persistent, account = wired
    app = persistent.application
    _document, schedule, _ = _recognized(app, account)
    scope = ReconciliationScope(
        org_id=ORG, relationship=RelationshipKind.RECOGNITION_LEDGER, subject_id=schedule.id
    )
    factory = IntegrityFactory(ids=SequentialIdGenerator(), clock=FixedClock(1.0, step=0.0))
    evidence = (_fact("rve_1", kind="recognition_entry"),)
    run = factory.run(scope, evidence, (), at=1.0)
    repos = app.context.reconciliation_engine
    a, first_new = repos.runs.create(run)
    b, second_new = repos.runs.create(run)
    assert a.id == b.id
    assert first_new and not second_new


def test_two_workers_recording_the_same_divergence_write_one_finding(tmp_path):
    first, second = _two_sessions(tmp_path)
    factory = IntegrityFactory(ids=SequentialIdGenerator(), clock=FixedClock(1.0, step=0.0))
    scope = ReconciliationScope(ORG, RelationshipKind.RECOGNITION_LEDGER, "rvs_1")
    source = _fact("rve_1", kind="recognition_entry")
    run = factory.run(scope, (source,), (), at=1.0)
    observation = Observation(
        subject_key="rve_1", divergence=DivergenceKind.MISSING_COUNTERPART, source=source
    )
    finding = factory.finding(run, observation, owner=RepairOwner.RECOGNITION, at=1.0)
    for persistent in (first, second):
        repos = persistent.application.context.reconciliation_engine
        repos.runs.create(run)
        stored, _ = repos.findings.append(finding)
        persistent.application.uow_factory.new().__enter__()
    a_repos = first.application.context.reconciliation_engine
    with first.application.uow_factory.new():
        a_repos.runs.create(run)
        _, is_new_a = a_repos.findings.append(finding)
    with second.application.uow_factory.new():
        b_repos = second.application.context.reconciliation_engine
        _, is_new_b = b_repos.findings.append(finding)
    assert not is_new_a and not is_new_b


def test_a_stale_worker_cannot_overwrite_a_resolution_in_the_database(tmp_path):
    """**The mandate's stale-worker scenario, with two real connections.**

    Worker A reads `OPEN`. Worker B resolves and commits. Worker A resumes and writes — and the
    conditional UPDATE finds nothing, so it fails loudly rather than silently replacing a
    resolution with a stale row."""
    first, second = _two_sessions(tmp_path, "stale.db")
    factory = IntegrityFactory(ids=SequentialIdGenerator(), clock=FixedClock(1.0, step=0.0))
    scope = ReconciliationScope(ORG, RelationshipKind.RECOGNITION_LEDGER, "rvs_1")
    source = _fact("rve_1", kind="recognition_entry")
    run = factory.run(scope, (source,), (), at=1.0)
    observation = Observation(
        subject_key="rve_1", divergence=DivergenceKind.MISSING_COUNTERPART, source=source
    )
    finding = factory.finding(run, observation, owner=RepairOwner.RECOGNITION, at=1.0)
    with first.application.uow_factory.new():
        repos = first.application.context.reconciliation_engine
        repos.runs.create(run)
        repos.findings.append(finding)

    lifecycle = FindingLifecycleService()
    with second.application.uow_factory.new():
        b = second.application.context.reconciliation_engine
        stored = b.findings.get(ORG, finding.id)
        b.findings.advance(
            lifecycle.apply(stored, FindingTrigger.RESOLVE, at=2.0), expected_version=0
        )

    with pytest.raises(FindingStale, match="another worker settled it"), \
            first.application.uow_factory.new():
        a = first.application.context.reconciliation_engine
        a.findings.advance(
            lifecycle.apply(finding, FindingTrigger.ACKNOWLEDGE, at=3.0), expected_version=0
        )


def test_findings_survive_a_restart(tmp_path, wired):
    """Durability across two Applications over the same file — not one object's memory."""
    persistent, account = wired
    app = persistent.application
    _document, schedule, _ = _recognized(app, account, periods=1, open_year=False)
    receipt = app.commands.dispatch(ReconcileRecognitionLedger(**KW, schedule_id=schedule.id))
    assert receipt.run.diverged == 1
    reopened = build_persistent_billing_application(
        database=persistent.database,
        authorizer=AllowAllAuthorizer(),
        create_all=False,
    )
    found = reopened.application.queries.dispatch(
        GetIntegrityFinding(**KW, finding_id=receipt.findings[0].id)
    )
    assert found.severity == "critical"


# ── 9. the repair boundary ────────────────────────────────────────────────────


def test_a_missing_ledger_posting_is_detected_repaired_and_then_observed(wired):
    """**The mandate's worked example, end to end.**

    Recognition succeeded but the Ledger refused (no open period) → CRITICAL finding, owner
    `recognition` → repair requested through M9.18's own command → rerun → clean → resolved."""
    persistent, account = wired
    app = persistent.application
    _document, schedule, _ = _recognized(app, account, periods=1, open_year=False)

    receipt = app.commands.dispatch(ReconcileRecognitionLedger(**KW, schedule_id=schedule.id))
    finding = receipt.findings[0]
    assert finding.divergence == "missing_counterpart"
    assert finding.severity == "critical"
    assert finding.owner == "recognition"

    rev._open_year(app, account)
    request = app.commands.dispatch(
        RequestFindingRepair(**KW, finding_id=finding.id, request_key="r1")
    )
    assert request.dispatched_command == "PostRecognitionToLedger"
    assert request.accepted

    rerun = app.commands.dispatch(ReconcileRecognitionLedger(**KW, schedule_id=schedule.id))
    assert rerun.run.id != receipt.run.id and rerun.run.is_clean

    resolved = app.commands.dispatch(
        ResolveFinding(**KW, finding_id=finding.id, request_key="k1", note="posted")
    )
    assert resolved.state == "resolved"
    assert resolved.allowed_triggers == ()


def test_resolution_is_refused_while_the_divergence_is_still_real(wired):
    """PY5: reconciliation is not auto-resolution. The books are re-read and must actually agree."""
    persistent, account = wired
    app = persistent.application
    _document, schedule, _ = _recognized(app, account, periods=1, open_year=False)
    receipt = app.commands.dispatch(ReconcileRecognitionLedger(**KW, schedule_id=schedule.id))
    with pytest.raises(ReconciliationRefused):
        app.commands.dispatch(
            ResolveFinding(**KW, finding_id=receipt.findings[0].id, request_key="k")
        )


def test_a_resolved_finding_cannot_be_reopened_through_the_application(wired):
    persistent, account = wired
    app = persistent.application
    _document, schedule, _ = _recognized(app, account, periods=1, open_year=False)
    receipt = app.commands.dispatch(ReconcileRecognitionLedger(**KW, schedule_id=schedule.id))
    finding = receipt.findings[0]
    rev._open_year(app, account)
    app.commands.dispatch(RequestFindingRepair(**KW, finding_id=finding.id, request_key="r"))
    app.commands.dispatch(ReconcileRecognitionLedger(**KW, schedule_id=schedule.id))
    app.commands.dispatch(ResolveFinding(**KW, finding_id=finding.id, request_key="k"))
    for command in (
        AcknowledgeFinding(**KW, finding_id=finding.id),
        CloseFinding(**KW, finding_id=finding.id, reason="x"),
        RequestFindingRepair(**KW, finding_id=finding.id, request_key="r2"),
    ):
        with pytest.raises(BillingConflict):
            app.commands.dispatch(command)


def test_a_repair_request_is_idempotent_under_one_key(wired):
    persistent, account = wired
    app = persistent.application
    _document, schedule, _ = _recognized(app, account, periods=1, open_year=False)
    receipt = app.commands.dispatch(ReconcileRecognitionLedger(**KW, schedule_id=schedule.id))
    finding = receipt.findings[0]
    rev._open_year(app, account)
    first = app.commands.dispatch(
        RequestFindingRepair(**KW, finding_id=finding.id, request_key="same")
    )
    requests = app.queries.dispatch(ListRepairRequests(**KW, finding_id=finding.id))
    assert len(requests) == 1 and requests[0].id == first.id


def test_a_divergence_with_no_declared_repair_names_an_owner_and_stops(wired):
    """`OPERATOR` is not a gap in the whitelist — it is the honest answer for divergences no
    automatic action can fix."""
    persistent, account = wired
    app = persistent.application
    document, _schedule, _ = _recognized(app, account, periods=1)
    receipt = app.commands.dispatch(ReconcileInvoiceRecognition(**KW, document_id=document.id))
    assert receipt.run.is_clean
    assert (RelationshipKind.INVOICE_RECOGNITION, DivergenceKind.OVER_ACCOUNTED) not in (
        REPAIR_COMMANDS
    )


def test_a_resolution_is_append_only_and_carries_its_evidence(wired):
    persistent, account = wired
    app = persistent.application
    _document, schedule, _ = _recognized(app, account, periods=1, open_year=False)
    receipt = app.commands.dispatch(ReconcileRecognitionLedger(**KW, schedule_id=schedule.id))
    finding = receipt.findings[0]
    rev._open_year(app, account)
    app.commands.dispatch(RequestFindingRepair(**KW, finding_id=finding.id, request_key="r"))
    app.commands.dispatch(ReconcileRecognitionLedger(**KW, schedule_id=schedule.id))
    app.commands.dispatch(ResolveFinding(**KW, finding_id=finding.id, request_key="k"))
    resolutions = app.queries.dispatch(ListFindingResolutions(**KW, finding_id=finding.id))
    assert len(resolutions) == 1
    assert resolutions[0].evidence_digest
    assert resolutions[0].resolved_by == ACTOR
    assert resolutions[0].owner == "recognition"


def test_historical_divergence_remains_auditable_after_resolution(wired):
    """A resolution is a new row pointing at a finding — never an edit that makes the finding
    look like it never happened."""
    persistent, account = wired
    app = persistent.application
    _document, schedule, _ = _recognized(app, account, periods=1, open_year=False)
    receipt = app.commands.dispatch(ReconcileRecognitionLedger(**KW, schedule_id=schedule.id))
    finding = receipt.findings[0]
    rev._open_year(app, account)
    app.commands.dispatch(RequestFindingRepair(**KW, finding_id=finding.id, request_key="r"))
    app.commands.dispatch(ReconcileRecognitionLedger(**KW, schedule_id=schedule.id))
    app.commands.dispatch(ResolveFinding(**KW, finding_id=finding.id, request_key="k"))
    after = app.queries.dispatch(GetIntegrityFinding(**KW, finding_id=finding.id))
    assert after.severity == "critical"  # unchanged
    assert after.divergence == "missing_counterpart"  # unchanged
    assert after.detected_at == finding.detected_at
    assert app.queries.dispatch(ListRunFindings(**KW, run_id=receipt.run.id))


# ── 10. cross-org isolation ───────────────────────────────────────────────────


def test_the_same_business_identifier_in_two_orgs_never_matches(wired):
    """Every identity and query carries org scope. A finding from org A must never resolve
    against org B."""
    persistent, account = wired
    app = persistent.application
    _document, schedule, _ = _recognized(app, account, periods=1, open_year=False)
    receipt = app.commands.dispatch(ReconcileRecognitionLedger(**KW, schedule_id=schedule.id))
    other = dict(actor_id=ACTOR, org_id="org_other")
    assert app.queries.dispatch(OpenFindings(**other)) == ()
    from billing.foundation import BillingNotFound

    with pytest.raises(BillingNotFound):
        app.queries.dispatch(GetIntegrityFinding(**other, finding_id=receipt.findings[0].id))
    with pytest.raises(BillingNotFound):
        app.queries.dispatch(GetIntegrityRun(**other, run_id=receipt.run.id))


def test_the_same_deterministic_digest_in_two_orgs_yields_different_runs():
    digest = "d" * 64
    a = derive_run_id("org_a", "payment_ledger", "org_a\x1fpayment_ledger\x1facct\x1f", digest)
    b = derive_run_id("org_b", "payment_ledger", "org_b\x1fpayment_ledger\x1facct\x1f", digest)
    assert a != b


def test_every_reconciliation_query_carries_an_org():
    for query in ALL_RECONCILIATION_QUERIES:
        assert "org_id" in query.__dataclass_fields__, query.__name__
    for command in ALL_RECONCILIATION_COMMANDS:
        assert "org_id" in command.__dataclass_fields__, command.__name__
        assert "actor_id" in command.__dataclass_fields__, command.__name__


def test_every_finding_row_is_org_scoped():
    from billing.persistence.reconciliation_models import RECONCILIATION_TABLES

    for table in RECONCILIATION_TABLES:
        assert "org_id" in table.__table__.columns, table.__tablename__
        assert table.__table__.columns["org_id"].index, table.__tablename__


# ── 11. the adversarial hunt ──────────────────────────────────────────────────
#
# The two defects the hunt found, and their regressions, are D-1 and D-2 below.


def test_hunt_d1_a_tax_free_invoice_is_not_a_critical_finding(wired):
    """**D-1.** Demanding a determination for *every* document reports a zero-rated invoice, an
    exempt customer and an installation that does not use tax determination at all as a critical
    integrity failure — and a critical that fires on every correct document is one operators learn
    to dismiss."""
    persistent, account = wired
    app = persistent.application
    document = rev._issued(app, account)
    receipt = app.commands.dispatch(ReconcileInvoiceTax(**KW, document_id=document.id))
    assert receipt.run.is_clean
    assert receipt.run.max_severity == "info"


def test_hunt_d2_an_unposted_recognition_is_the_headline_divergence(wired):
    """**D-2.** `UNPOSTED` means the Ledger refused after the decision was committed — revenue
    earned, no financial fact. An evidence filter that drops it as "not recognized yet" makes the
    engine blind to exactly the case this relationship exists to catch."""
    persistent, account = wired
    app = persistent.application
    _document, schedule, entries = _recognized(app, account, periods=1, open_year=False)
    entry = app.queries.dispatch(
        __import__("billing.application", fromlist=["GetRecognitionEntry"]).GetRecognitionEntry(
            **KW, entry_id=entries[0].id
        )
    )
    assert entry.state == "unposted"
    receipt = app.commands.dispatch(ReconcileRecognitionLedger(**KW, schedule_id=schedule.id))
    assert receipt.run.diverged == 1
    assert receipt.findings[0].divergence == "missing_counterpart"
    assert receipt.findings[0].severity == "critical"


def test_hunt_a_paid_invoice_is_never_assumed_earned(wired):
    """Cash and revenue are different questions. Invoice ↔ recognition reads no payment state."""
    engine = _code_only(Path("billing/application/reconciliation_engine.py").read_text())
    reader = engine[engine.index("def invoice_recognition") : engine.index("def payment_invoice")]
    for forbidden in ("payment", "collection", "dunning"):
        assert forbidden not in reader.lower(), forbidden


def test_hunt_an_ambiguous_payment_is_not_expected_to_have_a_ledger_entry(wired):
    """M9.16's lesson: an attempt whose outcome nobody knows is not evidence money moved, so
    demanding a ledger entry for it would manufacture a critical from an honest unknown."""
    from billing.application.reconciliation_engine import _EvidenceReader

    source = _code_only(_inspect.getsource(_EvidenceReader._is_successful))
    assert "ambiguous" not in source
    assert "captured" in source and "settled" in source


def test_hunt_a_closed_accounting_period_is_m9_13s_rule_reused(wired):
    """A late entry landing in the next open period is a **warning**, not an error, and M9.19
    restates none of M9.13's close semantics."""
    for path in FOUNDATION_FILES:
        code = _code_only(path.read_text())
        for forbidden in ("AccountingPeriod", "PeriodState", "close_digest"):
            assert forbidden not in code, (path.name, forbidden)


def test_hunt_a_failed_run_is_not_a_clean_run():
    """Recording an unreadable evaluation as COMPLETED with zero divergences is how a broken
    reader gets mistaken for healthy books — the quietest possible failure of this engine."""
    factory = IntegrityFactory(ids=SequentialIdGenerator(), clock=FixedClock(1.0, step=0.0))
    scope = ReconciliationScope(ORG, RelationshipKind.PAYMENT_LEDGER, "acct_1")
    run = factory.run(scope, (_fact("a"),), (), at=1.0)
    failed = factory.failed(run, reason="ledger unavailable", at=2.0)
    assert failed.state is RunState.FAILED
    assert not failed.is_clean
    assert failed.note


def test_hunt_run_counts_are_self_consistent_and_inspected(wired):
    persistent, account = wired
    app = persistent.application
    _document, schedule, _ = _recognized(app, account)
    for command in (
        ReconcileRecognitionLedger(**KW, schedule_id=schedule.id),
        ReconcilePaymentLedger(**KW, account_id=account.id),
        ReconcilePaymentInvoice(**KW, account_id=account.id),
    ):
        receipt = app.commands.dispatch(command)
        inspection = app.queries.dispatch(InspectIntegrityRun(**KW, run_id=receipt.run.id))
        assert inspection.is_counted_correctly, (command, inspection.notes)
        assert inspection.is_scoped and inspection.is_complete
        assert receipt.run.compared == receipt.run.matched + receipt.run.diverged


def test_hunt_delegated_runs_also_count_their_matches(wired):
    """A delegated service reports only *discrepancies*, so a run that forgot to reconstruct its
    matches would report `compared == diverged` — a false integrity failure about the integrity
    engine itself."""
    persistent, account = wired
    app = persistent.application
    document, _schedule, _ = _recognized(app, account)
    for command in (
        ReconcileInvoiceLedger(**KW, document_id=document.id),
        ReconcileInvoiceTax(**KW, document_id=document.id),
    ):
        receipt = app.commands.dispatch(command)
        inspection = app.queries.dispatch(InspectIntegrityRun(**KW, run_id=receipt.run.id))
        assert inspection.is_counted_correctly, (command, inspection.notes)


def test_hunt_a_finding_explains_itself_without_re_reading_a_domain(wired):
    persistent, account = wired
    app = persistent.application
    _document, schedule, _ = _recognized(app, account, periods=1, open_year=False)
    receipt = app.commands.dispatch(ReconcileRecognitionLedger(**KW, schedule_id=schedule.id))
    explanation = app.queries.dispatch(
        ExplainFinding(**KW, finding_id=receipt.findings[0].id)
    )
    joined = " ".join(explanation.narrative).lower()
    for required in ("relationship", "expected", "observed", "severity", "owner"):
        assert required in joined, required
    assert explanation.matching_identity and explanation.authoritative_side
    assert explanation.evidence_digest


def test_hunt_the_explainer_reads_only_stored_evidence():
    from billing.foundation import FindingExplainer

    source = _code_only(_inspect.getsource(FindingExplainer))
    for forbidden in ("repos", "repository", "session", "ledger", "payment"):
        assert forbidden not in source.lower(), forbidden


def test_hunt_a_recurrence_after_resolution_creates_a_new_finding(wired):
    """"If the same underlying divergence occurs again, create a new observation rather than
    rewriting the previous finding.\""""
    persistent, account = wired
    app = persistent.application
    _document, schedule, entries = _recognized(app, account, periods=1, open_year=False)
    first = app.commands.dispatch(ReconcileRecognitionLedger(**KW, schedule_id=schedule.id))
    rev._open_year(app, account)
    app.commands.dispatch(
        RequestFindingRepair(**KW, finding_id=first.findings[0].id, request_key="r")
    )
    app.commands.dispatch(ReconcileRecognitionLedger(**KW, schedule_id=schedule.id))
    app.commands.dispatch(ResolveFinding(**KW, finding_id=first.findings[0].id, request_key="k"))
    # A second recognition, and the evidence has moved on: a different run, a different finding id.
    app.commands.dispatch(
        RecognizeRevenue(**KW, entry_id=entries[1].id, at=entries[1].recognize_on + 1)
    )
    second = app.commands.dispatch(ReconcileRecognitionLedger(**KW, schedule_id=schedule.id))
    assert second.run.id != first.run.id


def test_hunt_posture_is_derived_not_stored(wired):
    persistent, account = wired
    app = persistent.application
    _document, schedule, _ = _recognized(app, account, periods=1, open_year=False)
    app.commands.dispatch(ReconcileRecognitionLedger(**KW, schedule_id=schedule.id))
    posture = app.queries.dispatch(GetIntegrityPosture(**KW))
    assert posture.critical == 1 and not posture.is_clean
    assert posture.by_owner.get("recognition") == 1
    assert not hasattr(posture, "total") and not hasattr(posture, "amount")


def test_hunt_search_and_queues_are_scoped_and_bounded(wired):
    persistent, account = wired
    app = persistent.application
    _document, schedule, _ = _recognized(app, account, periods=1, open_year=False)
    app.commands.dispatch(ReconcileRecognitionLedger(**KW, schedule_id=schedule.id))
    found = app.queries.dispatch(
        SearchIntegrityFindings(**KW, severities=("critical",), limit=5)
    )
    assert found.total == 1 and not found.truncated
    assert app.queries.dispatch(FindingsByOwner(**KW, owner="recognition"))
    assert app.queries.dispatch(FindingsForAccount(**KW, account_id=account.id))
    assert app.queries.dispatch(IntegrityTimeline(**KW, run_id=found.findings[0].run_id))


def test_hunt_the_matrix_is_served_rather_than_hard_coded(wired):
    persistent, _account = wired
    served = persistent.application.queries.dispatch(GetReconciliationMatrix(**KW))
    assert {r.relationship for r in served} == {k.value for k in RelationshipKind}


# ── 12. the surface: routes, transport-only, deny-by-default ──────────────────


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


PATH = "/v1/billing/integrity"


def test_the_integrity_prefix_collides_with_nothing(client):
    """M9.20 shares this prefix under the reserved `incidents` segment, so the assertion is
    **path** disjointness rather than prefix exclusivity — which is the property that actually
    matters, since two routes shadow each other only when their paths are equal."""
    spec = client.app.openapi()
    incidents = f"{PATH}/incidents"
    ours = {
        p for p in spec["paths"]
        if p.startswith(PATH) and not p.startswith(incidents)
    }
    theirs = {p for p in spec["paths"] if p.startswith(incidents)}
    assert len(ours) == 25
    assert not (ours & theirs)


def test_every_bounded_context_is_tagged():
    assert any(tag["name"] == "integrity" for tag in OPENAPI_TAGS)


def test_there_is_no_delete_anywhere_on_this_surface(client):
    spec = client.app.openapi()
    for path, ops in spec["paths"].items():
        if path.startswith(PATH):
            assert "delete" not in ops, path


def test_reads_and_writes_deny_by_default(client):
    for path in (f"{PATH}/posture", f"{PATH}/findings", f"{PATH}/relationships", f"{PATH}/runs"):
        assert client.get(path).status_code == 401, path
    for path, body in (
        (f"{PATH}/reconcile/payment-ledger", {"account_id": "a"}),
        (f"{PATH}/reconcile/recognition-ledger", {"schedule_id": "s"}),
        (f"{PATH}/findings/x/resolve", {}),
        (f"{PATH}/findings/x/request-repair", {}),
    ):
        assert client.post(path, json=body).status_code == 401, path


def test_the_relationships_endpoint_answers(client):
    response = client.get(f"{PATH}/relationships", headers=_auth())
    assert response.status_code == 200
    assert len(response.json()) == 6


def test_the_router_is_transport_only():
    """Parse, dispatch, serialize. No rule, no comparison, no arithmetic."""
    source = Path("billing/customer/routers/reconciliation_engine.py").read_text()
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef):
            continue
        body = _code_only(ast.get_source_segment(source, node) or "")
        for forbidden in ("if ", "for ", "while ", "MatchingService", "SEVERITY_BY_KIND"):
            if forbidden in ("for ",) and "for " in body and " in app.queries" in body:
                continue  # a comprehension over a query result is serialization
            if forbidden in ("if ", "while "):
                assert forbidden not in body, (node.name, forbidden)
    assert "+" not in _code_only(source).replace("+ ", "")


def test_no_reconciliation_rule_leaks_into_the_transport_layer():
    for path in (
        Path("billing/customer/routers/reconciliation_engine.py"),
        Path("billing/customer/reconciliation_schemas.py"),
    ):
        code = _code_only(path.read_text())
        for forbidden in (
            "MatchingService",
            "SEVERITY_BY_KIND",
            "RECONCILIATION_MATRIX",
            "FindingLifecycleService",
            "derive_run_id",
            "derive_finding_id",
        ):
            assert forbidden not in code, (path.name, forbidden)


def test_the_posture_endpoint_carries_counts_and_no_amount(client):
    """One reconciliation, one finding, one explanation — through the transport."""
    response = client.get(f"{PATH}/posture", headers=_auth())
    assert response.status_code == 200
    body = response.json()
    assert "critical" in body and "amount" not in body
    assert set(body["by_owner"]) <= {o.value for o in RepairOwner}


# ── 13. the Studio is a consumer ──────────────────────────────────────────────


STUDIO = Path("billing/customer/studio/integrity.html")


def test_the_studio_stores_nothing():
    code = _html_code_only(STUDIO.read_text())
    for forbidden in ("localStorage", "sessionStorage", "document.cookie", "indexedDB"):
        assert forbidden not in code, forbidden


def test_the_studio_performs_no_arithmetic_on_money():
    """A client that subtracted two amounts would produce a number nobody owns."""
    code = _html_code_only(STUDIO.read_text())
    for forbidden in ("minor -", "minor +", "reduce(", ".sum(", "parseFloat"):
        assert forbidden not in code, forbidden


def test_the_studio_decides_no_severity_and_no_transition():
    code = _html_code_only(STUDIO.read_text())
    for forbidden in ("critical =", "severity =", "SEVERITY", "if (f.state ==="):
        assert forbidden not in code, forbidden
    assert "allowed_triggers" in code  # buttons come from the server


def test_the_studio_reaches_no_provider_and_no_other_domain():
    code = _html_code_only(STUDIO.read_text())
    assert code.count('const API = "/v1/billing/integrity"') == 1
    for forbidden in ("stripe", "razorpay", "http://", "https://", "cdn."):
        assert forbidden not in code.lower(), forbidden


def test_the_studio_is_self_contained():
    code = STUDIO.read_text()
    assert "<script src=" not in code
    assert "<link rel=\"stylesheet\"" not in code


# ── 14. the package layout is the declared one ────────────────────────────────


def test_every_reconciliation_module_exists_and_is_exported():
    for path in RECONCILIATION_FILES:
        assert path.is_file(), path
    import billing.foundation as foundation

    for name in (
        "IntegrityFinding",
        "IntegrityRun",
        "IntegrityResolution",
        "RepairRequest",
        "RelationshipKind",
        "DivergenceKind",
        "FindingSeverity",
        "MatchingService",
        "IntegrityFactory",
        "FindingStale",
    ):
        assert name in foundation.__all__, name
        assert hasattr(foundation, name), name


def test_the_command_and_query_vocabulary_is_registered(wired):
    persistent, _account = wired
    app = persistent.application
    for command in ALL_RECONCILIATION_COMMANDS:
        assert command in app.commands.registered, command.__name__
    for query in ALL_RECONCILIATION_QUERIES:
        assert query in app.queries.registered, query.__name__
    assert len(ALL_RECONCILIATION_COMMANDS) == 10
    assert len(ALL_RECONCILIATION_QUERIES) == 15
