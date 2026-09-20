"""Epic 10 Freeze Manifest (M10.14) — the certified state, written down deterministically.

Ratified by owner rulings **R-1 … R-7** and recorded in **DL-0146**. The roadmap specifies §M10.14
exactly:

> Freeze manifest enumerating every Epic 10 module, table, migration and test, plus explicit
> attestation that the frozen boundary is unchanged. Fresh digest; the Epic 9 manifest digest is
> quoted as history, never recomputed. **Freeze does not commit** — that is M10.15.

M10.13 certified the repository. M10.14 proves the tree is **still in that certified state** and
records it. It introduces **no production module, no table, no migration, no route, no port method
and no runtime behaviour** — it reads the tree M10.13 certified and writes down what is there.

## Independence from Epic 9 (R-5)

This module sits **beside** `manifest.py` in the same directory, and that adjacency is filesystem
placement only. It has its own namespace (`EF-`), its own version, its own verdict vocabulary, its
own canonicalizer and its own digest. It **never** imports, invokes, wraps, extends or re-exports
Epic 9's freeze implementation — `tests.freeze.manifest` is not imported at all, and Epic 9's
`FREEZE_MANIFEST_VERSION`, `build_manifest`, `manifest_digest` and `render` are untouched.

Epic 9 data enters in exactly one direction and one form: its **historical digests**, read out of
the `v4.0.0` tree as evidence (R-1). That is an observation of a value, not the execution of Epic
9's freeze.

## The frozen boundary is a **commit range**, not `HEAD` and not the working tree

Every "unchanged since" comparison here runs between two pinned literals —
**`CERTIFIED_COMMIT` → `FREEZE_BOUNDARY_COMMIT`** — and never against `HEAD` or the checkout.

Deriving the near end from `HEAD` would let the manifest drift the moment M10.15 commits. Leaving
the *far* end implicit is the same mistake pointing the other way, and it was the live defect this
module carried until **M11.2.1**: `git diff CERTIFIED_COMMIT -- <path>` with no second ref compares
against **the working tree**, so EF-1 and EF-10 silently changed the question from *"did the freeze
change this?"* to *"has anyone changed this, ever, in any later epic?"* Those are different
questions, and only the first is Epic 10's to ask. EF-11 already stated that distinction
(`EPIC10_FREEZE_BOUNDARY_PATHS`); M11.2.1 finished applying it (**ADR-0052**).

Pinning both ends means this manifest describes the same state forever, and legitimate later
work — governed by its own accepted ADRs and certification gates — cannot make Epic 10
retroactively un-freeze.

## Derived versus declared (honesty, not decoration)

**Derived** values are computed from the repository on every run: the module, table, migration and
test inventory, the migration head, the certification verdict and digest, the billing-exception
state, the Epic 9 historical and current digests. Re-running reproduces them exactly.

**Declared** values cannot be derived by reading anything — the repository-wide suite result, the
ruff results and the commit state are facts about *running* and about *VCS posture*, not about
reading the tree. They are supplied as freeze inputs and recorded as such. A manifest that
pretended to derive them would be claiming a reproducibility it does not have.

Both halves enter the digest.

## Two digests, two rules

The **certification** digest (M10.13) is computed over findings alone and never sees a verdict —
ADR-0043 **CD8**. This module **consumes it unchanged** and never recomputes it.

The **freeze manifest** digest is computed over the whole manifest, verdict included, which is
Epic 9's freeze shape and is not governed by CD8: CD8 scopes the separation to the certification
report, and a freeze manifest whose digest excluded its own verdict could be altered in the one
field that matters without moving the digest.
"""

from __future__ import annotations

import ast
import hashlib
import json
import re
import subprocess
from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

#: Bumped only when a change here would produce a different manifest from the same tree. Distinct
#: from Epic 9's `FREEZE_MANIFEST_VERSION`, which this module neither reads nor redefines (R-5).
EPIC10_FREEZE_VERSION = "m10.14.1"

#: The Epic 10 milestone range this manifest freezes.
EPIC10_FIRST_MILESTONE = "M10.2"
EPIC10_LAST_MILESTONE = "M10.13"

PLATFORM_ROOT = Path(__file__).resolve().parent.parent.parent
REPO_ROOT = PLATFORM_ROOT.parent

#: The Epic 9 release baseline every "since" comparison starts from.
BASELINE_TAG = "v4.0.0"
BASELINE_COMMIT = "532b0bd72d55921fa385a3e4162d899c90fbe6ad"

#: **The frozen boundary.** The M10.13 commit whose tree M10.14 freezes. Pinned as a literal and
#: never read from `HEAD`: a boundary derived from the thing under test cannot detect that thing
#: moving, and would silently absorb M10.15's commit into the frozen inventory.
CERTIFIED_COMMIT = "db8c50828c91567aa9a7c518e58f804593a592f9"

#: **The freeze boundary commit** — the far end of every "did the freeze change this?" range.
#:
#: M10.14 deferred its commit; M10.15 discharged it under DL-0146 **R-2**, so the tree that *is*
#: the frozen state, once committed, is the Epic 10 release commit. Every attestation and test
#: that asks what **the freeze** changed compares `CERTIFIED_COMMIT → FREEZE_BOUNDARY_COMMIT`.
#:
#: This is the same correction EF-11 already carries (see `EPIC10_FREEZE_BOUNDARY_PATHS`): a
#: comparison whose far end is the **working tree** stops being a question about M10.14 the moment
#: anything else happens in the repository. Asked of the live tree, EF-1 and EF-10 could not
#: distinguish *"the freeze changed a frozen plane"* from *"a later, separately governed epic
#: did"* — so they would report `NOT_FROZEN` for work Epic 10 never had any authority over.
#: Pinning both ends makes the answer a permanent fact about history rather than a statement
#: about whoever last edited the checkout (ADR-0052).
FREEZE_BOUNDARY_COMMIT = "3779e932182ed06ec5411c83ba53f0c2b585b8b8"

