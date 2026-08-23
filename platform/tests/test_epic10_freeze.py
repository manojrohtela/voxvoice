"""M10.14 — the Epic 10 freeze suite (owner rulings R-1…R-7, DL-0146).

M10.13 certified the repository. M10.14 proves the tree is **still in that certified state** and
writes it down deterministically. This suite keeps the distinction: M10.13 asks *"does this
architecture hold?"* and owns thirteen `E10-` controls; M10.14 asks *"is this the tree that was
certified, and has the freeze itself changed anything?"* and owns twelve `EF-` attestations.

The freeze **fails closed**, and — per the Gate 3 authorization — every important boundary property
is exercised **twice**: once against the real tree, where it must be `SATISFIED`, and once against
a synthetic violation, where it must be `VIOLATED` and must name what offended. A guard that has
only ever seen a passing repository has not been shown to guard anything.

Synthetic cases substitute the evidence helpers. Nothing here writes to the repository.
"""

from __future__ import annotations

import ast
import subprocess
from pathlib import Path

import pytest

from tests.freeze import epic10 as freeze
from tests.freeze.epic10 import (
    BASELINE_TAG,
    CERTIFIED_COMMIT,
    DECLARED_RUFF_SCOPES,
    EPIC9_CURRENT_CERTIFICATION_DIGEST,
    EPIC9_CURRENT_MANIFEST_DIGEST,
    EPIC9_HISTORICAL_CERTIFICATION_DIGEST,
    EPIC9_HISTORICAL_MANIFEST_DIGEST,
    EPIC10_FREEZE_CONTROLS,
    EPIC10_FREEZE_VERSION,
    EPIC10_MIGRATION_HEAD,
    FREEZE_CONTROLS_BY_ID,
    FREEZE_EVALUATORS,
    FREEZE_PASSING_OUTCOMES,
    REPO_ROOT,
    Epic10FreezeOutcome,
    Epic10FreezeSeverity,
    Epic10FreezeVerdict,
    FreezeAttestation,
    FreezeControl,
    attest,
    build_freeze_manifest,
    freeze_manifest_digest,
    freeze_verdict,
    render_freeze,
)

FREEZE_MODULE = Path(freeze.__file__)

#: The declared freeze inputs measured at the M10.14 freeze boundary.
FREEZE_INPUTS = dict(repository_wide_tests=5345, failed=0, skipped=0)

#: Epic 9's freeze namespace. Epic 10's must be disjoint from it.
EPIC9_FREEZE_PREFIX = "FZ-"

#: The published M10.14 freeze digest. Pinned as a literal so a drifting manifest fails the freeze
#: rather than quietly re-digesting to a different value.
M10_14_FREEZE_DIGEST = "d3d4858d5f18b7f6d583f4d34caeec52933acc262e36d4f8b343e2e8b6a00d38"


@pytest.fixture(scope="module")
def attestations():
    return attest()


@pytest.fixture(scope="module")
def manifest():
    return build_freeze_manifest(**FREEZE_INPUTS)


def _git(*args: str) -> str:
    return subprocess.run(
        ["git", *args], capture_output=True, text=True, cwd=REPO_ROOT, check=False
    ).stdout.strip()


def _violation_of(result: FreezeAttestation) -> str:
    assert result.outcome is Epic10FreezeOutcome.VIOLATED, result
    assert result.violations, "a violation that cannot be located cannot be fixed"
    return " | ".join(result.violations)


def _satisfied(result: FreezeAttestation) -> None:
    assert result.outcome is Epic10FreezeOutcome.SATISFIED, result


def _v5_existed_at_the_freeze_boundary() -> bool:
    """Whether a `v5.0.0` tag existed when M10.14 froze — the same question EF-11 asks.

    Reuses `freeze._is_ancestor`, so there is exactly **one** interpretation of the freeze boundary
    in this repository rather than a second, subtly different one. A tag M10.15 creates points at a
    *descendant* of the certified commit and is therefore not something the freeze produced; a tag
    at the certified commit or behind it is."""
    if not _git("tag", "--list", "v5.0.0"):
        return False
    target = _git("rev-list", "-n", "1", "v5.0.0")
    return freeze._is_ancestor(target, CERTIFIED_COMMIT)


def _release_artifacts_in(paths) -> list[str]:
    """Which of `paths` are release artifacts. Used against the freeze boundary, never the tree."""
    return [p for p in paths if p.startswith("docs/releases/")]


def _tracked_at_the_freeze_boundary(path: str) -> bool:
    """Whether the **exact** `path` was already committed as of the certified commit.

    This is how *"the freeze committed nothing"* is stated durably: M10.14's artifacts must be
    absent from `CERTIFIED_COMMIT`'s tree. That stays true forever, whereas the old
    `HEAD == CERTIFIED_COMMIT` form stopped being evidence about M10.14 the moment M10.15 made
    its authorized commit.

    `-r` is required — `ls-tree` is not recursive by default. A wildcard pathspec is **not** used
    here: `docs/releases/epic9-*` returns nothing even for a file that plainly exists, so a guard
    built on one would be vacuous. Directory pathspecs do work, and
    `_release_docs_at_the_freeze_boundary` uses one."""
    return bool(_git("ls-tree", "-r", "--name-only", CERTIFIED_COMMIT, "--", path))


def _release_docs_at_the_freeze_boundary() -> list[str]:
    """Every `docs/releases` entry tracked at the certified commit."""
    listing = _git("ls-tree", "-r", "--name-only", CERTIFIED_COMMIT, "--", "docs/releases")
    return [p for p in listing.splitlines() if p.strip()]


