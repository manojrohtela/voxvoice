"""M9.25 — Epic 9 Freeze.

M9.24 certified the repository. M9.25 proves the tree is **still in that certified state** and
writes it down deterministically.

The distinction matters and this suite keeps it: M9.24 asks *"does this architecture hold?"* and
owns 27 controls that answer it. M9.25 asks *"is this the tree that was certified, and has the
freeze itself changed anything?"* — which is a smaller, different question. This file therefore
**verifies the frozen state rather than recreating M9.24**: it re-runs the certification once to
confirm the verdict and digest, and spends the rest of its assertions on the twelve freeze
invariants, which M9.24 does not cover.

The freeze **fails closed**. Every invariant below is an assertion, not a report: a migration after
0033, a new route, a new Studio page, a lost limitation owner or a moved certification digest fails
the freeze rather than being footnoted in it.
"""

from __future__ import annotations

import pkgutil
from pathlib import Path

import pytest

from tests.certification.catalogue import (
    CERTIFICATION_VERSION,
    CONTROLS_BY_ID,
    DECLARED_LIMITATIONS,
    CertificationVerdict,
    ControlOutcome,
    ControlSeverity,
)
from tests.certification.runner import certify
from tests.freeze.manifest import (
    CERTIFIED_MIGRATION_HEAD,
    EPIC9_FIRST_MILESTONE,
    EPIC9_LAST_MILESTONE,
    build_manifest,
    derived_inventory,
    manifest_digest,
    render,
)

#: The release inputs M9.24 verified. Declared, because they are facts about *running* the suite,
#: not about reading the tree — see the manifest module's "derived versus declared" note.
RELEASE_INPUTS = dict(
    repository_wide_tests=4643,
    failed=0,
    skipped=0,
    ruff_clean=True,
    commit_state="uncommitted",
)

#: The certification digest M9.24 closed with. Pinned here so a drifting tree fails the freeze
#: rather than quietly re-certifying to a different value.
CERTIFIED_DIGEST = "39790b95d061a7631e6d1b61dab14b227f02038873ee87b5c6a34658bb01d2d8"


@pytest.fixture(scope="module")
def manifest() -> dict:
    return build_manifest(**RELEASE_INPUTS)


# ── FZ-1 · no new financial fact ────────────────────────────────────────────────


def test_fz1_no_financial_fact_is_introduced_by_the_freeze():
    """M9.25 adds no table, no aggregate and no money-bearing column."""
    inventory = derived_inventory()
    assert inventory["database"]["table_count"] == 145
    assert inventory["database"]["tables_all_billing_scoped"] is True


def test_fz1_freeze_adds_no_production_module():
    """Freeze logic lives under `tests/`, the M9.24 D-10 lesson applied before it could recur."""
    assert Path("tests/freeze/manifest.py").exists()
    assert not Path("billing/application/freeze_engine.py").exists()
    assert not Path("billing/foundation/freeze.py").exists()
    import billing.application as A
    import billing.foundation as F

    for package in (F, A):
        names = {m.name for m in pkgutil.iter_modules(package.__path__)}
        assert not any("freeze" in n for n in names), names


# ── FZ-2 / FZ-3 · migration head and schema ─────────────────────────────────────


def test_fz2_migration_head_is_exactly_0033():
    from billing.persistence import migrations

    versions = [m.VERSION for m in migrations.MIGRATIONS]
    assert versions[-1] == CERTIFIED_MIGRATION_HEAD
    assert len(versions) == 21
    assert versions[0] == "0013"


def test_fz2_no_migration_follows_0033():
    """A migration after the certified head means the freeze is freezing a different schema."""
    from billing.persistence import migrations

    later = [m.VERSION for m in migrations.MIGRATIONS if m.VERSION > CERTIFIED_MIGRATION_HEAD]
    assert later == [], later
    on_disk = sorted(p.name for p in Path("billing/persistence/migrations").glob("v0*.py"))
    assert on_disk[-1].startswith("v0033"), on_disk[-3:]


def test_fz3_no_schema_mutation_is_required_by_the_freeze():
    inventory = derived_inventory()["database"]
    assert inventory["migration_chain_ordered"] is True
    assert inventory["migration_chain_contiguous"] is True
    assert inventory["index_names_unique"] is True
    assert inventory["index_count"] == 1193


# ── FZ-4 / FZ-5 · API and Studio surfaces ───────────────────────────────────────


def test_fz4_no_api_surface_is_introduced_by_the_freeze():
    inventory = derived_inventory()["api"]
    assert inventory["route_count"] == 521
    assert inventory["prefix_count"] == 30
    assert inventory["duplicate_routes"] == 0