#: The migration head M10.13 certified. Asserted, never inferred: a freeze that read the head from
#: the chain would happily freeze a chain that had grown.
#:
#: Named `EPIC10_` rather than `CERTIFIED_` because Epic 9's freeze already exports
#: `CERTIFIED_MIGRATION_HEAD` (= "0033"); sharing the name across two `__all__` lists would be the
#: kind of quiet collision R-5's independence boundary exists to prevent.
EPIC10_MIGRATION_HEAD = "0036"
FROZEN_MIGRATION_CEILING = "0033"

#: Epic 9's **original** digests, as certified at `v4.0.0`. Quoted as history and **never
#: recomputed** (R-1). The freeze reads them out of the baseline tree and compares against these
#: literals; it does not restore them into any current artifact.
EPIC9_HISTORICAL_MANIFEST_DIGEST = (
    "eed692224f0aeb82c1a64e8edb39b3b553a709c67cf5d37ae50090bcb466b65c"
)
EPIC9_HISTORICAL_CERTIFICATION_DIGEST = (
    "39790b95d061a7631e6d1b61dab14b227f02038873ee87b5c6a34658bb01d2d8"
)

#: Epic 9's **current** post-M10.10 digests, recorded separately for state distinction (R-1). These
#: are the authorized re-baselined values; they are never presented as the historical ones.
EPIC9_CURRENT_MANIFEST_DIGEST = (
    "cdafb4ab6cba00bcaed0c9b6a3ae477253ce66e492b5b743c9835072704d1fb7"
)
EPIC9_CURRENT_CERTIFICATION_DIGEST = (
    "c044b3f6a23066df4ed97c8b7d0992d8419490ed68dd1b3633b22717f7cab6e6"
)

EPIC9_RELEASE_DOC = "docs/releases/epic9-billing-platform-v1.0.md"

#: Epic 9's freeze implementation and artifacts. M10.14 leaves every one of them exactly as the
#: frozen commit has it.
EPIC9_FREEZE_ARTIFACTS = (
    "platform/tests/freeze/manifest.py",
    "platform/tests/test_billing_freeze.py",
    "platform/tests/certification/catalogue.py",
    "platform/tests/certification/runner.py",
    "platform/tests/test_billing_certification.py",
    "platform/tests/test_billing_release.py",
    EPIC9_RELEASE_DOC,
)

#: The M10.13 certification surface, consumed as evidence and never modified.
M10_13_ARTIFACTS = (
    "platform/tests/certification/epic10.py",
    "platform/tests/test_epic10_certification.py",
)

#: **The M10.14 freeze boundary, stated as data.** The certified commit plus exactly these four
#: paths *is* what M10.14 froze — the three artifacts it authored and the one existing file it was
#: authorized to modify (DL-0146).
#:
#: EF-11 asks whether **the freeze** produced a release artifact, which is a question about this
#: change-set and nothing else. Asking it of the live working tree instead cannot distinguish
#: *"M10.14 created a release document"* from *"M10.15 created the one it was required to create"*,
#: so the freeze would report `NOT_FROZEN` the moment the release it exists to authorise happens.
EPIC10_FREEZE_BOUNDARY_PATHS = (
    ".voxvoice/DECISION_LOG.md",
    "docs/architecture/epic10/freeze.md",
    "platform/tests/freeze/epic10.py",
    "platform/tests/test_epic10_freeze.py",
)

#: The release tag M10.15 owns. EF-11 asserts it did not exist **at the freeze boundary**; it says
#: nothing about whether M10.15 later creates it.
RELEASE_TAG = "v5.0.0"

#: Planes M10.14 must not have touched since the frozen commit.
FROZEN_PLANES = (
    "platform/billing",
    "platform/api",
    "platform/core",
    "platform/runtime",
    "platform/observability",
    "platform/replay",
    "platform/events",
)

#: The exact ruff scopes the Epic 10 DoD defines, recorded with their results. **No repository-wide
#: claim** is made: `ruff check .` reports two pre-existing findings in `voicebot/bot.py`, which is
#: outside M10.14's scope and is not modified (R-7).
DECLARED_RUFF_SCOPES = (
    "ruff check . --exclude voicebot",
    "ruff check billing/ tests/",
)


class Epic10FreezeVerdict(StrEnum):
    """Epic 10's own freeze verdict vocabulary (R-4).

    Epic 9's `FREEZE_WITH_ADVISORIES` is neither imported nor reused. `NOT_FROZEN` follows from any
    violated attestation **or any inconclusive one** — a boundary nobody could verify is not a
    frozen boundary."""

    FROZEN = "frozen"
    FROZEN_WITH_ADVISORIES = "frozen_with_advisories"
    NOT_FROZEN = "not_frozen"


class Epic10FreezeOutcome(StrEnum):
    """Three outcomes, and the third is the point.

    `INCONCLUSIVE` is an admission that the evidence could not be gathered. It is never collapsed
    into `SATISFIED`, and it can never produce `FROZEN`."""

    SATISFIED = "satisfied"
    VIOLATED = "violated"
    INCONCLUSIVE = "inconclusive"