def _all_satisfied() -> list[FreezeAttestation]:
    return [
        FreezeAttestation(c.id, Epic10FreezeOutcome.SATISFIED)
        for c in EPIC10_FREEZE_CONTROLS
    ]


# ── 1. the verdict ──────────────────────────────────────────────────────────────


def test_the_epic10_tree_is_frozen(manifest):
    """**The headline.** The certified M10.13 state is intact and the freeze changed nothing."""
    assert manifest["freeze_verdict"] == Epic10FreezeVerdict.FROZEN.value, render_freeze(manifest)


def test_every_attestation_is_satisfied(attestations):
    for attestation in attestations:
        _satisfied(attestation)


def test_no_attestation_is_inconclusive(attestations):
    inconclusive = [
        a.control_id for a in attestations if a.outcome is Epic10FreezeOutcome.INCONCLUSIVE
    ]
    assert inconclusive == []


def test_every_control_produced_an_attestation(attestations):
    assert {a.control_id for a in attestations} == set(FREEZE_CONTROLS_BY_ID)


def test_there_are_twelve_controls_in_a_fresh_namespace():
    assert len(EPIC10_FREEZE_CONTROLS) == 12
    assert [c.id for c in EPIC10_FREEZE_CONTROLS] == [f"EF-{n}" for n in range(1, 13)]
    for control in EPIC10_FREEZE_CONTROLS:
        assert not control.id.startswith(EPIC9_FREEZE_PREFIX), control.id
        assert not control.id.startswith("E10-"), control.id


def test_every_control_cites_an_authority():
    for control in EPIC10_FREEZE_CONTROLS:
        assert control.clauses, control.id
        assert control.statement, control.id


def test_a_control_without_an_authority_is_rejected():
    with pytest.raises(ValueError, match="cites no ratified clause"):
        FreezeControl(id="EF-99", severity=Epic10FreezeSeverity.BLOCKING, statement="x", clauses=())


def test_every_control_has_an_evaluator():
    assert set(FREEZE_EVALUATORS) == set(FREEZE_CONTROLS_BY_ID)


# ── 2. INCONCLUSIVE can never freeze (R-4) ─────────────────────────────────────


def test_inconclusive_is_not_a_passing_outcome():
    assert Epic10FreezeOutcome.INCONCLUSIVE not in FREEZE_PASSING_OUTCOMES
    assert not FreezeAttestation("EF-1", Epic10FreezeOutcome.INCONCLUSIVE, detail="why").passed


def test_an_inconclusive_attestation_cannot_produce_frozen():
    results = _all_satisfied()
    results[0] = FreezeAttestation(
        "EF-1", Epic10FreezeOutcome.INCONCLUSIVE, detail="could not look"
    )
    assert freeze_verdict(results) is Epic10FreezeVerdict.NOT_FROZEN


def test_a_blocking_violation_produces_not_frozen():
    results = _all_satisfied()
    results[0] = FreezeAttestation(
        "EF-1", Epic10FreezeOutcome.VIOLATED, violations=("synthetic",)
    )
    assert freeze_verdict(results) is Epic10FreezeVerdict.NOT_FROZEN


def test_an_advisory_violation_produces_frozen_with_advisories(monkeypatch):
    advisory = FreezeControl(
        id="EF-1", severity=Epic10FreezeSeverity.ADVISORY, statement="s", clauses=("c",)
    )
    monkeypatch.setattr(
        freeze, "FREEZE_CONTROLS_BY_ID", dict(FREEZE_CONTROLS_BY_ID) | {"EF-1": advisory}
    )
    results = _all_satisfied()
    results[0] = FreezeAttestation(
        "EF-1", Epic10FreezeOutcome.VIOLATED, violations=("synthetic",)
    )
    assert freeze_verdict(results) is Epic10FreezeVerdict.FROZEN_WITH_ADVISORIES


def test_a_complete_satisfied_set_produces_frozen():
    assert freeze_verdict(_all_satisfied()) is Epic10FreezeVerdict.FROZEN


def test_a_missing_attestation_fails_rather_than_shrinking_the_freeze():
    with pytest.raises(ValueError, match="incomplete"):
        freeze_verdict(_all_satisfied()[:-1])


def test_an_unknown_attestation_is_rejected():
    results = _all_satisfied()
    results.append(FreezeAttestation("EF-99", Epic10FreezeOutcome.SATISFIED))
    with pytest.raises(ValueError, match="unknown controls"):
        freeze_verdict(results)


def test_a_violation_must_name_what_offended():
    with pytest.raises(ValueError, match="nothing named"):
        FreezeAttestation("EF-1", Epic10FreezeOutcome.VIOLATED)


def test_an_inconclusive_attestation_must_say_why():
    with pytest.raises(ValueError, match="must always say why"):
        FreezeAttestation("EF-1", Epic10FreezeOutcome.INCONCLUSIVE)


def test_an_evaluator_that_raises_becomes_inconclusive_not_a_pass(monkeypatch):
    def _explode():
        raise RuntimeError("evidence unavailable")

    monkeypatch.setitem(FREEZE_EVALUATORS, "EF-12", _explode)
    result = next(a for a in attest() if a.control_id == "EF-12")
    assert result.outcome is Epic10FreezeOutcome.INCONCLUSIVE
    assert not result.passed
    assert freeze_verdict(attest()) is Epic10FreezeVerdict.NOT_FROZEN


# ── 3. Epic 9 independence (R-5) ────────────────────────────────────────────────


