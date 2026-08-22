"""M10.13 — the Epic 10 certification suite (ADR-0043).

The roadmap's DoD for Epic 9's certification was *"reproducible as tests, not a one-off
document."* Epic 10 inherits that standard and adds one of its own, from the Gate 3 authorization:

> Do not rely only on "the repository currently happens to satisfy the condition".

So every control is exercised **twice**: once against the real tree, where it must be `SATISFIED`,
and once against a synthetic violation, where it must be `VIOLATED` and must **name** what
offended. A guard that has only ever seen a passing repository has not been shown to guard
anything — the failure mode this file exists to prevent.

The synthetic cases are built by substituting the evidence-gathering helpers, never by mutating the
repository: nothing here writes to `billing/**`, to a migration, to an Epic 9 artifact or to the
working tree.
"""

from __future__ import annotations

import ast
import subprocess
from pathlib import Path

import pytest

from tests.certification import epic10
from tests.certification.epic10 import (
    BASELINE_COMMIT,
    BASELINE_TAG,
    EPIC9_HISTORICAL_CERTIFICATION_DIGEST,
    EPIC9_HISTORICAL_MANIFEST_DIGEST,
    EPIC10_CERTIFICATION_CONTROLS,
    EPIC10_CERTIFICATION_VERSION,
    EPIC10_CONTROLS_BY_ID,
    EPIC10_DECLARED_LIMITATIONS,
    EPIC10_EVALUATORS,
    EPIC10_PASSING_OUTCOMES,
    MB14_BILLING_EXCEPTION,
    PERMITTED_BILLING_EXCEPTIONS,
    PLATFORM_ROOT,
    REPO_ROOT,
    Epic10CertificationService,
    Epic10Control,
    Epic10ControlFamily,
    Epic10ControlOutcome,
    Epic10ControlResult,
    Epic10ControlSeverity,
    Epic10DeclaredLimitation,
    Epic10Verdict,
    GitUnavailable,
    certify_epic10,
    epic10_certification_digest,
    render_epic10,
)

EPIC10_MODULE = Path(epic10.__file__)

#: Epic 9's control-family prefixes. Epic 10's namespace must be disjoint from every one of them.
EPIC9_PREFIXES = ("DT-", "NS-", "OWN-", "PL-", "PY-", "RS-", "ST-", "TX-")

#: The **only** names Epic 10 may take from Epic 9's catalogue, and both are data (ADR-0043 CD2).
PERMITTED_EPIC9_IMPORTS = frozenset({"CONTROLS_BY_ID", "DECLARED_LIMITATIONS"})


@pytest.fixture(scope="module")
def report():
    """One certification run, shared. Deterministic, so sharing it changes no result."""
    return certify_epic10()


def _git(*args: str) -> str:
    return subprocess.run(
        ["git", *args], capture_output=True, text=True, cwd=REPO_ROOT, check=False
    ).stdout.strip()


def _violation_of(result: Epic10ControlResult) -> str:
    """Assert a synthetic case was caught, and return what the control named."""
    assert result.outcome is Epic10ControlOutcome.VIOLATED, result
    assert result.violations, "a violation that cannot be located cannot be fixed"
    return " | ".join(result.violations)


def _satisfied(result: Epic10ControlResult) -> None:
    assert result.outcome is Epic10ControlOutcome.SATISFIED, result


def _passing_results() -> list[Epic10ControlResult]:
    return [
        Epic10ControlResult(control.id, Epic10ControlOutcome.SATISFIED)
        for control in EPIC10_CERTIFICATION_CONTROLS
    ]


# ── 1. the verdict ──────────────────────────────────────────────────────────────


def test_epic10_is_certified(report):
    """**The headline.** Epic 10 stayed inside the boundaries it was authorized to work within."""
    assert report.is_certified, render_epic10(report)
    assert report.verdict is Epic10Verdict.CERTIFIED, render_epic10(report)


def test_every_control_produced_a_result(report):
    assert {r.control_id for r in report.results} == set(EPIC10_CONTROLS_BY_ID)


def test_no_control_is_inconclusive(report):
    """An unevaluated boundary is not a certified one."""
    assert report.inconclusive == (), render_epic10(report)


def test_no_blocking_violation(report):
    assert report.blocking_violations == (), render_epic10(report)


def test_the_report_renders_every_control_and_limitation(report):
    rendered = render_epic10(report)
    for control_id in EPIC10_CONTROLS_BY_ID:
        assert control_id in rendered
    for limitation in EPIC10_DECLARED_LIMITATIONS:
        assert limitation.id in rendered


# ── 2. thirteen controls, freshly identified (CV1, CV2) ─────────────────────────


def test_there_are_exactly_thirteen_controls():
    assert len(EPIC10_CERTIFICATION_CONTROLS) == 13