class Epic10FreezeSeverity(StrEnum):
    BLOCKING = "blocking"
    ADVISORY = "advisory"


#: The outcomes that let an attestation pass. `INCONCLUSIVE` is deliberately absent.
FREEZE_PASSING_OUTCOMES: frozenset[Epic10FreezeOutcome] = frozenset(
    {Epic10FreezeOutcome.SATISFIED}
)


@dataclass(frozen=True, slots=True)
class FreezeControl:
    """One frozen-boundary statement, bound to the ruling that requires it."""

    id: str
    severity: Epic10FreezeSeverity
    statement: str
    clauses: tuple[str, ...]

    def __post_init__(self) -> None:
        if not self.clauses:
            raise ValueError(
                f"freeze control {self.id!r} cites no ratified clause; a control without an "
                "authority is an opinion, not an attestation"
            )
        if not self.statement:
            raise ValueError(f"freeze control {self.id!r} has no statement")


@dataclass(frozen=True, slots=True)
class FreezeAttestation:
    """What one attestation found. Immutable."""

    control_id: str
    outcome: Epic10FreezeOutcome
    detail: str = ""
    violations: tuple[str, ...] = ()

    @property
    def passed(self) -> bool:
        return self.outcome in FREEZE_PASSING_OUTCOMES

    def __post_init__(self) -> None:
        if self.outcome is Epic10FreezeOutcome.VIOLATED and not self.violations:
            raise ValueError(
                f"attestation {self.control_id!r} reports a violation with nothing named; "
                "a violation that cannot be located cannot be fixed"
            )
        if self.outcome is Epic10FreezeOutcome.INCONCLUSIVE and not self.detail:
            raise ValueError(
                f"attestation {self.control_id!r} is inconclusive with no reason; "
                "'could not check' must always say why"
            )


# ── the frozen-boundary attestation ─────────────────────────────────────────────

EPIC10_FREEZE_CONTROLS: tuple[FreezeControl, ...] = (
    FreezeControl(
        id="EF-1",
        severity=Epic10FreezeSeverity.BLOCKING,
        statement="Every frozen production plane is unchanged since the certified commit.",
        clauses=("plan §M10.14", "DL-0146"),
    ),
    FreezeControl(
        id="EF-2",
        severity=Epic10FreezeSeverity.BLOCKING,
        statement="The migration head is 0036 and no M10.14 migration exists.",
        clauses=("plan §M10.14", "DL-0146", "ADR-0043 CD13"),
    ),
    FreezeControl(
        id="EF-3",
        severity=Epic10FreezeSeverity.BLOCKING,
        statement="Migrations 0001-0033 remain unchanged since v4.0.0.",
        clauses=("plan §1.1", "plan §4"),
    ),
    FreezeControl(
        id="EF-4",
        severity=Epic10FreezeSeverity.BLOCKING,
        statement="The billing boundary carries exactly the one authorized MB14 delta.",
        clauses=("ADR-0039 Amendment 1 MB14", "ADR-0043 CD5"),
    ),
    FreezeControl(
        id="EF-5",
        severity=Epic10FreezeSeverity.BLOCKING,
        statement="Epic 9's freeze implementation and artifacts are unchanged.",
        clauses=("DL-0146 R-5", "DL-0136"),
    ),
    FreezeControl(
        id="EF-6",
        severity=Epic10FreezeSeverity.BLOCKING,
        statement="The M10.13 certification surface is unchanged and consumed only as evidence.",
        clauses=("DL-0146", "ADR-0043"),
    ),
    FreezeControl(
        id="EF-7",
        severity=Epic10FreezeSeverity.BLOCKING,
        statement="The M10.13 certification verdict is certified with no blocking violation, "
        "no advisory and nothing inconclusive.",
        clauses=("plan §M10.14", "ADR-0043 CD4"),
    ),
    FreezeControl(
        id="EF-8",
        severity=Epic10FreezeSeverity.BLOCKING,
        statement="Epic 9's original manifest and certification digests reproduce from v4.0.0 and "
        "are recorded separately from the current post-M10.10 values.",
        clauses=("plan §M10.14", "DL-0146 R-1", "ADR-0043 CD6"),
    ),
    FreezeControl(
        id="EF-9",
        severity=Epic10FreezeSeverity.BLOCKING,
        statement="Epic 9's nine declared limitations and its advisories NS-5 and ST-3 keep their "
        "owners.",
        clauses=("plan §M10.14", "ADR-0043 CD9"),
    ),
    FreezeControl(
        id="EF-10",
        severity=Epic10FreezeSeverity.BLOCKING,
        statement="The freeze introduces no production module, table, migration or route.",
        clauses=("DL-0146", "DL-0136 freeze principle"),
    ),
    FreezeControl(
        id="EF-11",
        severity=Epic10FreezeSeverity.BLOCKING,
        statement="The freeze creates no release document, release notes or v5.0.0 artifact.",
        clauses=("DL-0146 R-6", "plan §M10.15"),
    ),
    FreezeControl(
        id="EF-12",
        severity=Epic10FreezeSeverity.BLOCKING,
        statement="The v4.0.0 baseline still names its release commit.",
        clauses=("plan §1.1",),
    ),
)