def test_epic10_never_imports_epic9s_freeze_manifest():
    tree = ast.parse(FREEZE_MODULE.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            assert node.module != "tests.freeze.manifest", ast.dump(node)
        if isinstance(node, ast.Import):
            for alias in node.names:
                assert "freeze.manifest" not in alias.name, alias.name


def test_epic10_invokes_no_epic9_freeze_function():
    """Structural: none of Epic 9's manifest entry points is named anywhere in the module."""
    source = FREEZE_MODULE.read_text(encoding="utf-8")
    tree = ast.parse(source)
    epic9_names = {"build_manifest", "manifest_digest", "derived_inventory", "canonical_digest"}
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            assert node.func.id not in epic9_names, ast.dump(node)


def test_epic10_re_exports_no_epic9_freeze_symbol():
    from tests.freeze import manifest as epic9_freeze

    assert set(freeze.__all__) & set(epic9_freeze.__all__) == set()


def test_epic10_reuses_no_epic9_verdict_constant():
    """Epic 9's freeze verdict is a string in its documents, not an enum Epic 10 shares."""
    assert [v.value for v in Epic10FreezeVerdict] == [
        "frozen",
        "frozen_with_advisories",
        "not_frozen",
    ]
    assert "freeze_with_advisories" not in {v.value for v in Epic10FreezeVerdict}


def test_the_epic10_freeze_version_is_its_own():
    from tests.freeze.manifest import FREEZE_MANIFEST_VERSION

    assert EPIC10_FREEZE_VERSION == "m10.14.1"
    assert EPIC10_FREEZE_VERSION != FREEZE_MANIFEST_VERSION


def test_epic9s_freeze_implementation_is_unchanged():
    from tests.freeze.manifest import CERTIFIED_MIGRATION_HEAD as EPIC9_HEAD
    from tests.freeze.manifest import FREEZE_MANIFEST_VERSION

    assert (
        _git("diff", "--name-only", CERTIFIED_COMMIT, "--", "platform/tests/freeze/manifest.py")
        == ""
    )

    assert FREEZE_MANIFEST_VERSION == "m9.25.1"
    assert EPIC9_HEAD == "0033"


def test_epic9_freeze_and_release_artifacts_are_unchanged():
    touched = _git("diff", "--name-only", CERTIFIED_COMMIT, "--", *freeze.EPIC9_FREEZE_ARTIFACTS)
    assert touched == "", touched


def test_the_epic10_freeze_module_lives_beside_epic9s():
    assert FREEZE_MODULE.parent.name == "freeze"
    assert (FREEZE_MODULE.parent / "manifest.py").exists()


# ── 4. inventory completeness ───────────────────────────────────────────────────


def test_the_inventory_enumerates_every_epic10_path(manifest):
    """Derived independently here from git, then compared with what the manifest recorded."""
    raw = _git("diff", "--name-status", BASELINE_TAG, CERTIFIED_COMMIT, "--", "platform")
    expected_added = sorted(
        line.split("\t")[-1] for line in raw.splitlines() if line.startswith("A")
    )
    inventory = manifest["derived"]["inventory"]
    recorded = sorted(inventory["modules_added"] + inventory["tests_added"])
    assert recorded == expected_added
    assert inventory["counts"]["platform_paths_touched"] == len(raw.splitlines())


def test_every_epic10_migration_is_accounted_for(manifest):
    from events.persistence import migrations

    versions = [m.VERSION for m in migrations.MIGRATIONS]
    assert versions == ["0034", "0035", "0036"]
    recorded = manifest["derived"]["inventory"]["migrations"]
    assert len(recorded) == len(versions)
    for version in versions:
        assert any(f"v{version}_" in path for path in recorded), version


def test_the_migration_head_remains_0036(manifest):
    assert manifest["derived"]["database"]["migration_head"] == EPIC10_MIGRATION_HEAD == "0036"


def test_no_m10_14_migration_was_created():
    for root in ("events", "api", "billing"):
        assert _git("diff", "--name-only", CERTIFIED_COMMIT, "--",
                    f"platform/{root}/persistence/migrations") == "", root


def test_every_epic10_test_file_is_accounted_for(manifest):
    expected = sorted(
        line.split("\t")[-1]
        for line in _git(
            "diff", "--name-status", BASELINE_TAG, CERTIFIED_COMMIT, "--", "platform/tests"
        ).splitlines()
        if line.startswith("A")
    )
    assert manifest["derived"]["inventory"]["tests_added"] == expected
    assert len(expected) == 14


def test_the_tables_and_indexes_are_enumerated(manifest):
    database = manifest["derived"]["database"]
    assert database["tables"] == ["evt_event", "evt_outbox", "evt_outbox_failure"]
    assert database["index_count"] >= 5
    assert database["all_evt_prefixed"] is True


def test_the_inventory_excludes_the_freeze_artifacts_themselves(manifest):
    """Deliberate: the freeze enumerates the *certified* tree, and its own artifacts are
    uncommitted at the boundary — which the commit-state block, not the inventory, records."""
    inventory = manifest["derived"]["inventory"]
    everything = inventory["modules_added"] + inventory["tests_added"] + inventory["tests_modified"]
    assert "platform/tests/freeze/epic10.py" not in everything
    assert "platform/tests/test_epic10_freeze.py" not in everything


# ── 5. the frozen-boundary attestations, each with a synthetic violation ───────


def _patch_changed(monkeypatch, changes):
    monkeypatch.setattr(freeze, "_changed", lambda *a, **k: tuple(changes))


def test_ef1_detects_a_frozen_plane_change(monkeypatch):
    _patch_changed(monkeypatch, [("M", "platform/events/bus.py")])
    assert "frozen plane changed" in _violation_of(freeze._ef_1())


def test_ef2_detects_a_migration_added_by_the_freeze(monkeypatch):
    _patch_changed(monkeypatch, [("A", "platform/events/persistence/migrations/v0037_x.py")])
    assert "v0037" in _violation_of(freeze._ef_2())


def test_ef3_detects_a_changed_frozen_migration(monkeypatch):
    monkeypatch.setattr(freeze, "_require_boundaries", lambda: None)
    _patch_changed(
        monkeypatch, [("M", "platform/billing/persistence/migrations/v0033_billing_x.py")]
    )
    assert "0033" in _violation_of(freeze._ef_3())


def test_ef3_ignores_an_epic10_migration(monkeypatch):
    monkeypatch.setattr(freeze, "_require_boundaries", lambda: None)
    _patch_changed(
        monkeypatch, [("A", "platform/events/persistence/migrations/v0034_durable_event_log.py")]
    )
    _satisfied(freeze._ef_3())


def test_ef4_detects_an_unauthorized_billing_change(monkeypatch):
    monkeypatch.setattr(freeze, "_require_boundaries", lambda: None)
    _patch_changed(monkeypatch, [("M", "platform/billing/application/invoicing.py")])
    assert "unauthorized billing change" in _violation_of(freeze._ef_4())


def test_ef4_accepts_exactly_the_mb14_delta(monkeypatch):
    monkeypatch.setattr(freeze, "_require_boundaries", lambda: None)
    _patch_changed(monkeypatch, [("M", "platform/billing/foundation/catalog.py")])
    _satisfied(freeze._ef_4())


def test_ef5_detects_a_modified_epic9_artifact(monkeypatch):
    _patch_changed(monkeypatch, [("M", "platform/tests/freeze/manifest.py")])
    assert "manifest.py" in _violation_of(freeze._ef_5())


def test_ef6_detects_a_modified_m10_13_artifact(monkeypatch):
    _patch_changed(monkeypatch, [("M", "platform/tests/certification/epic10.py")])
    assert "certification" in _violation_of(freeze._ef_6())


def test_ef7_detects_a_non_certified_verdict(monkeypatch):
    monkeypatch.setattr(
        freeze,
        "_certification_state",
        lambda: {
            "version": "m10.13.1", "verdict": "not_certified", "digest": "x", "control_count": 13,
            "blocking_violations": 1, "advisories": 0, "inconclusive": 0,
        },
    )
    named = _violation_of(freeze._ef_7())
    assert "not_certified" in named


def test_ef7_detects_an_inconclusive_certification(monkeypatch):
    monkeypatch.setattr(
        freeze,
        "_certification_state",
        lambda: {
            "version": "m10.13.1", "verdict": "certified", "digest": "x", "control_count": 13,
            "blocking_violations": 0, "advisories": 0, "inconclusive": 2,
        },
    )
    assert "inconclusive" in _violation_of(freeze._ef_7())


def test_ef8_detects_a_historical_digest_that_does_not_reproduce(monkeypatch):
    monkeypatch.setattr(
        freeze,
        "_epic9_digest_states",
        lambda: {
            "historical": {"source": "a", "manifest_digest": "0" * 64,
                           "certification_digest": EPIC9_HISTORICAL_CERTIFICATION_DIGEST},
            "current": {"source": "b", "manifest_digest": EPIC9_CURRENT_MANIFEST_DIGEST,
                        "certification_digest": EPIC9_CURRENT_CERTIFICATION_DIGEST},
        },
    )
    assert "historical manifest digest" in _violation_of(freeze._ef_8())


def test_ef8_detects_conflated_digest_states(monkeypatch):
    """R-1's core guard: reading both states from one source must fail, not quietly agree."""
    monkeypatch.setattr(
        freeze,
        "_epic9_digest_states",
        lambda: {
            "historical": {"source": "same", "manifest_digest": EPIC9_HISTORICAL_MANIFEST_DIGEST,
                           "certification_digest": EPIC9_HISTORICAL_CERTIFICATION_DIGEST},
            "current": {"source": "same", "manifest_digest": EPIC9_CURRENT_MANIFEST_DIGEST,
                        "certification_digest": EPIC9_CURRENT_CERTIFICATION_DIGEST},
        },
    )
    assert "cannot be conflated" in _violation_of(freeze._ef_8())


def test_ef9_detects_a_reassigned_limitation_owner(monkeypatch):
    owners = dict(freeze.EPIC9_LIMITATION_OWNERS) | {"LIM-7": "M10.14"}
    monkeypatch.setattr(
        freeze, "_epic9_declared_state", lambda: (owners, {"NS-5": "advisory", "ST-3": "advisory"})
    )
    assert "LIM-7" in _violation_of(freeze._ef_9())


def test_ef9_detects_a_dropped_limitation(monkeypatch):
    owners = dict(freeze.EPIC9_LIMITATION_OWNERS)
    owners.pop("LIM-3")
    monkeypatch.setattr(
        freeze, "_epic9_declared_state", lambda: (owners, {"NS-5": "advisory", "ST-3": "advisory"})
    )
    assert "LIM-3" in _violation_of(freeze._ef_9())


def test_ef9_detects_a_promoted_advisory(monkeypatch):
    monkeypatch.setattr(
        freeze,
        "_epic9_declared_state",
        lambda: (dict(freeze.EPIC9_LIMITATION_OWNERS), {"NS-5": "blocking", "ST-3": "advisory"}),
    )
    assert "NS-5" in _violation_of(freeze._ef_9())


def test_ef10_detects_a_production_change_by_the_freeze(monkeypatch):
    _patch_changed(monkeypatch, [("M", "platform/observability/turn_latency.py")])
    assert "production path" in _violation_of(freeze._ef_10())


def test_ef10_permits_a_test_only_change(monkeypatch):
    _patch_changed(monkeypatch, [("A", "platform/tests/test_epic10_freeze.py")])
    _satisfied(freeze._ef_10())


def test_ef11_is_satisfied_at_the_freeze_boundary():
    _satisfied(freeze._ef_11())


def test_the_freeze_boundary_is_exactly_the_four_m10_14_paths():
    assert freeze.EPIC10_FREEZE_BOUNDARY_PATHS == (
        ".voxvoice/DECISION_LOG.md",
        "docs/architecture/epic10/freeze.md",
        "platform/tests/freeze/epic10.py",
        "platform/tests/test_epic10_freeze.py",
    )
    for path in freeze.EPIC10_FREEZE_BOUNDARY_PATHS:
        assert not path.startswith("docs/releases/"), path


def test_ef11_detects_a_release_artifact_authored_by_the_freeze(monkeypatch):
    """The synthetic violation: a freeze that shipped a release document."""
    monkeypatch.setattr(
        freeze,
        "EPIC10_FREEZE_BOUNDARY_PATHS",
        freeze.EPIC10_FREEZE_BOUNDARY_PATHS + ("docs/releases/epic10-premature-v1.0.md",),
    )
    monkeypatch.setattr(freeze, "_git", lambda *a: "")
    assert "authored a release artifact" in _violation_of(freeze._ef_11())


def test_ef11_detects_a_v5_tag_that_existed_at_the_freeze_boundary(monkeypatch):
    """A tag pointing *at* the certified commit means the freeze shipped a release.

    `_is_ancestor` is left real here — `merge-base --is-ancestor` is inclusive, so the certified
    commit is its own ancestor and the check fires without any simulation."""
    monkeypatch.setattr(
        freeze, "_git", lambda *a: "v5.0.0" if a[0] == "tag" else CERTIFIED_COMMIT
    )
    named = _violation_of(freeze._ef_11())
    assert "v5.0.0" in named and "freeze boundary" in named


def test_ef11_detects_a_v5_tag_behind_the_freeze_boundary(monkeypatch):
    """An ancestor commit is equally disqualifying, and equally real: M10.12's commit."""
    ancestor = _git("rev-parse", "8ee7d3c^{commit}")
    monkeypatch.setattr(freeze, "_git", lambda *a: "v5.0.0" if a[0] == "tag" else ancestor)
    assert "existed at the freeze boundary" in _violation_of(freeze._ef_11())


def test_a_later_m10_15_release_does_not_invalidate_the_freeze(monkeypatch):
    """**The property the correction exists for.**

    Once M10.15 creates `docs/releases/epic10-…-v1.0.md` and tags `v5.0.0` on a *descendant* of
    the certified commit, EF-11 must stay satisfied, the verdict must stay FROZEN, and the
    published freeze digest must not move. Before the correction this scenario produced
    NOT_FROZEN and a different digest."""
    later_commit = "f" * 40
    real_git = freeze._git

    def _git_with_a_later_tag(*args: str) -> str:
        """Intercept only the two tag queries; every other call reaches real git.

        A blanket substitution would starve `build_freeze_manifest` of the evidence it needs and
        prove nothing about EF-11."""
        if args[:2] == ("tag", "--list"):
            return "v5.0.0\n"
        if args[0] == "rev-list" and args[-1] == "v5.0.0":
            return later_commit
        return real_git(*args)

    monkeypatch.setattr(freeze, "_git", _git_with_a_later_tag)
    monkeypatch.setattr(freeze, "_is_ancestor", lambda candidate, descendant: False)

    _satisfied(freeze._ef_11())
    manifest = build_freeze_manifest(**FREEZE_INPUTS)
    assert manifest["freeze_verdict"] == Epic10FreezeVerdict.FROZEN.value
    assert freeze_manifest_digest(manifest) == M10_14_FREEZE_DIGEST


def test_ef11_no_longer_reads_the_live_working_tree():
    """Structural: the evaluator must not consult a working-tree diff at all."""
    source = FREEZE_MODULE.read_text(encoding="utf-8")
    tree = ast.parse(source)
    body = next(
        n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == "_ef_11"
    )
    called = {
        n.func.id
        for n in ast.walk(body)
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
    }
    assert "_changed" not in called, called
    assert "_is_ancestor" in called


def test_ef12_detects_a_moved_baseline(monkeypatch):
    monkeypatch.setattr(freeze, "_git", lambda *a: "0" * 40)
    assert BASELINE_TAG in _violation_of(freeze._ef_12())


def test_a_moved_boundary_is_inconclusive_not_a_pass(monkeypatch):
    monkeypatch.setattr(freeze, "_git", lambda *a: "0" * 40)
    with pytest.raises(freeze.FreezeEvidenceUnavailable, match="pinned baseline"):
        freeze._require_boundaries()


# ── 6. the two Epic 9 digest states (R-1) ──────────────────────────────────────


def test_the_historical_digests_are_quoted_from_v4_0_0(manifest):
    historical = manifest["derived"]["epic9_digest_states"]["historical"]
    assert historical["manifest_digest"] == EPIC9_HISTORICAL_MANIFEST_DIGEST
    assert historical["certification_digest"] == EPIC9_HISTORICAL_CERTIFICATION_DIGEST
    assert historical["source"].startswith(f"{BASELINE_TAG}:")


def test_the_historical_digests_really_exist_at_the_baseline():
    doc = _git("show", f"{BASELINE_TAG}:docs/releases/epic9-billing-platform-v1.0.md")
    assert EPIC9_HISTORICAL_MANIFEST_DIGEST in doc
    assert EPIC9_HISTORICAL_CERTIFICATION_DIGEST in doc


def test_the_current_digests_are_recorded_as_a_separate_state(manifest):
    states = manifest["derived"]["epic9_digest_states"]
    assert states["current"]["manifest_digest"] == EPIC9_CURRENT_MANIFEST_DIGEST
    assert states["current"]["certification_digest"] == EPIC9_CURRENT_CERTIFICATION_DIGEST
    assert states["current"]["source"] != states["historical"]["source"]
    assert states["current"]["manifest_digest"] != states["historical"]["manifest_digest"]


def test_the_two_digest_states_live_under_distinct_keys(manifest):
    states = manifest["derived"]["epic9_digest_states"]
    assert set(states) >= {"historical", "current"}
    assert states["states_are_separate_keys"] is True


def test_epic10_never_recomputes_an_epic9_digest(manifest):
    """R-1 / requirement 7 — structural, not a promise in prose."""
    assert manifest["derived"]["epic9_digest_states"]["recomputed_by_epic10"] is False
    source = FREEZE_MODULE.read_text(encoding="utf-8")
    assert "canonical_digest" not in source
    #: Scoped to functions that *compute* a digest, not to functions whose name contains the word:
    #: `_epic9_digest_states` reads two recorded values and computes nothing, so a name-based check
    #: would flag the very reader that keeps the two states apart.
    tree = ast.parse(source)
    computing = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef):
            continue
        for child in ast.walk(node):
            if (
                isinstance(child, ast.Call)
                and isinstance(child.func, ast.Attribute)
                and child.func.attr in {"sha256", "sha1", "md5", "blake2b"}
            ):
                computing.add(node.name)
    assert computing == {"freeze_manifest_digest"}, computing


