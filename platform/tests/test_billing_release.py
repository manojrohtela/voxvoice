"""M9.26 — Epic 9 Repository Release · Billing Platform v1.0 · v4.0.0.

M9.24 certified the architecture. M9.25 froze it. **M9.26 makes it durable** — one commit that
preserves the frozen tree, and one annotated tag that names it.

That is the whole milestone. It introduces no financial fact, no table, no migration, no route, no
Studio page and no production module; it contributes repository-release metadata only.

## Why this file is small

§12 asks for release integrity, *not* a second certification suite. The 27 controls already prove
the architecture and the 29 freeze tests already prove the frozen state; re-running either here
would be duplication that adds confidence nobody should feel. What is genuinely new at M9.26 is the
**release artifact itself**: does a commit exist, does the tag point at it, is the tree clean, and
does the release preserve the digests it claims to preserve.

## The assertion relocated from M9.25

M9.25's freeze suite originally asserted `v4.0.0 not in tags` — the absence of a future milestone's
artifact, which is not a property of a frozen tree and which fails the moment M9.26 does its job.
It moved here, inverted and stronger: the tag must **exist**, be **annotated** (the v1.0.0/v2.0.0/
v3.0.0 convention), and point at the **release commit**. Nothing was weakened.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from tests.certification.catalogue import CONTROLS_BY_ID, DECLARED_LIMITATIONS, ControlSeverity
from tests.certification.runner import certify
from tests.freeze.manifest import build_manifest, manifest_digest

#: The release identity the roadmap specifies. Not invented here — §10 of
#: `docs/roadmap/epic9-implementation-plan.md`: *"Commit, tag, baseline **v4.0.0** recommendation"*.
RELEASE_TAG = "v4.0.0"

#: The commit `v4.0.0` names. Pinned at **DL-0139's resolution** (owner ruling, 2026-08-20) so the
#: two assertions below state the invariant they always meant: *"the tag points at **its** release
#: commit, and **that** commit contains no unrelated work."* Both previously compared against
#: `HEAD`, which is the same thing only while HEAD *is* the release commit — so both broke at the
#: first post-release commit (`8b27a1a`, M10.1) and could never pass again by construction. Written
#: as a literal, never derived from `HEAD` or from the tag: a value read from the thing under test
#: cannot detect that thing moving.
RELEASE_COMMIT = "532b0bd72d55921fa385a3e4162d899c90fbe6ad"

#: The certification and freeze-manifest digests the release must preserve exactly.
#:
#: **Re-baselined at M10.10** (owner ruling, 2026-08-20) from M9.24/M9.25's
#: `39790b95d061a7631e6d1b61dab14b227f02038873ee87b5c6a34658bb01d2d8` and
#: `eed692224f0aeb82c1a64e8edb39b3b553a709c67cf5d37ae50090bcb466b65c`, which are quoted here as
#: history and never recomputed. PL-2's control definition legitimately changed — it gained the
#: narrow, structural `api/metering/** → billing.foundation` exception that ADR-0031 IR6 and its
#: topology always contemplated — and the manifest embeds the certification results, so both
#: digests move together. Neither digest generation nor manifest semantics changed, and no
#: verification was weakened: the verdict, the advisory set and every other control are identical.
CERTIFICATION_DIGEST = "c044b3f6a23066df4ed97c8b7d0992d8419490ed68dd1b3633b22717f7cab6e6"
FREEZE_MANIFEST_DIGEST = "cdafb4ab6cba00bcaed0c9b6a3ae477253ce66e492b5b743c9835072704d1fb7"

REPO_ROOT = Path(__file__).resolve().parent.parent.parent


def _git(*args: str) -> str:
    return subprocess.run(
        ["git", *args], capture_output=True, text=True, cwd=REPO_ROOT
    ).stdout.strip()


@pytest.fixture(scope="module")
def tags() -> list[str]:
    return _git("tag").split()


# ── the release artifact ────────────────────────────────────────────────────────


def test_the_release_tag_exists(tags):
    """**Relocated from M9.25, inverted.** The tag is M9.26's deliverable."""
    assert RELEASE_TAG in tags, tags


def test_the_release_tag_is_annotated():
    """v1.0.0, v2.0.0 and v3.0.0 are annotated; the convention is not re-invented here."""
    assert _git("cat-file", "-t", RELEASE_TAG) == "tag"


def test_the_release_tag_points_at_the_release_commit():
    """**Retargeted at DL-0139's resolution** (owner ruling, 2026-08-20) from `HEAD` to
    `RELEASE_COMMIT`. Nothing is weakened: the assertion still fails if the tag is moved to any
    other commit — which is the release identity it exists to protect — and it now also holds
    while development continues past the release."""
    tagged = _git("rev-list", "-n", "1", RELEASE_TAG)
    assert tagged == RELEASE_COMMIT, f"{RELEASE_TAG} -> {tagged[:8]}, expected {RELEASE_COMMIT[:8]}"


def test_no_second_epic9_tag_was_invented(tags):
    """§9: do not create an Epic 9 tag with a different name."""
    epic9_tags = [t for t in tags if "epic9" in t.lower() or "billing" in t.lower()]
    assert epic9_tags == [], epic9_tags


def test_the_working_tree_is_clean_after_release():
    assert _git("status", "--short") == "", _git("status", "--short")


# ── FZ-12, discharged ───────────────────────────────────────────────────────────