FREEZE_CONTROLS_BY_ID: dict[str, FreezeControl] = {c.id: c for c in EPIC10_FREEZE_CONTROLS}


# ── evidence gathering ──────────────────────────────────────────────────────────


class FreezeEvidenceUnavailable(RuntimeError):
    """Raised when evidence cannot be gathered, so an attestation is INCONCLUSIVE, never a pass."""


def _git(*args: str) -> str:
    try:
        completed = subprocess.run(
            ["git", *args], capture_output=True, text=True, cwd=REPO_ROOT, check=False
        )
    except OSError as exc:  # pragma: no cover — git absent from the machine
        raise FreezeEvidenceUnavailable(f"git could not be executed: {exc}") from exc
    if completed.returncode != 0:
        raise FreezeEvidenceUnavailable(
            f"`git {' '.join(args)}` failed ({completed.returncode}): "
            f"{completed.stderr.strip() or 'no stderr'}"
        )
    return completed.stdout


def _is_ancestor(candidate: str, descendant: str) -> bool:
    """Whether `candidate` is `descendant` or precedes it.

    `git merge-base --is-ancestor` is **inclusive**, which is exactly what EF-11 needs: a tag
    pointing *at* the certified commit existed at the freeze boundary just as surely as one
    pointing behind it. An unresolvable revision returns a non-zero status and is reported as
    "not an ancestor" rather than raising — EF-11's own violation path stays the one that speaks."""
    return (
        subprocess.run(
            ["git", "merge-base", "--is-ancestor", candidate, descendant],
            capture_output=True,
            text=True,
            cwd=REPO_ROOT,
            check=False,
        ).returncode
        == 0
    )


def _require_boundaries() -> None:
    """Both pinned commits must exist and resolve to what they are pinned as."""
    baseline = _git("rev-parse", f"{BASELINE_TAG}^{{commit}}").strip()
    if baseline != BASELINE_COMMIT:
        raise FreezeEvidenceUnavailable(
            f"{BASELINE_TAG} resolves to {baseline}, not the pinned baseline {BASELINE_COMMIT}"
        )
    certified = _git("rev-parse", f"{CERTIFIED_COMMIT}^{{commit}}").strip()
    if certified != CERTIFIED_COMMIT:  # pragma: no cover — a rewritten history
        raise FreezeEvidenceUnavailable(
            f"the certified commit {CERTIFIED_COMMIT} is not resolvable as itself"
        )
    boundary = _git("rev-parse", f"{FREEZE_BOUNDARY_COMMIT}^{{commit}}").strip()
    if boundary != FREEZE_BOUNDARY_COMMIT:  # pragma: no cover — a rewritten history
        raise FreezeEvidenceUnavailable(
            f"the freeze boundary {FREEZE_BOUNDARY_COMMIT} is not resolvable as itself"
        )
    # Ancestry of the range is asserted by its own test against real git, not here: `_is_ancestor`
    # is a documented monkeypatch seam (EF-11's later-tag scenario stubs it), so a guard placed in
    # this helper would fail on a stub rather than on the repository.


def _changed(from_ref: str, to_ref: str | None, *pathspecs: str) -> tuple[tuple[str, str], ...]:
    """`(status, path)` pairs. `to_ref=None` compares against the working tree."""
    args = ["diff", "--name-status", from_ref]
    if to_ref is not None:
        args.append(to_ref)
    raw = _git(*args, "--", *pathspecs)
    changes = [
        (line.split("\t")[0].strip(), line.split("\t")[-1].strip())
        for line in raw.splitlines()
        if line.strip()
    ]
    return tuple(sorted(changes, key=lambda c: c[1]))


def _frozen_inventory() -> dict:
    """Every Epic 10 module, table, migration and test, derived from the frozen boundary.

    Enumerated from `v4.0.0 → CERTIFIED_COMMIT`, so the inventory is the **certified** Epic 10
    tree. M10.14's own freeze artifacts are deliberately absent from it: they are uncommitted at
    the freeze boundary, and the commit-state block is where they are accounted for."""
    _require_boundaries()
    changes = _changed(BASELINE_TAG, CERTIFIED_COMMIT, "platform")

    added_source = sorted(
        p for s, p in changes if s.startswith("A") and "/tests/" not in p
    )
    modified_source = sorted(
        p for s, p in changes if s.startswith("M") and "/tests/" not in p
    )
    added_tests = sorted(p for s, p in changes if s.startswith("A") and "/tests/" in p)
    modified_tests = sorted(p for s, p in changes if s.startswith("M") and "/tests/" in p)
    migrations = sorted(p for p in added_source if "/migrations/v" in p)

    return {
        "modules_added": added_source,
        "modules_modified": modified_source,
        "tests_added": added_tests,
        "tests_modified": modified_tests,
        "migrations": migrations,
        "counts": {
            "modules_added": len(added_source),
            "modules_modified": len(modified_source),
            "tests_added": len(added_tests),
            "tests_modified": len(modified_tests),
            "migrations": len(migrations),
            "platform_paths_touched": len(changes),
        },
    }


def _declared_tables() -> tuple[str, ...]:
    """Epic 10's tables, read structurally from the model declarations."""
    names: list[str] = []
    root = PLATFORM_ROOT / "events" / "persistence"
    for path in sorted(root.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except (OSError, SyntaxError):  # pragma: no cover
            continue
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Assign)
                and any(isinstance(t, ast.Name) and t.id == "__tablename__" for t in node.targets)
                and isinstance(node.value, ast.Constant)
                and isinstance(node.value.value, str)
            ):
                names.append(node.value.value)
    return tuple(sorted(names))


