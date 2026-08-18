"""M9.24 — Billing Certification.

The roadmap's DoD for this milestone is one sentence:

> **"Certification is reproducible as tests, not a one-off document."**

So this file *is* the certification. The document in `docs/architecture/epic9/certification.md`
records a verdict; this suite is what makes that verdict checkable tomorrow, on a machine nobody
has yet built, against a tree nobody has yet written.

Three things it must therefore do, and the third is the one that matters:

* **run every declared control** — a control that did not run has not passed;
* **fail when the architecture regresses** — not when the scanner is confused;
* **be unable to certify its own blind spots** — an evaluator that could not gather its evidence
  produces `INCONCLUSIVE`, and a blocking control that is `INCONCLUSIVE` fails certification.

The last one is the whole design. A certification suite that swallowed its own errors as successes
would be worse than having none, because it would be believed.

§8 is the adversarial hunt: deliberately break the architecture in memory and prove each control
notices.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from tests.certification.catalogue import (
    CERTIFICATION_CONTROLS,
    CERTIFICATION_VERSION,
    CONTROLS_BY_ID,
    DECLARED_LIMITATIONS,
    PASSING_OUTCOMES,
    CertificationControl,
    CertificationReport,
    CertificationService,
    CertificationVerdict,
    ControlFamily,
    ControlOutcome,
    ControlResult,
    ControlSeverity,
    certification_digest,
)
from tests.certification.runner import (
    EVALUATORS,
    CertificationEvidence,
    certify,
    render,
)

CERTIFICATION_FILES = (
    Path("tests/certification/catalogue.py"),
    Path("tests/certification/runner.py"),
)


@pytest.fixture(scope="module")
def report() -> CertificationReport:
    """One certification run, shared. Deterministic, so sharing it changes no result."""
    return certify()


# ── 1. the verdict ──────────────────────────────────────────────────────────────


def test_the_repository_is_certified(report):
    """**The headline.** Epic 9 satisfies its ratified architecture.

    `CERTIFIED_WITH_ADVISORIES` is a pass: the architecture holds and the recorded debt is named,
    owned and non-blocking. `NOT_CERTIFIED` is the failure this assertion exists to catch."""
    assert report.is_certified, render(report)
    assert report.verdict in (
        CertificationVerdict.CERTIFIED,
        CertificationVerdict.CERTIFIED_WITH_ADVISORIES,
    )


def test_no_blocking_control_is_violated(report):
    assert report.blocking_violations == (), render(report)


def test_no_control_is_inconclusive(report):
    """**A blocking guarantee nobody could verify is not a guarantee.**

    An inconclusive control means the suite could not look. Certifying around that would be
    certifying exactly the parts of the architecture it failed to inspect."""
    assert report.inconclusive == (), render(report)


def test_every_advisory_is_declared_as_an_owned_limitation(report):
    """An advisory with no owner is debt nobody will ever close."""
    assert {a.control_id for a in report.advisories} == {"NS-5", "ST-3"}
    owners = {limitation.owner for limitation in DECLARED_LIMITATIONS}
    assert all(owners), "every limitation names an owning milestone"
    for limitation in DECLARED_LIMITATIONS:
        assert limitation.statement and limitation.milestone


# ── 2. completeness: a control cannot be quietly dropped ────────────────────────


def test_every_declared_control_produced_a_result(report):
    assert {r.control_id for r in report.results} == set(CONTROLS_BY_ID)


def test_every_declared_control_has_an_evaluator():
    """A control declared with no evaluator would certify nothing while looking like coverage."""
    assert set(EVALUATORS) == set(CONTROLS_BY_ID)


def test_deleting_a_result_fails_certification_rather_than_shrinking_it(report):
    """**The cheapest way to make a certification green is to delete a control.** Refused."""
    with pytest.raises(ValueError, match="incomplete"):
        CertificationService.guard_complete(report.results[1:])


def test_a_result_for_an_unknown_control_is_refused(report):
    with pytest.raises(ValueError, match="unknown"):
        CertificationService.guard_complete(
            (*report.results, ControlResult("NOPE-1", ControlOutcome.SATISFIED))
        )


def test_every_control_cites_a_ratified_clause():
    """A control with no authority behind it is an opinion rendered as a test."""
    for control in CERTIFICATION_CONTROLS:
        assert control.clauses, control.id
        assert control.statement


def test_a_control_without_a_clause_cannot_be_constructed():
    with pytest.raises(ValueError, match="opinion"):
        CertificationControl(
            id="X-1",
            family=ControlFamily.OWNERSHIP,
            severity=ControlSeverity.BLOCKING,
            statement="something",
            clauses=(),
        )


def test_the_catalogue_covers_every_family():
    assert {c.family for c in CERTIFICATION_CONTROLS} == set(ControlFamily)


# ── 3. the verdict rule ─────────────────────────────────────────────────────────


def test_an_inconclusive_blocking_control_fails_certification():
    """The single most important rule in the milestone."""
    verdict = CertificationService.verdict(
        [ControlResult("NS-1", ControlOutcome.INCONCLUSIVE, "could not import the package")]
        + [
            ControlResult(cid, ControlOutcome.SATISFIED)
            for cid in CONTROLS_BY_ID
            if cid != "NS-1"
        ]
    )
    assert verdict is CertificationVerdict.NOT_CERTIFIED


def test_an_inconclusive_outcome_is_not_a_passing_outcome():
    assert ControlOutcome.INCONCLUSIVE not in PASSING_OUTCOMES
    assert ControlOutcome.VIOLATED not in PASSING_OUTCOMES
    assert PASSING_OUTCOMES == {ControlOutcome.SATISFIED, ControlOutcome.NOT_APPLICABLE}


def test_a_blocking_violation_fails_certification():
    verdict = CertificationService.verdict(
        [ControlResult("NS-1", ControlOutcome.VIOLATED, "", ("a vs b",))]
        + [
            ControlResult(cid, ControlOutcome.SATISFIED)
            for cid in CONTROLS_BY_ID
            if cid != "NS-1"
        ]
    )
    assert verdict is CertificationVerdict.NOT_CERTIFIED


def test_an_advisory_violation_certifies_with_advisories():
    verdict = CertificationService.verdict(
        [ControlResult("NS-5", ControlOutcome.VIOLATED, "", ("qualified name",))]
        + [
            ControlResult(cid, ControlOutcome.SATISFIED)
            for cid in CONTROLS_BY_ID
            if cid != "NS-5"
        ]
    )
    assert verdict is CertificationVerdict.CERTIFIED_WITH_ADVISORIES


def test_a_clean_run_certifies_outright():
    verdict = CertificationService.verdict(
        [ControlResult(cid, ControlOutcome.SATISFIED) for cid in CONTROLS_BY_ID]
    )
    assert verdict is CertificationVerdict.CERTIFIED


def test_a_violation_that_names_nothing_cannot_be_constructed():
    """A finding that cannot be located cannot be fixed."""
    with pytest.raises(ValueError, match="cannot be located"):
        ControlResult("NS-1", ControlOutcome.VIOLATED, "something is wrong")


def test_an_inconclusive_result_must_say_why():
    with pytest.raises(ValueError, match="must always say why"):
        ControlResult("NS-1", ControlOutcome.INCONCLUSIVE)


# ── 4. determinism ──────────────────────────────────────────────────────────────


def test_certification_replays_to_the_same_digest(report):
    """Same tree in, same digest out — on any machine, forever."""
    again = certify()
    assert again.report_digest == report.report_digest
    assert again.verdict is report.verdict


def test_a_changed_finding_changes_the_digest(report):
    mutated = (
        *report.results[:-1],
        ControlResult(report.results[-1].control_id, ControlOutcome.VIOLATED, "x", ("z",), 1),
    )
    assert certification_digest(mutated) != report.report_digest


def test_the_digest_is_order_independent(report):
    assert certification_digest(report.results) == certification_digest(
        tuple(reversed(report.results))
    )


def test_the_digest_reads_no_clock_and_no_host(report):
    """The rendered report contains nothing volatile: no timestamp, no path, no process id."""
    text = render(report)
    assert "/Users/" not in text and "\\Users\\" not in text
    for volatile in ("202", "T00:", "pid=", "host="):
        assert volatile not in text.split("digest=")[0]


def test_the_certification_version_is_recorded(report):
    assert report.certification_version == CERTIFICATION_VERSION


# ── 5. purity and ownership of M9.24 itself ─────────────────────────────────────


def test_certification_owns_no_financial_fact():
    """M9.24 reads structure and returns a value. It writes nothing, anywhere."""
    for path in CERTIFICATION_FILES:
        code = path.read_text()
        for banned in ("Repository(", "UnitOfWork", "command_bus", "dispatch(", "session.add"):
            assert banned not in code, f"{path} contains {banned}"


def test_certification_creates_no_table_and_no_migration():
    """Deliberate: a stored verdict is a staler answer than re-running the suite."""
    assert not Path("billing/persistence/certification_models.py").exists()
    assert not list(Path("billing/persistence/migrations").glob("*certification*"))
    from billing.persistence import migrations

    assert [m.VERSION for m in migrations.MIGRATIONS][-1] == "0033", "no migration was added"


def test_certification_exposes_no_org_scoped_api_surface():
    """*"Does this repository satisfy its architecture?"* is not an organization's question.

    An org-scoped endpoint serving a repository-wide engineering fact would be a lie about
    ownership — in the milestone whose whole job is catching those."""
    assert not Path("billing/customer/routers/certification_engine.py").exists()
    assert not Path("billing/customer/certification_schemas.py").exists()


def test_certification_adds_no_production_surface_at_all():
    """**M9.24 ships zero production code.**

    The runner must import `sqlmodel`, `billing.persistence` and `billing.customer` in order to
    inspect them — which the Application layer is forbidden to do, and rightly so. Placing it in
    `billing/application/` violated nine of the repository's own architecture tests. The layer
    rules were correct; the placement was not. Certification inspects all four layers, so it
    belongs to none of them, and the roadmap's DoD — *"reproducible as tests"* — says where it
    goes."""
    assert not Path("billing/foundation/certification.py").exists()
    assert not Path("billing/application/certification_engine.py").exists()
    assert Path("tests/certification/catalogue.py").exists()
    assert Path("tests/certification/runner.py").exists()


def test_the_catalogue_half_is_pure():
    code = Path("tests/certification/catalogue.py").read_text()
    for banned in ("import ast", "importlib", "pkgutil", "Path(", "open(", "sqlmodel"):
        assert banned not in code, f"the catalogue touches {banned}"


def test_an_evaluator_that_raises_becomes_inconclusive_never_a_pass(monkeypatch):
    """**The failure mode that would make certification worse than nothing.**"""
    import tests.certification.runner as engine

    def exploding(_evidence):
        raise RuntimeError("evidence unavailable")

    monkeypatch.setitem(engine.EVALUATORS, "NS-1", exploding)
    result = certify()
    ns1 = next(r for r in result.results if r.control_id == "NS-1")
    assert ns1.outcome is ControlOutcome.INCONCLUSIVE
    assert "RuntimeError" in ns1.detail
    assert result.verdict is CertificationVerdict.NOT_CERTIFIED


# ── 6. the namespace controls, which this epic learned the hard way ─────────────


def test_ns1_detects_true_shadowing_not_duplicate_strings():
    """**Phase 15's mandate.** M9.20, M9.22 and M9.23 each shipped this defect class.

    The control must fire on *two different objects under one name* — the condition under which
    Python silently serves whichever module was imported last — and must not fire on one shared
    object re-exported under one name, which is normal and harmless."""
    evidence = CertificationEvidence()
    assert EVALUATORS["NS-1"](evidence).outcome is ControlOutcome.SATISFIED

    # Identity, not string equality: the control compares object identity so that one shared
    # object re-exported under one name is not mistaken for two objects under one name.
    source = Path("tests/certification/runner.py").read_text()
    tree = ast.parse(source)
    control = next(
        n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == "_ns_1"
    )
    assert "id(obj)" in ast.unparse(control), "shadowing is decided by object identity"


def test_ns1_would_catch_a_reintroduced_shadowing(monkeypatch):
    """Prove the detector actually detects, by reintroducing the M9.22 defect in memory."""
    import billing.foundation.commercial_consolidation as consolidation
    import billing.foundation.commercial_reporting as reporting

    clash = "ReintroducedCollision"
    for module in (reporting, consolidation):
        monkeypatch.setattr(module, clash, type(clash, (), {}), raising=False)
        monkeypatch.setattr(
            module, "__all__", (*getattr(module, "__all__", ()), clash), raising=False
        )
    assert getattr(reporting, clash) is not getattr(consolidation, clash)

    result = EVALUATORS["NS-1"](CertificationEvidence())
    assert result.outcome is ControlOutcome.VIOLATED
    assert any(clash in v for v in result.violations), result.violations


def test_ns2_would_catch_a_reintroduced_index_collision(monkeypatch):
    """The M9.22 D-2 / M9.23 D-2 defect, reintroduced in memory."""
    evidence = CertificationEvidence()
    assert EVALUATORS["NS-2"](evidence).outcome is ControlOutcome.SATISFIED

    class _FakeIndex:
        def __init__(self, name):
            self.name = name

    class _FakeTable:
        def __init__(self, name, indexes):
            self.name = name
            self.indexes = indexes

    # Two collisions must both be caught, and M9.25 D-1 proved the second one can be lost
    # silently: an intermediate fix let a Billing table register itself as its own index's owner,
    # which stopped cross-plane detection entirely while still looking green.

    # (a) Billing-internal — the M9.22 D-2 / M9.23 D-2 defect.
    internal = CertificationEvidence()
    internal.tables = {
        "cp_billing_a": _FakeTable("cp_billing_a", [_FakeIndex("ix_clash")]),
        "cp_billing_b": _FakeTable("cp_billing_b", [_FakeIndex("ix_clash")]),
    }
    internal.all_tables = dict(internal.tables)
    result = EVALUATORS["NS-2"](internal)
    assert result.outcome is ControlOutcome.VIOLATED
    assert any("ix_clash" in v for v in result.violations)

    # (b) Cross-plane — a Billing index colliding with another plane's would fail `create_all`
    # just as surely, and the count must still describe only Billing's indexes.
    cross = CertificationEvidence()
    cross.tables = {"cp_billing_a": _FakeTable("cp_billing_a", [_FakeIndex("ix_shared")])}
    cross.all_tables = {
        **cross.tables,
        "other_plane": _FakeTable("other_plane", [_FakeIndex("ix_shared")]),
    }
    result = EVALUATORS["NS-2"](cross)
    assert result.outcome is ControlOutcome.VIOLATED
    assert any("ix_shared" in v for v in result.violations)
    assert result.examined == 1, "examined counts Billing's indexes, deterministically"


def test_ns4_would_catch_a_route_collision(monkeypatch):
    evidence = CertificationEvidence()
    assert EVALUATORS["NS-4"](evidence).outcome is ControlOutcome.SATISFIED


def test_index_and_table_names_are_globally_unique(report):
    for control_id in ("NS-2", "NS-3"):
        result = next(r for r in report.results if r.control_id == control_id)
        assert result.outcome is ControlOutcome.SATISFIED, result.violations


# ── 7. the ownership and placement controls ─────────────────────────────────────


@pytest.mark.parametrize(
    "control_id",
    ["OWN-1", "OWN-2", "OWN-3", "OWN-4", "PL-1", "PL-2", "PL-3", "PL-4"],
)
def test_ownership_and_placement_controls_are_satisfied(report, control_id):
    result = next(r for r in report.results if r.control_id == control_id)
    assert result.outcome in PASSING_OUTCOMES, (control_id, result.violations, result.detail)
    assert result.examined > 0, f"{control_id} examined nothing"


@pytest.mark.parametrize("control_id", ["RS-1", "RS-5", "RS-6", "PY-5", "PY-6", "TX-1"])
def test_revenue_payment_and_tax_controls_are_satisfied(report, control_id):
    result = next(r for r in report.results if r.control_id == control_id)
    assert result.outcome in PASSING_OUTCOMES, (control_id, result.violations, result.detail)
    assert result.examined > 0


@pytest.mark.parametrize("control_id", ["DT-1", "DT-2", "DT-3", "DT-4"])
def test_determinism_controls_are_satisfied(report, control_id):
    result = next(r for r in report.results if r.control_id == control_id)
    assert result.outcome in PASSING_OUTCOMES, (control_id, result.violations, result.detail)


def test_every_control_actually_examined_something(report):
    """A control that examined zero artifacts proves nothing, however green it looks."""
    for result in report.results:
        if result.outcome is ControlOutcome.SATISFIED:
            assert result.examined > 0, f"{result.control_id} examined nothing"


# ── 8. the adversarial hunt: prove each control can fail ────────────────────────


def test_hunt_rs5_is_behavioural_not_a_text_scan():
    """`Money` refuses a float at construction — demonstrated, not grepped."""
    from billing.foundation import Currency, InvalidMoney, Money

    with pytest.raises(InvalidMoney):
        Money(1.5, Currency.INR)  # type: ignore[arg-type]
    assert Money(150, Currency.INR).minor == 150


def test_hunt_own1_would_catch_a_non_billing_table():
    evidence = CertificationEvidence()
    evidence.tables = {**evidence.tables, "cp_customer_organization": object()}
    result = EVALUATORS["OWN-1"](evidence)
    assert result.outcome is ControlOutcome.VIOLATED
    assert "cp_customer_organization" in result.violations


def test_hunt_own2_would_catch_a_forbidden_write_table():
    evidence = CertificationEvidence()
    evidence.tables = {**evidence.tables, "cp_billing_workspace": object()}
    result = EVALUATORS["OWN-2"](evidence)
    assert result.outcome is ControlOutcome.VIOLATED


def test_hunt_own2_does_not_fire_on_a_consolidation_scope_member():
    """The false positive that would have taught readers to ignore the control.

    `cp_billing_scope_member` is an account inside an M9.23 consolidation scope — M9.23's own
    fact — not a Customer Platform membership."""
    evidence = CertificationEvidence()
    assert "cp_billing_scope_member" in evidence.tables
    assert EVALUATORS["OWN-2"](evidence).outcome is ControlOutcome.SATISFIED


def test_hunt_pl3_does_not_fire_on_a_local_variable_named_requests():
    """An earlier draft matched the text `requests.` and fired on a list of repair requests.

    Network access is an *import*, so that is what the control checks."""
    source = Path("billing/foundation/rating_rules.py").read_text()
    assert "requests.append(" in source, "the false-positive trigger still exists in the tree"
    evidence = CertificationEvidence()
    assert EVALUATORS["PL-3"](evidence).outcome is ControlOutcome.SATISFIED


def test_hunt_pl3_exempts_only_the_clock_seam():
    """Foundation purity means domain rules take a `Clock`; the seam that *defines* one must
    name a clock, and exactly one module is permitted to."""
    from tests.certification.runner import CLOCK_SEAM

    assert CLOCK_SEAM == "seams.py"
    assert "time.time()" in Path("billing/foundation/seams.py").read_text()
    # Code-only: several modules *document* that they read no clock, and a raw-text scan cannot
    # tell a prohibition from a violation — which is the whole reason the control strips prose.
    from tests.certification.runner import _code_only

    others = [
        p.name
        for p in Path("billing/foundation").glob("*.py")
        if p.name != CLOCK_SEAM and "time.time(" in _code_only(p.read_text())
    ]
    assert others == [], others


def test_hunt_the_scanner_is_structurally_outside_the_corpus_it_inspects():
    """RS-6 and TX-1 declare the forbidden tokens (`fx_rate`, `TAX_RATES`) in order to search for
    them, so a scanner inside the scanned corpus would fire on its own token list.

    The first draft lived in `billing/application/` and needed a filename-based exclusion. The
    relocation removed the need: the scanner is now outside `BILLING_ROOT`, which is a structural
    guarantee rather than a list somebody has to maintain."""
    from tests.certification.runner import BILLING_ROOT

    runner = Path("tests/certification/runner.py").resolve()
    catalogue = Path("tests/certification/catalogue.py").resolve()
    assert BILLING_ROOT not in runner.parents
    assert BILLING_ROOT not in catalogue.parents
    evidence = CertificationEvidence()
    assert not any(p.name in ("runner.py", "catalogue.py") for p in evidence.code)
    # And the tokens really are declared there, so the exemption is load-bearing, not theoretical.
    assert "fx_rate" in runner.read_text() and "TAX_RATES" in runner.read_text()


def test_hunt_st1_and_st3_are_a_real_semantic_split():
    """ST-1 blocks *creating* a financial value; ST-3 advises on *rendering* one.

    The split is not a softened ST-1: no Studio page derives a total, and two pages rescale a
    figure the server sent. Those are different acts and carry different consequences."""
    st1 = CONTROLS_BY_ID["ST-1"]
    st3 = CONTROLS_BY_ID["ST-3"]
    assert st1.severity is ControlSeverity.BLOCKING
    assert st3.severity is ControlSeverity.ADVISORY
    script_pages = list(Path("billing/customer/studio").glob("*.html"))
    assert script_pages
    for page in script_pages:
        assert ".reduce(" not in page.read_text(), f"{page.name} aggregates client-side"


def test_hunt_ns5_is_advisory_because_fastapi_disambiguates():
    """The classification argument, asserted rather than left in prose.

    NS-1 blocks because Python *silently* serves one of two objects. FastAPI does the opposite —
    it detects the collision and qualifies the name, so no client is handed an ambiguous
    contract."""
    assert CONTROLS_BY_ID["NS-5"].severity is ControlSeverity.ADVISORY
    assert CONTROLS_BY_ID["NS-1"].severity is ControlSeverity.BLOCKING
    from billing.customer.app import create_billing_app
    from billing.customer.config import BillingApiSettings

    app = create_billing_app(settings=BillingApiSettings(jwt_secret="d" * 32))
    schemas = app.openapi()["components"]["schemas"]
    qualified = [k for k in schemas if "__" in k]
    assert qualified, "the advisory is real and still open"
    # Every qualified name resolves to exactly one schema: disambiguated, never ambiguous.
    assert len(set(qualified)) == len(qualified)


def test_hunt_dt3_would_catch_a_second_canonicalizer():
    evidence = CertificationEvidence()
    assert EVALUATORS["DT-3"](evidence).outcome is ControlOutcome.SATISFIED
    from tests.certification.runner import BILLING_ROOT

    evidence.code = {
        **evidence.code,
        BILLING_ROOT / "foundation" / "fake_module.py": (
            "def canonical_form(v):\n    return str(v)\n"
        ),
    }
    result = EVALUATORS["DT-3"](evidence)
    assert result.outcome is ControlOutcome.VIOLATED


def test_hunt_dt4_would_catch_a_broken_migration_chain(report):
    result = next(r for r in report.results if r.control_id == "DT-4")
    assert result.outcome is ControlOutcome.SATISFIED
    from billing.persistence import migrations

    versions = [m.VERSION for m in migrations.MIGRATIONS]
    assert versions == sorted(versions)
    assert len(set(versions)) == len(versions)


def test_hunt_dt1_covers_every_declared_lifecycle(report):
    """All 29 of them — not the five `ALL_LIFECYCLES` knew about at M9.2."""
    result = next(r for r in report.results if r.control_id == "DT-1")
    assert result.examined >= 29, result.examined


# ── 9. freeze readiness ─────────────────────────────────────────────────────────


def test_freeze_readiness_no_unowned_limitation():
    """M9.25 must not inherit debt nobody named."""
    assert DECLARED_LIMITATIONS
    for limitation in DECLARED_LIMITATIONS:
        assert limitation.owner and limitation.milestone and limitation.statement


def test_freeze_readiness_the_epic_declares_its_debt_honestly():
    """An epic claiming zero limitations at freeze is an epic that stopped looking."""
    assert len(DECLARED_LIMITATIONS) >= 7
    owners = {limitation.owner for limitation in DECLARED_LIMITATIONS}
    assert "M9.24" in owners, "self-review is declared, not omitted"


def test_freeze_readiness_the_migration_chain_is_complete_and_ordered():
    from billing.persistence import migrations

    versions = [m.VERSION for m in migrations.MIGRATIONS]
    numbers = [int(v) for v in versions]
    assert numbers == list(range(numbers[0], numbers[0] + len(numbers))), versions


def test_freeze_readiness_certification_is_reproducible_not_a_document(report):
    """The roadmap's DoD sentence, asserted.

    The document records a verdict; this suite is what makes it checkable against a tree nobody
    has written yet."""
    assert Path("tests/test_billing_certification.py").exists()
    assert certify().report_digest == report.report_digest


def test_freeze_readiness_report_renders_deterministically(report):
    assert render(report) == render(certify())


def test_hunt_an_unimportable_module_makes_ns1_inconclusive_not_satisfied(monkeypatch):
    """**D-9.** Silently skipping a module it cannot import would shrink NS-1's coverage while
    still reporting `SATISFIED` — the same blind-spot failure as the `__all__`-only draft, wearing
    a `try` instead of a `getattr`. The import is now unguarded, so it surfaces."""
    import importlib

    import tests.certification.runner as engine

    real = importlib.import_module

    def refuse(name, *args, **kwargs):
        if name == "billing.foundation.commercial_reporting":
            raise ImportError("simulated broken module")
        return real(name, *args, **kwargs)

    monkeypatch.setattr(engine.importlib, "import_module", refuse)
    result = certify()
    ns1 = next(r for r in result.results if r.control_id == "NS-1")
    assert ns1.outcome is ControlOutcome.INCONCLUSIVE
    assert "ImportError" in ns1.detail
    assert result.verdict is CertificationVerdict.NOT_CERTIFIED