def test_fz5_no_studio_surface_is_introduced_by_the_freeze():
    assert derived_inventory()["architecture"]["studio_pages"] == 18


# ── FZ-6 / FZ-7 · dependencies and ownership ────────────────────────────────────


def test_fz6_no_runtime_or_cross_plane_dependency_is_introduced(manifest):
    """Re-asserted through certification's own controls rather than a second scan."""
    results = manifest["derived"]["certification"]["results"]
    assert results["PL-1"] == "satisfied"
    assert results["PL-2"] == "satisfied"


def test_fz7_no_cross_domain_ownership_change(manifest):
    results = manifest["derived"]["certification"]["results"]
    for control in ("OWN-1", "OWN-2", "OWN-3", "OWN-4", "RS-1"):
        assert results[control] == "satisfied", control


def test_fz7_the_freeze_closes_no_other_milestones_limitation():
    """**The rule this milestone could most easily break.**

    LIM-1..6 belong to M9.15, M9.16, M9.18, M9.8 and M9.13/M9.14. A freeze that "tidied" one of
    them would be stealing ownership under cover of governance work."""
    owners = {limit.id: limit.owner for limit in DECLARED_LIMITATIONS}
    assert owners["LIM-1"] == "M9.15"
    assert owners["LIM-2"] == "M9.16"
    assert owners["LIM-3"] == "M9.18"
    assert owners["LIM-4"] == "M9.8"
    assert owners["LIM-5"] == "M9.8"
    assert owners["LIM-6"] == "M9.13/M9.14"
    assert "M9.25" not in set(owners.values()), "the freeze took nobody's limitation"


# ── FZ-8 · certification remains valid against the frozen tree ──────────────────


def test_fz8_certification_verdict_is_unchanged():
    report = certify()
    assert report.verdict is CertificationVerdict.CERTIFIED_WITH_ADVISORIES
    assert len(report.blocking_violations) == 0
    assert len(report.inconclusive) == 0


def test_fz8_certification_digest_is_unchanged():
    """**The load-bearing freeze assertion.** A moved digest means a moved tree."""
    assert certify().report_digest == CERTIFIED_DIGEST


def test_fz8_the_catalogue_is_unchanged():
    assert CERTIFICATION_VERSION == "m9.24.1"
    assert len(CONTROLS_BY_ID) == 27
    blocking = sum(
        1 for c in CONTROLS_BY_ID.values() if c.severity is ControlSeverity.BLOCKING
    )
    advisory = sum(
        1 for c in CONTROLS_BY_ID.values() if c.severity is ControlSeverity.ADVISORY
    )
    assert (blocking, advisory) == (24, 3)


def test_fz8_every_blocking_control_remains_satisfied():
    report = certify()
    for result in report.results:
        if CONTROLS_BY_ID[result.control_id].severity is ControlSeverity.BLOCKING:
            assert result.outcome is ControlOutcome.SATISFIED, result.control_id


# ── FZ-9 / FZ-10 · limitations and advisories ───────────────────────────────────


def test_fz9_all_nine_limitations_remain_explicitly_owned():
    assert len(DECLARED_LIMITATIONS) == 9
    for limitation in DECLARED_LIMITATIONS:
        assert limitation.owner, limitation.id
        assert limitation.milestone, limitation.id
        assert limitation.statement, limitation.id


def test_fz9_the_self_administered_certification_limitation_remains_explicit():
    """M9.24 certified itself. The freeze must not quietly drop that admission."""
    lim7 = next(x for x in DECLARED_LIMITATIONS if x.id == "LIM-7")
    assert lim7.owner == "M9.24"
    assert "self-administered" in lim7.statement


def test_fz10_advisories_remain_ns5_and_st3():
    """Unless the repository proves they were independently resolved — it does not."""
    report = certify()
    assert sorted(a.control_id for a in report.advisories) == ["NS-5", "ST-3"]
    assert CONTROLS_BY_ID["NS-5"].severity is ControlSeverity.ADVISORY
    assert CONTROLS_BY_ID["ST-3"].severity is ControlSeverity.ADVISORY


def test_fz10_the_freeze_did_not_reclassify_an_advisory_into_a_pass():
    report = certify()
    outcomes = {r.control_id: r.outcome for r in report.results}
    assert outcomes["NS-5"] is ControlOutcome.VIOLATED
    assert outcomes["ST-3"] is ControlOutcome.VIOLATED


# ── FZ-11 · deterministic freeze evidence ───────────────────────────────────────


def test_fz11_the_manifest_is_byte_identical_across_runs(manifest):
    assert render(build_manifest(**RELEASE_INPUTS)) == render(manifest)


def test_fz11_the_manifest_digest_is_stable(manifest):
    assert manifest_digest(build_manifest(**RELEASE_INPUTS)) == manifest_digest(manifest)