def _declared_indexes() -> tuple[str, ...]:
    names: list[str] = []
    path = PLATFORM_ROOT / "events" / "persistence" / "models.py"
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "Index"
            and node.args
            and isinstance(node.args[0], ast.Constant)
            and isinstance(node.args[0].value, str)
        ):
            names.append(node.args[0].value)
    return tuple(sorted(names))


_MANIFEST_DIGEST_RE = re.compile(r"\*\*Manifest digest:\*\*\s*`([0-9a-f]{64})`")
_CERTIFICATION_DIGEST_RE = re.compile(r"\*\*Certification digest:\*\*\s*`([0-9a-f]{64})`")


def _epic9_digest_states() -> dict:
    """Epic 9's two digest states, each read from its own source and kept apart (R-1).

    The historical pair comes out of the `v4.0.0` tree; the current pair comes out of the working
    tree. They are stored under **different keys** and neither is ever computed from the other —
    `recomputed` is a standing `False`, because this module contains no Epic 9 digest computation
    at all."""
    _require_boundaries()
    historical_doc = _git("show", f"{BASELINE_TAG}:{EPIC9_RELEASE_DOC}")
    current_doc = (REPO_ROOT / EPIC9_RELEASE_DOC).read_text(encoding="utf-8")

    historical_manifest = _MANIFEST_DIGEST_RE.search(historical_doc)
    historical_certification = _CERTIFICATION_DIGEST_RE.search(historical_doc)
    current_manifest = _MANIFEST_DIGEST_RE.search(current_doc)
    current_certification = _CERTIFICATION_DIGEST_RE.search(current_doc)
    if not all((historical_manifest, historical_certification, current_manifest,
                current_certification)):
        raise FreezeEvidenceUnavailable(
            "the Epic 9 release record did not yield both digests in both states"
        )

    return {
        "historical": {
            "source": f"{BASELINE_TAG}:{EPIC9_RELEASE_DOC}",
            "manifest_digest": historical_manifest.group(1),
            "certification_digest": historical_certification.group(1),
        },
        "current": {
            "source": f"working tree:{EPIC9_RELEASE_DOC}",
            "manifest_digest": current_manifest.group(1),
            "certification_digest": current_certification.group(1),
        },
        "recomputed_by_epic10": False,
        "states_are_separate_keys": True,
    }


def _epic9_declared_state() -> tuple[dict[str, str], dict[str, str]]:
    """Epic 9's limitations and control severities, **read as data**.

    Epic 9's freeze implementation is not involved: this reads the certification catalogue's
    values, exactly as ADR-0043 CD2 permits, and executes nothing."""
    from tests.certification.catalogue import CONTROLS_BY_ID, DECLARED_LIMITATIONS

    owners = {limit.id: limit.owner for limit in DECLARED_LIMITATIONS}
    severities = {cid: c.severity.value for cid, c in CONTROLS_BY_ID.items()}
    return owners, severities


def _certification_state() -> dict:
    """The M10.13 verdict and digest, **consumed unchanged**.

    Nothing here recomputes the certification digest: `certify_epic10()` produces it and this
    function copies the value."""
    from tests.certification.epic10 import certify_epic10

    report = certify_epic10()
    return {
        "version": report.certification_version,
        "verdict": report.verdict.value,
        "digest": report.report_digest,
        "control_count": len(report.results),
        "blocking_violations": len(report.blocking_violations),
        "advisories": len(report.advisories),
        "inconclusive": len(report.inconclusive),
        "results": {r.control_id: r.outcome.value for r in report.results},
        "limitations": [
            {"id": limit.id, "owner": limit.owner, "inherits": limit.inherited_from}
            for limit in sorted(report.limitations, key=lambda x: x.id)
        ],
    }


# ── the attestations ────────────────────────────────────────────────────────────


def _ok(control_id: str, detail: str) -> FreezeAttestation:
    return FreezeAttestation(control_id, Epic10FreezeOutcome.SATISFIED, detail=detail)


def _bad(control_id: str, violations: Sequence[str], detail: str) -> FreezeAttestation:
    if violations:
        return FreezeAttestation(
            control_id,
            Epic10FreezeOutcome.VIOLATED,
            detail=detail,
            violations=tuple(sorted(violations)),
        )
    return _ok(control_id, detail)


def _ef_1() -> FreezeAttestation:
    changed = _changed(CERTIFIED_COMMIT, FREEZE_BOUNDARY_COMMIT, *FROZEN_PLANES)
    return _bad(
        "EF-1",
        [f"{p}: frozen plane changed ({s})" for s, p in changed],
        f"{len(FROZEN_PLANES)} frozen plane(s) identical to the certified commit",
    )


def _ef_2() -> FreezeAttestation:
    from events.persistence import migrations

    versions = tuple(m.VERSION for m in migrations.MIGRATIONS)
    violations = []
    if versions[-1] != EPIC10_MIGRATION_HEAD:
        violations.append(f"migration head is {versions[-1]}, expected {EPIC10_MIGRATION_HEAD}")
    added = _changed(CERTIFIED_COMMIT, FREEZE_BOUNDARY_COMMIT, "platform")
    for status, path in added:
        if "/migrations/v" in path:
            violations.append(f"{path}: migration changed by M10.14 ({status})")
    return _bad("EF-2", violations, f"migration head {versions[-1]}; chain {list(versions)}")