def test_the_epic9_release_record_is_not_modified_by_the_freeze():
    assert _git("diff", "--name-only", CERTIFIED_COMMIT, "--",
                "docs/releases/epic9-billing-platform-v1.0.md") == ""


# ── 7. the M10.13 certification is consumed, not modified ──────────────────────


def test_the_certification_verdict_is_consumed_unchanged(manifest):
    from tests.certification.epic10 import certify_epic10

    report = certify_epic10()
    recorded = manifest["derived"]["certification"]
    assert recorded["verdict"] == report.verdict.value == "certified"
    assert recorded["digest"] == report.report_digest
    assert recorded["version"] == report.certification_version


def test_the_certification_digest_is_not_recomputed_by_the_freeze(manifest):
    """The freeze copies the value; it contains no certification-digest computation."""
    source = FREEZE_MODULE.read_text(encoding="utf-8")
    assert "epic10_certification_digest" not in source
    assert len(manifest["derived"]["certification"]["digest"]) == 64


def test_the_m10_13_surface_is_untouched():
    assert _git("diff", "--name-only", CERTIFIED_COMMIT, "--", *freeze.M10_13_ARTIFACTS) == ""


def test_all_thirteen_certification_results_are_recorded(manifest):
    results = manifest["derived"]["certification"]["results"]
    assert set(results) == {f"E10-{n}" for n in range(1, 14)}
    assert set(results.values()) == {"satisfied"}