def test_fz11_a_changed_input_changes_the_manifest_digest(manifest):
    moved = build_manifest(**{**RELEASE_INPUTS, "repository_wide_tests": 9999})
    assert manifest_digest(moved) != manifest_digest(manifest)


def test_fz11_the_manifest_contains_nothing_volatile(manifest):
    """No timestamp, no UUID, no process id, no absolute path, no host."""
    text = render(manifest)
    for volatile in ("/Users/", "\\Users\\", "/private/", "pid=", "host=", "T00:", "2026-"):
        assert volatile not in text, volatile
    assert "tmp" not in text.lower()


def test_fz11_the_manifest_declares_no_second_canonicalizer():
    """`DT-3` certifies exactly one canonicalizer exists; the manifest calls it, never clones it."""
    source = Path("tests/freeze/manifest.py").read_text()
    assert "canonical_digest" in source
    assert "def canonical_form" not in source
    assert "hashlib" not in source


# ── FZ-12 · reconstructability, stated honestly ─────────────────────────────────


def test_fz12_the_manifest_declares_its_release_inputs(manifest):
    """FZ-12 is *"reconstructable from the manifest and its declared release inputs"*, so the
    inputs must actually be in the manifest rather than assumed."""
    declared = manifest["declared_release_inputs"]
    assert declared["repository_wide_tests"] == 4643
    assert declared["failed"] == 0
    assert declared["skipped"] == 0
    assert declared["ruff_clean"] is True
    assert declared["commit_state"] == "uncommitted"


def test_fz12_reconstructability_is_not_overclaimed(manifest):
    """**The honest half of FZ-12.**

    Epic 9 is entirely uncommitted — `HEAD` is Epic 8's release commit — so there is no commit from
    which this tree can be reconstructed today. That is roadmap risk **D-11**, anticipated when
    freeze (M9.25) and repository release (M9.26) were split, and it is **M9.26's** to discharge by
    committing and tagging. The manifest records the posture rather than claiming a reproducibility
    the repository cannot currently provide."""
    assert manifest["declared_release_inputs"]["commit_state"] == "uncommitted"
    assert manifest["freeze_status"] == "frozen"


# ── freeze scope: what M9.25 did not touch ──────────────────────────────────────


def test_the_freeze_introduced_no_new_billing_domain():
    """No new engine, no new bounded context."""
    import billing.application as A

    names = {m.name for m in pkgutil.iter_modules(A.__path__)}
    engines = {n for n in names if n.endswith("_engine")}
    assert len(engines) == 15, sorted(engines)


def test_the_freeze_covers_the_declared_milestone_range(manifest):
    assert manifest["derived"]["epic"]["first_milestone"] == EPIC9_FIRST_MILESTONE == "M9.2"
    assert manifest["derived"]["epic"]["last_milestone"] == EPIC9_LAST_MILESTONE == "M9.24"
    assert manifest["derived"]["epic"]["certified_state"] == "certified_with_advisories"


def test_the_freeze_records_the_inventory_it_claims(manifest):
    """Every number in the freeze report comes from here, not from prose."""
    derived = manifest["derived"]
    assert derived["architecture"] == {
        "foundation_modules": 78,
        "application_modules": 30,
        "persistence_modules": 45,
        "customer_modules": 28,
        "studio_pages": 18,
    }
    assert derived["events"]["billing_domain_events"] == 252
    assert derived["events"]["all_billing_namespaced"] is True
    assert derived["lifecycles"] == {"count": 29, "all_have_terminal_states": True}
    assert derived["namespace"]["foundation_exports"] == 1381
    assert derived["namespace"]["application_exports"] == 1028


def test_m9_26_is_the_release_milestone_and_m9_25_did_not_do_its_work():
    """Freeze and release are separate milestones; collapsing them repeats the M8.19 condition.

    **The forward-looking half of this assertion moved to M9.26.** As first written it asserted
    `v4.0.0 not in tags` — the *absence of a future milestone's artifact*, which is not a property
    of the frozen tree and which fails the moment M9.26 does the job the roadmap assigns it. It now
    lives in `tests/test_billing_release.py` inverted and stronger: the tag must exist, be
    annotated, and point at the release commit. Nothing was weakened; the assertion was relocated
    to the milestone that owns the artifact.

    What M9.25 can still prove is what it proves here: the freeze produced no release metadata of
    its own, and the manifest records the commit posture rather than asserting a release."""
    assert not Path("docs/releases/epic9-repository-release-v4.0.0.md").exists()
    manifest = build_manifest(**RELEASE_INPUTS)
    assert manifest["freeze_status"] == "frozen"
    assert "release" not in manifest["derived"]["epic"]["certified_state"]