def _ef_3() -> FreezeAttestation:
    _require_boundaries()
    changed = _changed(BASELINE_TAG, None, "platform")
    violations = [
        f"{p}: frozen migration changed ({s})"
        for s, p in changed
        if (m := re.search(r"/migrations/v(\d{4})", p)) and m.group(1) <= FROZEN_MIGRATION_CEILING
    ]
    return _bad("EF-3", violations, f"migrations 0001-{FROZEN_MIGRATION_CEILING} unchanged")


def _ef_4() -> FreezeAttestation:
    _require_boundaries()
    changed = _changed(BASELINE_TAG, None, "platform/billing")
    expected = ("M", "platform/billing/foundation/catalog.py")
    violations = [
        f"{p}: unauthorized billing change ({s})" for s, p in changed if (s, p) != expected
    ]
    return _bad(
        "EF-4",
        violations,
        f"{len(changed)} billing path(s) differ from {BASELINE_TAG}; the only permitted delta is "
        "MeterSource.OBSERVABILITY (ADR-0039 Amendment 1 / MB14)",
    )


def _ef_5() -> FreezeAttestation:
    changed = _changed(CERTIFIED_COMMIT, FREEZE_BOUNDARY_COMMIT, *EPIC9_FREEZE_ARTIFACTS)
    return _bad(
        "EF-5",
        [f"{p}: Epic 9 artifact modified by M10.14 ({s})" for s, p in changed],
        f"{len(EPIC9_FREEZE_ARTIFACTS)} Epic 9 freeze/certification artifact(s) untouched",
    )


def _ef_6() -> FreezeAttestation:
    changed = _changed(CERTIFIED_COMMIT, FREEZE_BOUNDARY_COMMIT, *M10_13_ARTIFACTS)
    return _bad(
        "EF-6",
        [f"{p}: M10.13 certification artifact modified ({s})" for s, p in changed],
        f"{len(M10_13_ARTIFACTS)} M10.13 artifact(s) unchanged and consumed as evidence",
    )


def _ef_7() -> FreezeAttestation:
    state = _certification_state()
    violations = []
    if state["verdict"] != "certified":
        violations.append(f"certification verdict is {state['verdict']!r}, expected 'certified'")
    for field in ("blocking_violations", "advisories", "inconclusive"):
        if state[field]:
            violations.append(f"certification reports {state[field]} {field}")
    return _bad(
        "EF-7",
        violations,
        f"certification {state['version']} verdict {state['verdict']} over "
        f"{state['control_count']} control(s)",
    )


def _ef_8() -> FreezeAttestation:
    states = _epic9_digest_states()
    violations = []
    if states["historical"]["manifest_digest"] != EPIC9_HISTORICAL_MANIFEST_DIGEST:
        violations.append("the historical manifest digest does not reproduce from the baseline")
    if states["historical"]["certification_digest"] != EPIC9_HISTORICAL_CERTIFICATION_DIGEST:
        violations.append(
            "the historical certification digest does not reproduce from the baseline"
        )
    if states["current"]["manifest_digest"] != EPIC9_CURRENT_MANIFEST_DIGEST:
        violations.append("the current manifest digest is not the recorded post-M10.10 value")
    if states["current"]["certification_digest"] != EPIC9_CURRENT_CERTIFICATION_DIGEST:
        violations.append("the current certification digest is not the recorded post-M10.10 value")
    if states["historical"]["source"] == states["current"]["source"]:
        violations.append("both digest states name the same source; they cannot be conflated")
    return _bad(
        "EF-8",
        violations,
        f"historical {EPIC9_HISTORICAL_MANIFEST_DIGEST[:16]}… quoted from {BASELINE_TAG}; "
        f"current {EPIC9_CURRENT_MANIFEST_DIGEST[:16]}… recorded separately; never recomputed",
    )


#: Epic 9's nine limitations with the owners they must still carry, and its two advisories.
EPIC9_LIMITATION_OWNERS = {
    "LIM-1": "M9.15", "LIM-2": "M9.16", "LIM-3": "M9.18", "LIM-4": "M9.8", "LIM-5": "M9.8",
    "LIM-6": "M9.13/M9.14", "LIM-7": "M9.24", "LIM-8": "M9.5/M9.6/M9.9/M9.10/M9.11",
    "LIM-9": "M9.8/M9.7",
}
EPIC9_ADVISORIES = ("NS-5", "ST-3")


def _ef_9() -> FreezeAttestation:
    owners, severities = _epic9_declared_state()
    violations = []
    for limitation_id, owner in sorted(EPIC9_LIMITATION_OWNERS.items()):
        if owners.get(limitation_id) != owner:
            violations.append(
                f"{limitation_id}: owner is {owners.get(limitation_id)!r}, expected {owner!r}"
            )
    if len(owners) != len(EPIC9_LIMITATION_OWNERS):
        violations.append(f"Epic 9 declares {len(owners)} limitation(s), expected 9")
    for advisory in EPIC9_ADVISORIES:
        if severities.get(advisory) != "advisory":
            violations.append(f"{advisory}: severity is {severities.get(advisory)!r}")
    return _bad(
        "EF-9",
        violations,
        f"{len(owners)} Epic 9 limitation(s) and {len(EPIC9_ADVISORIES)} advisory control(s) "
        "still owned as declared",
    )