def test_fz12_is_discharged_by_the_release_commit():
    """**The invariant M9.25 could not satisfy.**

    A freeze over an uncommitted tree freezes nothing durable: the manifest described a working
    directory no commit preserved. One commit changes that, and this is the assertion that says so
    — it passes only because the commit actually exists."""
    head = _git("rev-parse", "HEAD")
    assert len(head) == 40, head
    tracked = _git("ls-tree", "-r", "--name-only", "HEAD")
    assert "platform/billing/foundation/money.py" in tracked
    assert "platform/tests/test_billing_freeze.py" in tracked
    assert "docs/architecture/epic9/freeze.md" in tracked


def test_the_release_commit_contains_the_whole_frozen_epic():
    """Every Epic 9 layer is preserved, not a subset."""
    tracked = set(_git("ls-tree", "-r", "--name-only", "HEAD").splitlines())
    for layer in ("foundation", "application", "persistence", "customer"):
        assert any(
            p.startswith(f"platform/billing/{layer}/") for p in tracked
        ), layer
    assert "platform/billing/persistence/migrations/v0033_billing_consolidation.py" in tracked
    assert "docs/releases/epic9-billing-platform-v1.0.md" in tracked
    assert "docs/adr/ADR-0023-billing-sole-owner-of-revenue.md" in tracked


def test_the_release_contains_no_unrelated_work():
    """§3: unrelated user work must never enter v4.0.0.

    **Retargeted at DL-0139's resolution** (owner ruling, 2026-08-20) from `HEAD` to
    `RELEASE_COMMIT`. The allow-list is untouched and no path is exempted; what changed is *which
    commit* is inspected. Reading `HEAD` asked whether the newest development commit looked like an
    Epic 9 release — a question with no true answer after `8b27a1a` — while the invariant §3
    actually states is about the release commit, which is immutable and still satisfies it.
    """
    # `--name-only`, not `--stat`: git *truncates* long paths in a stat summary
    # (`.../customer/routers/consolidation_engine.py`), so a keyword filter over stat output
    # silently fails to see the very paths it is meant to classify.
    paths = [
        p for p in _git("show", "--name-only", "--format=", RELEASE_COMMIT).splitlines() if p
    ]
    unrelated = [
        p
        for p in paths
        if not any(
            k in p
            for k in (
                "billing", "epic9", "ADR-0023", "ADR-0031", "CHANGELOG",
                "INDEX", "DECISION_LOG", "adr/README", "pyproject",
                "certification", "freeze", "release",
            )
        )
    ]
    assert unrelated == [], unrelated


# ── the release preserves what it claims to preserve ────────────────────────────


def test_the_release_preserves_the_certification_digest():
    assert certify().report_digest == CERTIFICATION_DIGEST


def test_the_release_preserves_the_freeze_manifest_digest():
    manifest = build_manifest(
        repository_wide_tests=4643,
        failed=0,
        skipped=0,
        ruff_clean=True,
        commit_state="uncommitted",
    )
    assert manifest_digest(manifest) == FREEZE_MANIFEST_DIGEST


def test_the_release_preserves_the_migration_head():
    from billing.persistence import migrations

    versions = [m.VERSION for m in migrations.MIGRATIONS]
    assert versions[-1] == "0033"
    assert [v for v in versions if v > "0033"] == []


def test_the_release_preserves_the_certification_controls():
    assert len(CONTROLS_BY_ID) == 27
    blocking = sum(1 for c in CONTROLS_BY_ID.values() if c.severity is ControlSeverity.BLOCKING)
    assert blocking == 24


def test_the_release_preserves_both_advisories():
    """§6: the release must not claim the advisories were fixed."""
    report = certify()
    assert sorted(a.control_id for a in report.advisories) == ["NS-5", "ST-3"]
    assert len(report.blocking_violations) == 0
    assert len(report.inconclusive) == 0


def test_the_release_preserves_all_nine_owned_limitations():
    assert len(DECLARED_LIMITATIONS) == 9
    for limitation in DECLARED_LIMITATIONS:
        assert limitation.owner, limitation.id
    owners = {x.id: x.owner for x in DECLARED_LIMITATIONS}
    assert owners["LIM-1"] == "M9.15"
    assert owners["LIM-7"] == "M9.24"
    assert "M9.26" not in set(owners.values()), "the release took nobody's limitation"


# ── M9.26 introduced no financial behaviour ─────────────────────────────────────


def test_the_release_introduced_no_production_module():
    """M9.26 contributes repository-release metadata only."""
    import pkgutil

    import billing.application as A
    import billing.foundation as F

    for package in (F, A):
        names = {m.name for m in pkgutil.iter_modules(package.__path__)}
        assert not any("release" in n for n in names), names
    assert not Path("billing/application/release_engine.py").exists()


def test_the_release_introduced_no_post_freeze_financial_domain():
    import pkgutil

    import billing.application as A

    engines = {
        m.name for m in pkgutil.iter_modules(A.__path__) if m.name.endswith("_engine")
    }
    assert len(engines) == 15, sorted(engines)


def test_the_release_document_states_the_release_identity():
    doc = (REPO_ROOT / "docs/releases/epic9-billing-platform-v1.0.md").read_text()
    for required in (
        "v4.0.0",
        "Billing Platform v1.0",
        "M9.26",
        "CERTIFIED_WITH_ADVISORIES",
        "FREEZE_WITH_ADVISORIES",
        CERTIFICATION_DIGEST[:16],
        FREEZE_MANIFEST_DIGEST[:16],
        "0033",
        "NS-5",
        "ST-3",
    ):
        assert required in doc, required


def test_the_release_document_does_not_claim_the_advisories_were_fixed():
    doc = (REPO_ROOT / "docs/releases/epic9-billing-platform-v1.0.md").read_text().lower()
    for forbidden in ("advisories fixed", "advisories resolved", "limitations closed"):
        assert forbidden not in doc, forbidden