# ── 8. determinism and reproducibility ─────────────────────────────────────────


def test_the_manifest_is_byte_identical_across_runs(manifest):
    assert render_freeze(build_freeze_manifest(**FREEZE_INPUTS)) == render_freeze(manifest)


def test_the_manifest_digest_is_stable(manifest):
    assert freeze_manifest_digest(build_freeze_manifest(**FREEZE_INPUTS)) == freeze_manifest_digest(
        manifest
    )


def test_a_changed_declared_input_changes_the_digest(manifest):
    moved = build_freeze_manifest(**{**FREEZE_INPUTS, "repository_wide_tests": 9999})
    assert freeze_manifest_digest(moved) != freeze_manifest_digest(manifest)


def test_a_changed_ruff_result_changes_the_digest(manifest):
    moved = build_freeze_manifest(**FREEZE_INPUTS, ruff_clean=False)
    assert freeze_manifest_digest(moved) != freeze_manifest_digest(manifest)


def test_the_verdict_is_inside_the_freeze_digest(manifest):
    """Unlike the certification digest (ADR-0043 CD8), which is deliberately verdict-free."""
    moved = dict(manifest) | {"freeze_verdict": Epic10FreezeVerdict.NOT_FROZEN.value}
    assert freeze_manifest_digest(moved) != freeze_manifest_digest(manifest)