def _ef_10() -> FreezeAttestation:
    """The freeze principle: freeze the certified state, do not improve it."""
    changed = _changed(CERTIFIED_COMMIT, FREEZE_BOUNDARY_COMMIT, "platform")
    violations = [
        f"{p}: the freeze changed a production path ({s})"
        for s, p in changed
        if "/tests/" not in p
    ]
    return _bad(
        "EF-10",
        violations,
        "the freeze introduces no production module, table, migration or route",
    )


def _ef_11() -> FreezeAttestation:
    """Evaluated at the **freeze boundary**, not against the live working tree.

    Two assertions, both permanent facts about what M10.14 did:

    1. **No M10.14 artifact is a release artifact** — none of the four boundary paths lies under
       `docs/releases/`. Epic 9's release record is separately and independently guarded by EF-5,
       so nothing is lost by scoping this one to the freeze's own change-set.
    2. **No `v5.0.0` tag existed at the freeze boundary** — a tag that did would point at the
       certified commit or an ancestor of it. A tag M10.15 later creates points at a *descendant*,
       and is therefore not something the freeze produced.

    The substantive assertion is unchanged: the freeze must not have authored a release. What
    changed is *which state is inspected*, because the working tree stops being evidence about
    M10.14 the moment M10.15 begins its authorized work."""
    violations = [
        f"{p}: the freeze authored a release artifact"
        for p in EPIC10_FREEZE_BOUNDARY_PATHS
        if p.startswith("docs/releases/")
    ]

    if _git("tag", "--list", RELEASE_TAG).strip():
        target = _git("rev-list", "-n", "1", RELEASE_TAG).strip()
        if _is_ancestor(target, CERTIFIED_COMMIT):
            violations.append(
                f"{RELEASE_TAG} -> {target[:12]} existed at the freeze boundary; "
                "M10.15 owns the release"
            )

    return _bad(
        "EF-11",
        violations,
        f"the freeze authored no release document or release notes, and no {RELEASE_TAG} tag "
        "existed at the freeze boundary",
    )


def _ef_12() -> FreezeAttestation:
    resolved = _git("rev-parse", f"{BASELINE_TAG}^{{commit}}").strip()
    violations = (
        [] if resolved == BASELINE_COMMIT else [f"{BASELINE_TAG} resolves to {resolved}"]
    )
    return _bad("EF-12", violations, f"{BASELINE_TAG} still names {BASELINE_COMMIT[:12]}…")


FREEZE_EVALUATORS = {
    "EF-1": _ef_1, "EF-2": _ef_2, "EF-3": _ef_3, "EF-4": _ef_4, "EF-5": _ef_5, "EF-6": _ef_6,
    "EF-7": _ef_7, "EF-8": _ef_8, "EF-9": _ef_9, "EF-10": _ef_10, "EF-11": _ef_11,
    "EF-12": _ef_12,
}


def attest() -> tuple[FreezeAttestation, ...]:
    """Run every frozen-boundary attestation.

    An evaluator that raises becomes `INCONCLUSIVE`, never a pass."""
    results = []
    for control_id, evaluator in FREEZE_EVALUATORS.items():
        try:
            results.append(evaluator())
        except Exception as exc:  # noqa: BLE001 — any failure to look is inconclusive
            results.append(
                FreezeAttestation(
                    control_id,
                    Epic10FreezeOutcome.INCONCLUSIVE,
                    detail=f"{type(exc).__name__}: {exc}",
                )
            )
    return tuple(sorted(results, key=lambda r: (len(r.control_id), r.control_id)))


def freeze_verdict(attestations: Sequence[FreezeAttestation]) -> Epic10FreezeVerdict:
    """`NOT_FROZEN` on any blocking violation **or any inconclusive blocking attestation** (R-4).

    Treating `INCONCLUSIVE` as a pass would freeze exactly the boundaries nobody could inspect."""
    produced = {a.control_id for a in attestations}
    missing = sorted(set(FREEZE_CONTROLS_BY_ID) - produced)
    if missing:
        raise ValueError(
            f"the freeze is incomplete: {missing} produced no attestation. "
            "An attestation that did not run has not passed."
        )
    unknown = sorted(produced - set(FREEZE_CONTROLS_BY_ID))
    if unknown:
        raise ValueError(f"the freeze produced attestations for unknown controls: {unknown}")

    blocking_failed = False
    advisory_failed = False
    for attestation in attestations:
        if attestation.passed:
            continue
        control = FREEZE_CONTROLS_BY_ID[attestation.control_id]
        if control.severity is Epic10FreezeSeverity.BLOCKING:
            blocking_failed = True
        else:
            advisory_failed = True
    if blocking_failed:
        return Epic10FreezeVerdict.NOT_FROZEN
    if advisory_failed:
        return Epic10FreezeVerdict.FROZEN_WITH_ADVISORIES
    return Epic10FreezeVerdict.FROZEN