def test_the_control_ids_are_e10_1_through_e10_13():
    assert [c.id for c in EPIC10_CERTIFICATION_CONTROLS] == [f"E10-{n}" for n in range(1, 14)]


def test_every_control_has_an_evaluator():
    assert set(EPIC10_EVALUATORS) == set(EPIC10_CONTROLS_BY_ID)


def test_every_control_cites_a_ratified_clause():
    for control in EPIC10_CERTIFICATION_CONTROLS:
        assert control.clauses, control.id
        assert control.statement, control.id


def test_a_control_without_an_authority_is_rejected():
    with pytest.raises(ValueError, match="cites no ratified clause"):
        Epic10Control(
            id="E10-99",
            family=Epic10ControlFamily.GOVERNANCE,
            severity=Epic10ControlSeverity.BLOCKING,
            statement="an opinion",
            clauses=(),
        )


def test_the_epic10_namespace_is_disjoint_from_epic9():
    """CV2 — a fresh namespace, not a continuation of Epic 9's."""
    from tests.certification.catalogue import CONTROLS_BY_ID as EPIC9_CONTROLS

    assert set(EPIC10_CONTROLS_BY_ID) & set(EPIC9_CONTROLS) == set()
    for control_id in EPIC10_CONTROLS_BY_ID:
        assert not control_id.startswith(EPIC9_PREFIXES), control_id


def test_the_epic10_version_is_its_own():
    from tests.certification.catalogue import CERTIFICATION_VERSION

    assert EPIC10_CERTIFICATION_VERSION != CERTIFICATION_VERSION
    assert EPIC10_CERTIFICATION_VERSION.startswith("m10.13")


# ── 3. independence from Epic 9 (CV3, CV4) ──────────────────────────────────────


def test_epic9_catalogue_is_unchanged():
    """CV3 — 27 controls, nine limitations, `m9.24.1`. Epic 10 added to none of them."""
    from tests.certification.catalogue import (
        CERTIFICATION_CONTROLS,
        CERTIFICATION_VERSION,
        DECLARED_LIMITATIONS,
    )

    assert len(CERTIFICATION_CONTROLS) == 27
    assert len(DECLARED_LIMITATIONS) == 9
    assert CERTIFICATION_VERSION == "m9.24.1"