def test_the_manifest_contains_nothing_volatile(manifest):
    text = render_freeze(manifest)
    assert str(REPO_ROOT) not in text
    for volatile in ("/Users/", "/home/", "/tmp/", "T00:", "timestamp", "uuid", "localhost"):
        assert volatile not in text, volatile


def test_the_digest_is_stable_across_key_insertion_order(manifest):
    reordered = {k: manifest[k] for k in reversed(list(manifest))}
    assert freeze_manifest_digest(reordered) == freeze_manifest_digest(manifest)


# ── 9. derived versus declared, stated honestly ────────────────────────────────


def test_the_manifest_separates_derived_from_declared(manifest):
    assert set(manifest) == {
        "manifest_version",
        "freeze_verdict",
        "declared_freeze_inputs",
        "derived",
    }
    assert set(manifest["declared_freeze_inputs"]) == {
        "repository_wide_tests", "failed", "skipped", "ruff", "commit_state"
    }


def test_the_declared_inputs_are_not_derivable_by_reading_the_tree(manifest):
    """Each declared field is a fact about *running* something, or about VCS posture."""
    declared = manifest["declared_freeze_inputs"]
    assert declared["repository_wide_tests"] == 5345
    assert declared["failed"] == 0 and declared["skipped"] == 0
    assert declared["ruff"]["clean"] is True
    assert declared["commit_state"]["kind"] == "freeze_boundary_evidence"