def build_freeze_manifest(
    *,
    repository_wide_tests: int,
    failed: int,
    skipped: int,
    ruff_scopes: Sequence[str] = DECLARED_RUFF_SCOPES,
    ruff_clean: bool = True,
    certified_implementation_commit: str = CERTIFIED_COMMIT,
) -> dict:
    """The full manifest: derived inventory and attestation, plus the declared freeze inputs.

    `repository_wide_tests`, `failed`, `skipped` and the ruff results are **declared** — they are
    facts about *running* the suite and the linter, not about reading the tree.

    The **commit state** is declared too, and it is deliberately two facts rather than one: the
    certified implementation is committed, and M10.14's own freeze artifacts are not. Recording it
    as a single classification, the way a single-commit epic could, would lose exactly the
    distinction that makes the freeze honest."""
    attestations = attest()
    return {
        "manifest_version": EPIC10_FREEZE_VERSION,
        "freeze_verdict": freeze_verdict(attestations).value,
        "declared_freeze_inputs": {
            "repository_wide_tests": repository_wide_tests,
            "failed": failed,
            "skipped": skipped,
            "ruff": {
                "clean": ruff_clean,
                "scopes": list(ruff_scopes),
                "repository_wide_claim": False,
                "note": "`ruff check .` is NOT clean: voicebot/bot.py carries two pre-existing "
                "findings (I001, F401). That file is outside M10.14 scope and is not modified. "
                "Cleanliness is claimed only for the scopes listed above.",
            },
            "commit_state": {
                "kind": "freeze_boundary_evidence",
                "certified_implementation": "committed",
                "certified_implementation_commit": certified_implementation_commit,
                "freeze_artifacts": "uncommitted",
                "freeze_artifacts_committed_by": "M10.15",
                "semantics": "Records the repository as it stood at the M10.14 freeze boundary. "
                "It is historical evidence, not a present-tense claim: once M10.15 commits the "
                "freeze artifacts, this remains what was true at freeze.",
            },
        },
        "derived": {
            "epic": {
                "first_milestone": EPIC10_FIRST_MILESTONE,
                "last_milestone": EPIC10_LAST_MILESTONE,
                "baseline_tag": BASELINE_TAG,
                "baseline_commit": BASELINE_COMMIT,
                "certified_commit": CERTIFIED_COMMIT,
            },
            "inventory": _frozen_inventory(),
            "database": {
                "migration_head": EPIC10_MIGRATION_HEAD,
                "tables": list(_declared_tables()),
                "indexes": list(_declared_indexes()),
                "table_count": len(_declared_tables()),
                "index_count": len(_declared_indexes()),
                "all_evt_prefixed": all(t.startswith("evt_") for t in _declared_tables())
                and all(i.startswith("ix_evt_") for i in _declared_indexes()),
            },
            "certification": _certification_state(),
            "epic9_digest_states": _epic9_digest_states(),
            "attestation": {
                "control_count": len(EPIC10_FREEZE_CONTROLS),
                "results": {a.control_id: a.outcome.value for a in attestations},
                "violations": {
                    a.control_id: list(a.violations) for a in attestations if a.violations
                },
                "inconclusive": [
                    a.control_id
                    for a in attestations
                    if a.outcome is Epic10FreezeOutcome.INCONCLUSIVE
                ],
            },
        },
    }


def freeze_manifest_digest(manifest: dict) -> str:
    """Content digest over the **whole** manifest, verdict included.

    Epic 10's own canonicalizer — sorted keys, compact separators, UTF-8, SHA-256 — declared here
    rather than borrowed, because R-5 makes this module independent of Epic 9's.

    The verdict is deliberately **inside** the digest, unlike the M10.13 certification digest which
    ADR-0043 CD8 keeps verdict-free. The two artifacts answer different questions: a certification
    digest records *what was found* so that evidence and interpretation stay separable, while a
    freeze manifest records *the frozen state as declared* — and one whose digest ignored its own
    verdict could be altered in the single field that matters without moving."""
    canonical = json.dumps(manifest, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def render_freeze(manifest: dict) -> str:
    """The manifest as stable text. Deterministic: sorted keys, no clock, no path, no host."""
    lines: list[str] = []

    def walk(node: object, prefix: str = "") -> None:
        if isinstance(node, dict):
            for key in sorted(node):
                walk(node[key], f"{prefix}{key}.")
        elif isinstance(node, list):
            for index, item in enumerate(node):
                walk(item, f"{prefix}{index}.")
        else:
            lines.append(f"{prefix.rstrip('.')}={node}")

    walk(manifest)
    lines.append(f"freeze_manifest_digest={freeze_manifest_digest(manifest)}")
    return "\n".join(lines)


__all__ = [
    "BASELINE_COMMIT",
    "BASELINE_TAG",
    "CERTIFIED_COMMIT",
    "EPIC10_MIGRATION_HEAD",
    "DECLARED_RUFF_SCOPES",
    "EPIC9_ADVISORIES",
    "EPIC9_CURRENT_CERTIFICATION_DIGEST",
    "EPIC9_CURRENT_MANIFEST_DIGEST",
    "EPIC9_FREEZE_ARTIFACTS",
    "EPIC9_HISTORICAL_CERTIFICATION_DIGEST",
    "EPIC9_HISTORICAL_MANIFEST_DIGEST",
    "EPIC9_LIMITATION_OWNERS",
    "EPIC10_FIRST_MILESTONE",
    "EPIC10_FREEZE_CONTROLS",
    "EPIC10_FREEZE_VERSION",
    "EPIC10_LAST_MILESTONE",
    "FREEZE_CONTROLS_BY_ID",
    "FREEZE_EVALUATORS",
    "FREEZE_PASSING_OUTCOMES",
    "Epic10FreezeOutcome",
    "Epic10FreezeSeverity",
    "Epic10FreezeVerdict",
    "FreezeAttestation",
    "FreezeControl",
    "FreezeEvidenceUnavailable",
    "attest",
    "build_freeze_manifest",
    "freeze_manifest_digest",
    "freeze_verdict",
    "render_freeze",
]