def test_epic10_never_imports_epic9s_runner():
    """CV4 — no Epic 9 evaluator can be invoked by a module that never imports the runner."""
    tree = ast.parse(EPIC10_MODULE.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            assert node.module != "tests.certification.runner", ast.dump(node)
        if isinstance(node, ast.Import):
            for alias in node.names:
                assert "certification.runner" not in alias.name, alias.name


def test_epic10_imports_only_data_from_epic9s_catalogue():
    """CD2 — the catalogue is read as evidence, never as a source of Epic 10 controls."""
    tree = ast.parse(EPIC10_MODULE.read_text(encoding="utf-8"))
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module == "tests.certification.catalogue":
            imported |= {alias.name for alias in node.names}
    assert imported == PERMITTED_EPIC9_IMPORTS, imported


def test_epic10_re_exports_no_epic9_symbol():
    """CD2 — re-exporting would make Epic 9's vocabulary Epic 10's by another route."""
    from tests.certification import catalogue

    assert set(epic10.__all__) & set(catalogue.__all__) == set()


def test_epic10_declares_no_epic9_control_identifier():
    """The catalogue must not smuggle an Epic 9 id in as a statement or clause."""
    for control in EPIC10_CERTIFICATION_CONTROLS:
        for prefix in EPIC9_PREFIXES:
            assert not control.id.startswith(prefix)


def test_the_epic10_module_lives_beside_epic9_without_touching_it():
    """CD2 — co-location is filesystem adjacency, and adjacency is not coupling."""
    assert EPIC10_MODULE.parent.name == "certification"
    assert (EPIC10_MODULE.parent / "catalogue.py").exists()
    assert (EPIC10_MODULE.parent / "runner.py").exists()


# ── 4. inconclusive never passes, and completeness cannot be shrunk ─────────────


def test_inconclusive_is_not_a_passing_outcome():
    assert Epic10ControlOutcome.INCONCLUSIVE not in EPIC10_PASSING_OUTCOMES
    assert not Epic10ControlResult(
        "E10-1", Epic10ControlOutcome.INCONCLUSIVE, detail="could not look"
    ).passed


def test_a_blocking_inconclusive_control_fails_certification():
    results = _passing_results()
    results[0] = Epic10ControlResult(
        results[0].control_id, Epic10ControlOutcome.INCONCLUSIVE, detail="could not look"
    )
    assert Epic10CertificationService.verdict(results) is Epic10Verdict.NOT_CERTIFIED


def test_a_blocking_violation_fails_certification():
    results = _passing_results()
    results[0] = Epic10ControlResult(
        results[0].control_id, Epic10ControlOutcome.VIOLATED, violations=("synthetic",)
    )
    assert Epic10CertificationService.verdict(results) is Epic10Verdict.NOT_CERTIFIED


def test_an_advisory_violation_certifies_with_advisories(monkeypatch):
    advisory = Epic10Control(
        id="E10-1",
        family=Epic10ControlFamily.GOVERNANCE,
        severity=Epic10ControlSeverity.ADVISORY,
        statement="synthetic advisory",
        clauses=("synthetic",),
    )
    patched = dict(EPIC10_CONTROLS_BY_ID) | {"E10-1": advisory}
    monkeypatch.setattr(epic10, "EPIC10_CONTROLS_BY_ID", patched)
    results = _passing_results()
    results[0] = Epic10ControlResult(
        "E10-1", Epic10ControlOutcome.VIOLATED, violations=("synthetic",)
    )
    assert Epic10CertificationService.verdict(results) is Epic10Verdict.CERTIFIED_WITH_ADVISORIES


def test_deleting_a_result_fails_certification_rather_than_shrinking_it():
    """The cheapest way to make a certification suite green, closed off."""
    with pytest.raises(ValueError, match="incomplete"):
        Epic10CertificationService.report(_passing_results()[:-1])


def test_a_result_for_an_unknown_control_is_rejected():
    results = _passing_results()
    results.append(Epic10ControlResult("E10-99", Epic10ControlOutcome.SATISFIED))
    with pytest.raises(ValueError, match="unknown control"):
        Epic10CertificationService.report(results)


def test_a_violation_must_name_what_offended():
    with pytest.raises(ValueError, match="nothing named"):
        Epic10ControlResult("E10-1", Epic10ControlOutcome.VIOLATED)


def test_an_inconclusive_result_must_say_why():
    with pytest.raises(ValueError, match="must always say why"):
        Epic10ControlResult("E10-1", Epic10ControlOutcome.INCONCLUSIVE)


def test_an_evaluator_that_raises_becomes_inconclusive_not_a_pass(monkeypatch):
    def _explode() -> Epic10ControlResult:
        raise RuntimeError("evidence unavailable")

    monkeypatch.setitem(EPIC10_EVALUATORS, "E10-8", _explode)
    result = next(r for r in certify_epic10().results if r.control_id == "E10-8")
    assert result.outcome is Epic10ControlOutcome.INCONCLUSIVE
    assert "evidence unavailable" in result.detail
    assert not result.passed


# ── 5. digest ⟂ verdict (CV16) ──────────────────────────────────────────────────


def test_the_digest_is_deterministic(report):
    assert epic10_certification_digest(report.results) == report.report_digest
    assert certify_epic10().report_digest == report.report_digest


def test_the_digest_ignores_result_order():
    results = _passing_results()
    assert epic10_certification_digest(results) == epic10_certification_digest(results[::-1])


def test_the_digest_changes_when_a_finding_changes():
    results = _passing_results()
    changed = list(results)
    changed[0] = Epic10ControlResult(
        changed[0].control_id, Epic10ControlOutcome.VIOLATED, violations=("synthetic",)
    )
    assert epic10_certification_digest(changed) != epic10_certification_digest(results)


def test_the_digest_carries_no_verdict():
    """CD8 — the same findings digest identically whatever anyone concludes from them."""
    results = _passing_results()
    digest = epic10_certification_digest(results)
    for verdict in Epic10Verdict:
        report = epic10.Epic10CertificationReport(
            verdict=verdict, results=tuple(results), report_digest=digest
        )
        assert report.report_digest == digest


def test_e10_12_detects_an_entangled_digest_and_verdict(monkeypatch, tmp_path):
    """The negative case: a module where the digest consults the verdict must be caught."""
    entangled = tmp_path / "entangled.py"
    entangled.write_text(
        "def epic10_certification_digest(results):\n"
        "    verdict = compute_verdict(results)\n"
        "    return hash((results, verdict))\n"
        "\n"
        "def verdict(results):\n"
        "    return report_digest(results)\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(epic10, "_SELF_SOURCE_PATH", entangled)
    named = _violation_of(epic10._e10_12())
    assert "verdict" in named and "digest" in named


def test_e10_12_is_inconclusive_when_the_functions_are_absent(monkeypatch, tmp_path):
    empty = tmp_path / "empty.py"
    empty.write_text("x = 1\n", encoding="utf-8")
    monkeypatch.setattr(epic10, "_SELF_SOURCE_PATH", empty)
    result = epic10._e10_12()
    assert result.outcome is Epic10ControlOutcome.INCONCLUSIVE
    assert not result.passed


# ── 6. E10-1 · the MB14 exception is exact (CV5, CV6) ───────────────────────────


def test_the_billing_exception_list_names_exactly_one_change():
    """CV6 — a closed allow-list of file, symbol and authority. Not a glob, not a waiver."""
    assert len(PERMITTED_BILLING_EXCEPTIONS) == 1
    assert MB14_BILLING_EXCEPTION.path == "platform/billing/foundation/catalog.py"
    assert MB14_BILLING_EXCEPTION.symbol == "OBSERVABILITY"
    assert MB14_BILLING_EXCEPTION.value == "observability"
    assert "MB14" in MB14_BILLING_EXCEPTION.authority
    assert "*" not in MB14_BILLING_EXCEPTION.path


def test_the_authorized_mb14_member_is_the_only_billing_delta(report):
    _satisfied(next(r for r in report.results if r.control_id == "E10-1"))


def _patch_billing(monkeypatch, changes, added=(), removed=()):
    monkeypatch.setattr(epic10, "_changed_since_baseline", lambda *p: tuple(changes))
    monkeypatch.setattr(
        epic10, "_added_and_removed_lines", lambda path: (tuple(added), tuple(removed))
    )


def test_e10_1_detects_a_second_billing_file(monkeypatch):
    _patch_billing(
        monkeypatch,
        [("M", "platform/billing/application/invoicing.py")],
    )
    assert "invoicing.py" in _violation_of(epic10._e10_1())


def test_e10_1_detects_a_second_added_member(monkeypatch):
    _patch_billing(
        monkeypatch,
        [("M", MB14_BILLING_EXCEPTION.path)],
        added=['    OBSERVABILITY = "observability"', '    SHADOW = "shadow"'],
    )
    assert "admits exactly" in _violation_of(epic10._e10_1())


def test_e10_1_detects_a_removed_line(monkeypatch):
    _patch_billing(
        monkeypatch,
        [("M", MB14_BILLING_EXCEPTION.path)],
        added=['    OBSERVABILITY = "observability"'],
        removed=['    WORKFLOW = "workflow"'],
    )
    assert "additive" in _violation_of(epic10._e10_1())


def test_e10_1_detects_a_modified_existing_member(monkeypatch):
    _patch_billing(
        monkeypatch,
        [("M", MB14_BILLING_EXCEPTION.path)],
        added=['    WORKFLOW = "workflow_v2"'],
        removed=['    WORKFLOW = "workflow"'],
    )
    assert _violation_of(epic10._e10_1())


def test_e10_1_detects_a_deleted_billing_file(monkeypatch):
    _patch_billing(monkeypatch, [("D", MB14_BILLING_EXCEPTION.path)])
    assert "modification only" in _violation_of(epic10._e10_1())


def test_e10_1_accepts_exactly_the_mb14_shape(monkeypatch):
    _patch_billing(
        monkeypatch,
        [("M", MB14_BILLING_EXCEPTION.path)],
        added=[
            "    # runtime observability (ADR-0039 Amendment 1 MB14)",
            '    OBSERVABILITY = "observability"',
        ],
    )
    _satisfied(epic10._e10_1())


def test_e10_1_is_satisfied_when_billing_is_pristine(monkeypatch):
    _patch_billing(monkeypatch, [])
    _satisfied(epic10._e10_1())


# ── 7. E10-2 · migrations (CV9) ─────────────────────────────────────────────────


def test_frozen_migrations_are_unchanged(report):
    _satisfied(next(r for r in report.results if r.control_id == "E10-2"))


def test_e10_2_detects_a_changed_frozen_migration(monkeypatch):
    monkeypatch.setattr(
        epic10,
        "_changed_since_baseline",
        lambda *p: (("M", "platform/billing/persistence/migrations/v0033_billing_x.py"),),
    )
    assert "0033" in _violation_of(epic10._e10_2())


def test_e10_2_ignores_an_epic10_migration(monkeypatch):
    monkeypatch.setattr(
        epic10,
        "_changed_since_baseline",
        lambda *p: (("A", "platform/events/persistence/migrations/v0036_outbox_retry_ladder.py"),),
    )
    _satisfied(epic10._e10_2())


def test_the_epic10_migration_chain_is_0034_to_0036():
    """CD13 — M10.13 added no migration; the chain is exactly what M10.6/M10.7 left."""
    from events.persistence import migrations

    assert tuple(m.VERSION for m in migrations.MIGRATIONS) == ("0034", "0035", "0036")


def test_m10_13_added_no_migration():
    for root in ("events", "api", "billing"):
        added = _git(
            "diff", "--name-only", "HEAD", "--", f"platform/{root}/persistence/migrations"
        )
        assert added == "", added


# ── 8. E10-3 · evt_ prefix conformance (CV10) ───────────────────────────────────


def test_every_epic10_table_and_index_is_evt_prefixed(report):
    _satisfied(next(r for r in report.results if r.control_id == "E10-3"))


def _patch_sources(monkeypatch, source: str):
    def _fake(root: Path) -> dict[Path, str]:
        return {root / "models.py": source}

    monkeypatch.setattr(epic10, "_python_sources", _fake)


def test_e10_3_detects_a_non_evt_table(monkeypatch):
    _patch_sources(
        monkeypatch,
        'class Row:\n    __tablename__ = "outbox"\n',
    )
    assert "outbox" in _violation_of(epic10._e10_3())


def test_e10_3_detects_a_non_evt_index(monkeypatch):
    _patch_sources(
        monkeypatch,
        'class Row:\n    __tablename__ = "evt_x"\n'
        '    __table_args__ = (Index("ix_outbox_state", "state"),)\n',
    )
    assert "ix_outbox_state" in _violation_of(epic10._e10_3())


def test_e10_3_is_inconclusive_when_nothing_declares_a_table(monkeypatch):
    _patch_sources(monkeypatch, "x = 1\n")
    result = epic10._e10_3()
    assert result.outcome is Epic10ControlOutcome.INCONCLUSIVE
    assert not result.passed


def test_the_placeholder_ddl_scan_reads_the_declared_index_names():
    """The regex cannot see through `CREATE INDEX IF NOT EXISTS {name}` — the binding can."""
    source = (
        'INDEXES = (("ix_evt_outbox_state_seq", "evt_outbox", "state, seq"),)\n'
        'text(f"CREATE INDEX IF NOT EXISTS {name} ON {table} ({columns})")\n'
    )
    names = epic10._created_object_names(source)
    assert "ix_evt_outbox_state_seq" in names
    assert "IF" not in names, "the keyword must never be mistaken for an object name"


def test_the_literal_ddl_scan_still_reads_a_plain_create():
    assert "evt_thing" in epic10._created_object_names('text("CREATE TABLE evt_thing (id TEXT)")')
    assert "cp_billing_thing" in epic10._created_object_names(
        'text("CREATE TABLE IF NOT EXISTS cp_billing_thing (id TEXT)")'
    )


# ── 9. E10-4 / E10-5 · the event vocabulary (CV11, CV12) ───────────────────────


def test_the_event_vocabulary_grew_additively(report):
    result = next(r for r in report.results if r.control_id == "E10-4")
    _satisfied(result)
    assert "METRIC_TURN" in result.detail


def test_e10_4_detects_a_removed_member(monkeypatch):
    monkeypatch.setattr(
        epic10,
        "_baseline_source",
        lambda path: 'class EventType:\n    GONE = "gone.forever"\n',
    )
    assert "GONE" in _violation_of(epic10._e10_4())


def test_e10_4_detects_a_changed_value(monkeypatch):
    monkeypatch.setattr(
        epic10,
        "_baseline_source",
        lambda path: 'class EventType:\n    METRIC_TURN = "metric.something_else"\n',
    )
    assert "METRIC_TURN" in _violation_of(epic10._e10_4())


def test_e10_4_is_inconclusive_when_the_baseline_is_unreadable(monkeypatch):
    monkeypatch.setattr(epic10, "_baseline_source", lambda path: "x = 1\n")
    result = epic10._e10_4()
    assert result.outcome is Epic10ControlOutcome.INCONCLUSIVE
    assert not result.passed


def test_the_metric_billing_namespace_is_unused(report):
    _satisfied(next(r for r in report.results if r.control_id == "E10-5"))


def test_e10_5_detects_a_metric_billing_type(monkeypatch):
    monkeypatch.setattr(
        epic10,
        "_enum_members",
        lambda source, name: {"BILLING_USAGE": "metric.billing.usage"},
    )
    assert "metric.billing.usage" in _violation_of(epic10._e10_5())


def test_the_enum_reader_ignores_prose():
    """AST, not regex: a member named in a docstring is not a declaration."""
    source = (
        "class EventType:\n"
        '    """Do not add METRIC_FAKE = "metric.billing.fake" here."""\n'
        '    REAL = "real.thing"\n'
    )
    assert epic10._enum_members(source, "EventType") == {"REAL": "real.thing"}


# ── 10. E10-6 · Billing subscribes to nothing (CV12) ───────────────────────────


def test_billing_holds_no_bus_subscription(report):
    _satisfied(next(r for r in report.results if r.control_id == "E10-6"))


def _patch_billing_sources(monkeypatch, source: str):
    monkeypatch.setattr(
        epic10, "_python_sources", lambda root: {PLATFORM_ROOT / "billing" / "x.py": source}
    )


def test_e10_6_detects_a_subscribe_call(monkeypatch):
    _patch_billing_sources(monkeypatch, "def wire(bus):\n    bus.subscribe(handler)\n")
    assert "subscribe" in _violation_of(epic10._e10_6())


def test_e10_6_detects_an_event_bus_import(monkeypatch):
    _patch_billing_sources(monkeypatch, "from core.events import EventBus\n")
    assert "EventBus" in _violation_of(epic10._e10_6())


def test_e10_6_does_not_fire_on_commercial_subscription_prose(monkeypatch):
    """The word appears legitimately all over Billing; only a call or a symbol is a subscription."""
    _patch_billing_sources(
        monkeypatch,
        '"""One subscribed component: what the customer subscribed to."""\n'
        "SUBSCRIBED_ITEMS = ()\n",
    )
    _satisfied(epic10._e10_6())


# ── 11. E10-7 / E10-8 / E10-9 · the frozen planes (CV13, CV14) ─────────────────


def test_no_pre_existing_api_file_was_modified(report):
    result = next(r for r in report.results if r.control_id == "E10-7")
    _satisfied(result)
    assert "api/metering" in result.detail, "the permitted additions must be named, not implied"


def test_e10_7_detects_a_modified_pre_existing_api_file(monkeypatch):
    monkeypatch.setattr(
        epic10,
        "_changed_since_baseline",
        lambda *p: (("M", "platform/api/customer/routers/observability.py"),),
    )
    assert "modified" in _violation_of(epic10._e10_7())


def test_e10_7_detects_a_deleted_api_file(monkeypatch):
    monkeypatch.setattr(
        epic10, "_changed_since_baseline", lambda *p: (("D", "platform/api/foundation/x.py"),)
    )
    assert "deleted" in _violation_of(epic10._e10_7())


def test_e10_7_permits_an_addition(monkeypatch):
    """CD7 — a file that did not exist at v4.0.0 cannot be pre-existing."""
    monkeypatch.setattr(
        epic10, "_changed_since_baseline", lambda *p: (("A", "platform/api/metering/new.py"),)
    )
    _satisfied(epic10._e10_7())


def test_the_replay_plane_is_untouched(report):
    _satisfied(next(r for r in report.results if r.control_id == "E10-8"))


def test_e10_8_detects_any_replay_change(monkeypatch):
    monkeypatch.setattr(
        epic10, "_changed_since_baseline", lambda *p: (("A", "platform/replay/new.py"),)
    )
    assert "replay" in _violation_of(epic10._e10_8())


def test_the_observability_files_are_untouched(report):
    _satisfied(next(r for r in report.results if r.control_id == "E10-9"))


def test_e10_9_detects_an_observability_change(monkeypatch):
    monkeypatch.setattr(
        epic10,
        "_changed_since_baseline",
        lambda *p: (("M", "platform/api/foundation/observability.py"),),
    )
    assert "observability" in _violation_of(epic10._e10_9())


# ── 12. E10-10 · the historical digests (CV7, CV8) ─────────────────────────────


def test_the_historical_digests_are_reproduced_from_the_baseline(report):
    result = next(r for r in report.results if r.control_id == "E10-10")
    _satisfied(result)
    assert EPIC9_HISTORICAL_CERTIFICATION_DIGEST[:16] in result.detail


def test_the_historical_digests_come_from_v4_0_0_and_nowhere_else():
    """CV7 — read straight out of the baseline tree, not out of the current file."""
    doc = _git("show", f"{BASELINE_TAG}:docs/releases/epic9-billing-platform-v1.0.md")
    assert EPIC9_HISTORICAL_MANIFEST_DIGEST in doc
    assert EPIC9_HISTORICAL_CERTIFICATION_DIGEST in doc


def test_the_current_release_record_carries_the_post_m10_10_values():
    """CD6's two-state distinction: the current values are different, and that is authorized."""
    current = (REPO_ROOT / "docs/releases/epic9-billing-platform-v1.0.md").read_text()
    assert EPIC9_HISTORICAL_MANIFEST_DIGEST not in current
    assert EPIC9_HISTORICAL_CERTIFICATION_DIGEST not in current
    assert "cdafb4ab6cba00bcaed0c9b6a3ae477253ce66e492b5b743c9835072704d1fb7" in current
    assert "c044b3f6a23066df4ed97c8b7d0992d8419490ed68dd1b3633b22717f7cab6e6" in current


def test_e10_10_detects_a_historical_digest_that_does_not_reproduce(monkeypatch):
    monkeypatch.setattr(
        epic10,
        "_baseline_source",
        lambda path: (
            f"**Manifest digest:** `{'0' * 64}`\n**Certification digest:** `{'1' * 64}`\n"
        ),
    )
    assert "historical" in _violation_of(epic10._e10_10())


def test_e10_10_detects_an_epic9_artifact_modified_by_m10_13(monkeypatch):
    monkeypatch.setattr(
        epic10,
        "_changed_since_head",
        lambda *p: ("platform/tests/certification/catalogue.py",),
    )
    assert "catalogue.py" in _violation_of(epic10._e10_10())


def test_e10_10_is_inconclusive_when_the_baseline_record_is_unreadable(monkeypatch):
    monkeypatch.setattr(epic10, "_baseline_source", lambda path: "no digests here\n")
    result = epic10._e10_10()
    assert result.outcome is Epic10ControlOutcome.INCONCLUSIVE
    assert not result.passed


def test_the_baseline_tag_still_names_the_release_commit():
    """CV24 — pinned as a literal; a value read from the tag cannot detect the tag moving."""
    assert _git("rev-parse", f"{BASELINE_TAG}^{{commit}}") == BASELINE_COMMIT


def test_a_moved_baseline_is_inconclusive_not_a_pass(monkeypatch):
    monkeypatch.setattr(epic10, "_git", lambda *args: "0" * 40)
    with pytest.raises(GitUnavailable, match="pinned baseline"):
        epic10._require_baseline()


# ── 13. E10-11 · Epic 9's advisories and limitations (CV15) ────────────────────


def test_epic9s_advisories_and_limitations_are_still_declared(report):
    _satisfied(next(r for r in report.results if r.control_id == "E10-11"))


def test_epic9_still_declares_nine_owned_limitations():
    from tests.certification.catalogue import DECLARED_LIMITATIONS

    assert len(DECLARED_LIMITATIONS) == 9
    assert all(limitation.owner for limitation in DECLARED_LIMITATIONS)
    assert {limitation.id for limitation in DECLARED_LIMITATIONS} == set(
        epic10.EPIC9_REQUIRED_LIMITATION_OWNERS
    )


def test_ns5_and_st3_remain_advisory_and_open():
    from tests.certification.catalogue import CONTROLS_BY_ID, ControlSeverity

    for control_id in epic10.EPIC9_REQUIRED_ADVISORIES:
        assert CONTROLS_BY_ID[control_id].severity is ControlSeverity.ADVISORY


def test_e10_11_detects_a_dropped_limitation(monkeypatch):
    owners = dict(epic10.EPIC9_REQUIRED_LIMITATION_OWNERS)
    owners.pop("LIM-4")
    monkeypatch.setattr(
        epic10, "_epic9_declared_state", lambda: (owners, {"NS-5": "advisory", "ST-3": "advisory"})
    )
    assert "LIM-4" in _violation_of(epic10._e10_11())


def test_e10_11_detects_a_reassigned_owner(monkeypatch):
    owners = dict(epic10.EPIC9_REQUIRED_LIMITATION_OWNERS) | {"LIM-7": "M10.13"}
    monkeypatch.setattr(
        epic10, "_epic9_declared_state", lambda: (owners, {"NS-5": "advisory", "ST-3": "advisory"})
    )
    assert "LIM-7" in _violation_of(epic10._e10_11())


def test_e10_11_detects_a_closed_advisory(monkeypatch):
    monkeypatch.setattr(
        epic10,
        "_epic9_declared_state",
        lambda: (dict(epic10.EPIC9_REQUIRED_LIMITATION_OWNERS), {"ST-3": "advisory"}),
    )
    assert "NS-5" in _violation_of(epic10._e10_11())


# ── 14. E10-13 · LIM-7 inherited honestly (CV17) ───────────────────────────────


def test_lim7_is_inherited_honestly(report):
    _satisfied(next(r for r in report.results if r.control_id == "E10-13"))


def test_epic10_declares_its_certification_self_administered():
    inherited = [
        limitation
        for limitation in EPIC10_DECLARED_LIMITATIONS
        if limitation.inherited_from == "LIM-7"
    ]
    assert len(inherited) == 1
    assert inherited[0].self_administered
    assert inherited[0].owner == "M10.13"


def test_epic9s_lim7_keeps_its_own_owner():
    """Inheriting must not re-own: LIM-7 stays M9.24's."""
    from tests.certification.catalogue import DECLARED_LIMITATIONS

    lim7 = next(limitation for limitation in DECLARED_LIMITATIONS if limitation.id == "LIM-7")
    assert lim7.owner == "M9.24"


def test_epic10_declares_its_own_limitations_non_empty_and_owned():
    assert EPIC10_DECLARED_LIMITATIONS
    for limitation in EPIC10_DECLARED_LIMITATIONS:
        assert limitation.owner, limitation.id
        assert limitation.statement, limitation.id


def test_a_limitation_without_an_owner_is_rejected():
    with pytest.raises(ValueError, match="names no owning milestone"):
        Epic10DeclaredLimitation(id="E10-LIM-9", milestone="M10.13", owner="", statement="x")


def test_e10_13_detects_a_dropped_inheritance(monkeypatch):
    monkeypatch.setattr(epic10, "EPIC10_DECLARED_LIMITATIONS", ())
    assert "LIM-7" in _violation_of(epic10._e10_13())


def test_e10_13_detects_a_re_owned_lim7(monkeypatch):
    owners = dict(epic10.EPIC9_REQUIRED_LIMITATION_OWNERS) | {"LIM-7": "M10.13"}
    monkeypatch.setattr(epic10, "_epic9_declared_state", lambda: (owners, {}))
    assert "re-own" in _violation_of(epic10._e10_13())


def test_e10_13_detects_a_dishonest_inheritance(monkeypatch):
    dishonest = (
        Epic10DeclaredLimitation(
            id="E10-LIM-1",
            milestone="M10.13",
            owner="M10.13",
            statement="inherits LIM-7 but claims independent audit",
            inherited_from="LIM-7",
            self_administered=False,
        ),
    )
    monkeypatch.setattr(epic10, "EPIC10_DECLARED_LIMITATIONS", dishonest)
    assert "self-administered" in _violation_of(epic10._e10_13())


def test_dl_0139_records_the_epic9_release_assertions_as_unresolved():
    """§M10.13 is explicit that an Epic 10 suite does not resolve them — so E10-LIM-4 says so."""
    lim4 = next(
        limitation for limitation in EPIC10_DECLARED_LIMITATIONS if limitation.id == "E10-LIM-4"
    )
    assert "DL-0139" in lim4.statement


# ── 15. the M10.13 boundary (CV8, CV18, CV20, CV21) ────────────────────────────


def test_no_epic9_certification_freeze_or_release_artifact_was_modified():
    """CV8 — measured against HEAD, so the authorized M10.10 re-baseline is not re-litigated."""
    touched = _git("diff", "--name-only", "HEAD", "--", *epic10.EPIC9_FROZEN_ARTIFACTS)
    assert touched == "", touched


def test_the_decision_log_was_not_modified():
    """CV18 — DL-0139 stays discharged at M10.10; M10.13 records, it does not reopen."""
    assert _git("diff", "--name-only", "HEAD", "--", ".voxvoice/DECISION_LOG.md") == ""


def test_dl_0139_is_still_resolved():
    log = (REPO_ROOT / ".voxvoice/DECISION_LOG.md").read_text()
    assert "M10.13 revisit no longer required" in log
    assert "discharged at **M10.10**" in log


def test_billing_and_migrations_were_not_modified_by_m10_13():
    for pathspec in ("platform/billing", "platform/replay"):
        assert _git("diff", "--name-only", "HEAD", "--", pathspec) == "", pathspec


def test_no_production_module_imports_the_epic10_certification():
    """CV20 — certification is a test-plane fact. Nothing in production may reach for it."""
    offenders = []
    for plane in ("events", "api", "billing", "core", "runtime", "observability", "replay"):
        root = PLATFORM_ROOT / plane
        if not root.exists():
            continue
        for path, source in epic10._python_sources(root).items():
            if "tests.certification" in source or "certification.epic10" in source:
                offenders.append(str(path.relative_to(PLATFORM_ROOT)))
    assert offenders == [], offenders


def test_the_certification_creates_no_table_and_no_migration():
    """CD12 — a stored verdict is a staler answer than re-running the suite."""
    source = EPIC10_MODULE.read_text(encoding="utf-8")
    tree = ast.parse(source)
    #: Persistence receivers. Scoped to the receiver rather than to the method name, because
    #: `add`, `commit` and `merge` are ordinary set and string operations too — banning the bare
    #: name would fail on `found.add(...)` and prove nothing about persistence.
    receivers = {"session", "db", "database", "connection", "engine", "uow", "repository"}
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and isinstance(node.func.value, ast.Name)
        ):
            assert node.func.value.id not in receivers, ast.dump(node)
    for banned in ("Repository(", "UnitOfWork", "command_bus", "dispatch("):
        assert banned not in source, banned
    #: `__tablename__` appears in the module as the *string E10-3 searches for*, so a textual ban
    #: would fire on the control that enforces the naming rule. What matters is that no class here
    #: **declares** one.
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef):
            for stmt in node.body:
                targets = getattr(stmt, "targets", [])
                assert not any(
                    isinstance(t, ast.Name) and t.id == "__tablename__" for t in targets
                ), node.name


def test_the_certification_module_writes_nothing(monkeypatch):
    """Structural: no `open(..., 'w')`, no `write_text`, no `mkdir` anywhere in the module."""
    tree = ast.parse(EPIC10_MODULE.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            assert node.func.attr not in {"write_text", "write_bytes", "mkdir", "unlink"}, (
                ast.dump(node)
            )


def test_the_suite_is_hermetic_and_deterministic():
    """CV23 — two runs of the whole certification agree on every field."""
    first, second = certify_epic10(), certify_epic10()
    assert first.verdict is second.verdict
    assert first.report_digest == second.report_digest
    assert first.results == second.results