def test_the_derived_values_are_actually_derived(manifest):
    """Each derived value is reproduced here from the repository, independently of the manifest."""
    from events.persistence import migrations

    derived = manifest["derived"]
    assert derived["epic"]["certified_commit"] == CERTIFIED_COMMIT
    assert derived["database"]["migration_head"] == migrations.MIGRATIONS[-1].VERSION
    assert derived["inventory"]["counts"]["migrations"] == len(migrations.MIGRATIONS)
    assert derived["attestation"]["control_count"] == len(EPIC10_FREEZE_CONTROLS)


def test_reconstructability_is_not_overclaimed(manifest):
    """The manifest never claims to derive the suite result, the ruff result or the commit state."""
    declared_keys = set(manifest["declared_freeze_inputs"])
    derived_keys = set(manifest["derived"])
    assert declared_keys & derived_keys == set()
    for key in ("repository_wide_tests", "failed", "skipped", "ruff", "commit_state"):
        assert key not in derived_keys, key


# ── 10. commit state at the freeze boundary (R-2) ──────────────────────────────


def test_the_commit_state_distinguishes_committed_from_uncommitted(manifest):
    state = manifest["declared_freeze_inputs"]["commit_state"]
    assert state["certified_implementation"] == "committed"
    assert state["certified_implementation_commit"] == CERTIFIED_COMMIT
    assert state["freeze_artifacts"] == "uncommitted"
    assert state["freeze_artifacts_committed_by"] == "M10.15"


def test_the_commit_state_is_described_as_freeze_boundary_evidence(manifest):
    state = manifest["declared_freeze_inputs"]["commit_state"]
    assert state["kind"] == "freeze_boundary_evidence"
    assert "historical evidence" in state["semantics"]
    assert "not a present-tense claim" in state["semantics"]


def test_the_certified_commit_is_pinned_not_read_from_head():
    """A boundary derived from HEAD would absorb M10.15's commit into the frozen inventory."""
    source = FREEZE_MODULE.read_text(encoding="utf-8")
    assert f'CERTIFIED_COMMIT = "{CERTIFIED_COMMIT}"' in source
    assert _git("cat-file", "-t", CERTIFIED_COMMIT) == "commit"
    assert _git("log", "-1", "--format=%s", CERTIFIED_COMMIT).startswith("feat(certification)")


def test_the_freeze_does_not_commit_anything():
    """R-2 — M10.14 creates evidence; M10.15 commits it.

    **Stated at the freeze boundary.** The durable fact is that the artifacts M10.14 authored are
    absent from `CERTIFIED_COMMIT`'s tree — the freeze committed nothing. Asserting
    `HEAD == CERTIFIED_COMMIT` said the same thing only for as long as no authorized commit
    followed, and M10.15's release commit is exactly such a commit."""
    for path in freeze.EPIC10_FREEZE_BOUNDARY_PATHS:
        if path == ".voxvoice/DECISION_LOG.md":
            # Pre-existing at the boundary; what M10.14 added to it is DL-0146, and the freeze
            # committed that no more than it committed the other three.
            continue
        assert not _tracked_at_the_freeze_boundary(path), (
            f"{path} is tracked at the freeze boundary; the freeze committed it"
        )
    assert [p for p in _release_docs_at_the_freeze_boundary() if "epic10" in p] == [], (
        "the freeze boundary tracks an Epic 10 release artifact"
    )
    # A later release commit is an authorized *descendant*, never a rewrite of the boundary.
    assert freeze._is_ancestor(CERTIFIED_COMMIT, _git("rev-parse", "HEAD"))
    assert _git("diff", "--cached", "--name-only") == ""


# ── 11. ruff scope, recorded exactly (R-7) ─────────────────────────────────────


def test_the_ruff_result_records_its_exact_scope(manifest):
    ruff = manifest["declared_freeze_inputs"]["ruff"]
    assert ruff["scopes"] == list(DECLARED_RUFF_SCOPES)
    assert ruff["scopes"] == ["ruff check . --exclude voicebot", "ruff check billing/ tests/"]


def test_the_manifest_makes_no_repository_wide_ruff_claim(manifest):
    ruff = manifest["declared_freeze_inputs"]["ruff"]
    assert ruff["repository_wide_claim"] is False
    assert "voicebot/bot.py" in ruff["note"]
    assert "not clean" in ruff["note"].lower()


def test_voicebot_is_outside_scope_and_unmodified():
    assert _git("diff", "--name-only", CERTIFIED_COMMIT, "--", "platform/voicebot") == ""


# ── 12. the M10.14 boundary ────────────────────────────────────────────────────


def test_the_freeze_changes_no_production_code():
    for plane in freeze.FROZEN_PLANES:
        assert _git("diff", "--name-only", CERTIFIED_COMMIT, "--", plane) == "", plane


def test_the_freeze_creates_no_release_notes_or_v5_artifact():
    """**Stated at the freeze boundary**, the same way EF-11 states it.

    The obligation is that *the freeze* authored no release artifact and that no `v5.0.0` existed
    when it ran — not that the repository may never hold one, which is the release M10.15 is
    required to produce."""
    assert _release_artifacts_in(freeze.EPIC10_FREEZE_BOUNDARY_PATHS) == []
    assert not _v5_existed_at_the_freeze_boundary()
    assert [p for p in _release_docs_at_the_freeze_boundary() if "epic10" in p] == []


def test_no_adr_0044_was_created():
    """R-3 — M10.14 is governance evidence; DL-0146 carries it."""
    assert not list((REPO_ROOT / "docs" / "adr").glob("ADR-0044*"))
    assert _git("diff", "--name-only", CERTIFIED_COMMIT, "--", "docs/adr") == ""


def test_the_roadmap_is_unmodified():
    assert _git("diff", "--name-only", CERTIFIED_COMMIT, "--", "docs/roadmap") == ""


def test_dl_0146_records_the_freeze_and_dl_0139_stays_resolved():
    log = (REPO_ROOT / ".voxvoice" / "DECISION_LOG.md").read_text(encoding="utf-8")
    assert "| DL-0146 |" in log
    assert "M10.13 revisit no longer required" in log
    assert "discharged at **M10.10**" in log
    assert log.count("| DL-0146 |") == 1


def test_m10_15_was_not_entered():
    """M10.15 had not occurred **at the freeze boundary** — commit, tag and release doc all absent.

    The assertion is historical: M10.14 must not be treated as having already released. It says
    nothing about whether M10.15 subsequently does its authorized work, because a freeze that
    forbade its own release forever would forbid the thing it exists to authorise."""
    assert not _v5_existed_at_the_freeze_boundary()
    assert [p for p in _release_docs_at_the_freeze_boundary() if "epic10" in p] == []
    assert not _tracked_at_the_freeze_boundary("docs/architecture/epic10/freeze.md")


# ── 12a. the corrected boundary guards, proven in both directions ──────────────
#
# The three guards above were rewritten from live-repository checks to freeze-boundary checks.
# A rewrite that only ever agrees with the current tree proves nothing, so each direction is
# exercised: a violation *at the boundary* must still fail, and authorized M10.15 work *after* the
# boundary must not.


def test_the_boundary_release_listing_is_not_vacuous():
    """The guard above can only be trusted if the listing it reads actually sees files.

    `ls-tree` is not recursive by default and its wildcard pathspecs do not match here, so an
    earlier draft of this helper returned empty for a file that plainly exists — a guard that
    could never fail. Epic 9's release document is the positive control."""
    listing = _release_docs_at_the_freeze_boundary()
    assert "docs/releases/epic9-billing-platform-v1.0.md" in listing, listing
    assert len(listing) >= 6, listing


def test_a_release_artifact_at_the_boundary_is_still_detected():
    """Direction A — a freeze that authored a release document fails."""
    polluted = freeze.EPIC10_FREEZE_BOUNDARY_PATHS + ("docs/releases/epic10-premature-v1.0.md",)
    assert _release_artifacts_in(polluted) == ["docs/releases/epic10-premature-v1.0.md"]


def test_a_v5_tag_at_the_boundary_is_still_detected(monkeypatch):
    """Direction A — a `v5.0.0` pointing at the certified commit means the freeze released.

    `_is_ancestor` is left real: `merge-base --is-ancestor` is inclusive, so the certified commit
    is its own ancestor and the check fires without simulation."""
    monkeypatch.setattr(
        "tests.test_epic10_freeze._git",
        lambda *a: "v5.0.0" if a[0] == "tag" else CERTIFIED_COMMIT,
    )
    assert _v5_existed_at_the_freeze_boundary()


def test_a_v5_tag_behind_the_boundary_is_still_detected(monkeypatch):
    """Direction A — an ancestor commit is equally disqualifying. M10.12's commit is a real one."""
    ancestor = _git("rev-parse", "8ee7d3c^{commit}")
    monkeypatch.setattr(
        "tests.test_epic10_freeze._git",
        lambda *a: "v5.0.0" if a[0] == "tag" else ancestor,
    )
    assert _v5_existed_at_the_freeze_boundary()


def test_a_later_authorized_v5_tag_does_not_trip_the_boundary_guards(monkeypatch):
    """Direction B — `v5.0.0` on an authorized descendant is not a freeze-boundary violation."""
    monkeypatch.setattr(
        "tests.test_epic10_freeze._git",
        lambda *a: "v5.0.0" if a[0] == "tag" else "f" * 40,
    )
    monkeypatch.setattr(freeze, "_is_ancestor", lambda candidate, descendant: False)
    assert not _v5_existed_at_the_freeze_boundary()


def test_the_later_m10_15_artifacts_are_absent_from_the_boundary():
    """Direction B — the release document, CHANGELOG and INDEX rows exist in the working tree now,
    and none of them is tracked at the certified commit. That is exactly the distinction the
    corrected guards make, and the reason they survive the release."""
    assert (REPO_ROOT / "docs/releases/epic10-durable-event-telemetry-spine-v1.0.md").exists()
    assert not _tracked_at_the_freeze_boundary(
        "docs/releases/epic10-durable-event-telemetry-spine-v1.0.md"
    )
    for path in freeze.EPIC10_FREEZE_BOUNDARY_PATHS:
        if path == ".voxvoice/DECISION_LOG.md":
            continue
        assert (REPO_ROOT / path).exists(), path
        assert not _tracked_at_the_freeze_boundary(path), path


def test_a_later_release_commit_leaves_the_boundary_an_ancestor():
    """Direction B — the certified commit stays reachable from HEAD however many commits follow."""
    assert freeze._is_ancestor(CERTIFIED_COMMIT, _git("rev-parse", "HEAD"))
    assert _git("cat-file", "-t", CERTIFIED_COMMIT) == "commit"


def test_the_freeze_module_writes_nothing():
    tree = ast.parse(FREEZE_MODULE.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            assert node.func.attr not in {"write_text", "write_bytes", "mkdir", "unlink"}, ast.dump(
                node
            )


def test_the_freeze_declares_no_table_and_no_production_import():
    source = FREEZE_MODULE.read_text(encoding="utf-8")
    for banned in ("__tablename__ =", "Repository(", "UnitOfWork", "command_bus"):
        assert banned not in source, banned
