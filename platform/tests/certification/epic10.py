"""Epic 10 Certification (M10.13) — a fresh control set, independent of Epic 9's.

Ratified by **ADR-0043**. The roadmap specifies §M10.13 exactly:

> Epic 10 defines its **own** control set with fresh identifiers; it does not renumber, reuse or
> extend Epic 9's 27, and does not re-run or reinterpret them.

So this module owns one question, and it is not Epic 9's:

> **Did Epic 10 stay inside the boundaries it was authorized to work within?**

## Independence from Epic 9 (ADR-0043 CD2)

This module sits **beside** `catalogue.py` and `runner.py` in the same directory, and that
adjacency is filesystem placement only. It declares its **own** controls, its **own** limitations
and its **own** version constant. It never adds to, renumbers, reuses, wraps or re-exports
`CERTIFICATION_CONTROLS`, `CONTROLS_BY_ID`, `DECLARED_LIMITATIONS` or `CERTIFICATION_VERSION`, and
it **never invokes an Epic 9 evaluator** — Epic 9's `runner` is not imported at all.

Epic 9's catalogue *is* read, in exactly one place and in exactly one direction: `E10-11` and
`E10-13` read `DECLARED_LIMITATIONS` and `CONTROLS_BY_ID` as **evidence about Epic 9's declared
state**. That is an observation of data, not the execution of a control (**CD3**), and it is what
lets Epic 10 certify that Epic 9's limitations and advisories are still declared with their owners
intact.

The shared *shape* — four outcomes, three verdicts, a content digest — is inherited by
**restatement in Epic 10's own vocabulary**, never by mutating Epic 9's definitions.

## The two historical exceptions

Two of §M10.13's thirteen named minimum controls are **false as literally worded**, because of
changes the owner authorized:

* `billing/**` gained the additive `MeterSource.OBSERVABILITY` member (ADR-0039 Amendment 1,
  **MB14**, M10.10). `E10-1` admits **exactly that one member**, named by file, symbol and
  authorizing decision — a narrow historical exception, not a general billing exception
  (**CD5**).
* The Epic 9 release record's digests were re-baselined at M10.10. `E10-10` proves the **original**
  values remain reproducible from the `v4.0.0` baseline, and **never reverts** the current
  authorized artifacts to recreate them (**CD6**).

A certification control that fails on authorized state is a defective control, not a discovered
defect — and deleting the control instead would be certifying by omission.

## Reads, never writes (CD12)

There is no table, no migration, no repository, no command bus, no port method and no production
wiring here. The report is a value, regenerated from the current tree, exactly as Epic 9 argued: a
stored verdict is a staler answer than re-running the suite.
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

#: Bumped when a change here would produce a different verdict from the same repository. Distinct
#: from Epic 9's `CERTIFICATION_VERSION`, which this module neither reads nor redefines (CD1).
EPIC10_CERTIFICATION_VERSION = "m10.13.1"

#: `platform/` — resolved from this module rather than from the working directory, so certification
#: answers the same wherever it is invoked from.
PLATFORM_ROOT = Path(__file__).resolve().parent.parent.parent

#: The git repository root, one level above the platform package.
REPO_ROOT = PLATFORM_ROOT.parent

#: The baseline every "unchanged since" control compares against. Pinned as literals, never derived
#: from the tag or from `HEAD`: a value read from the thing under test cannot detect that thing
#: moving. (The same argument Epic 9's release suite makes for `RELEASE_COMMIT`.)
BASELINE_TAG = "v4.0.0"
BASELINE_COMMIT = "532b0bd72d55921fa385a3e4162d899c90fbe6ad"

#: The **original** Epic 9 digests, as certified at `v4.0.0` and before the M10.10 re-baseline.
#: `E10-10` reproduces these from the baseline tree; it does not restore them into any current
#: artifact (ADR-0043 CD6).
EPIC9_HISTORICAL_MANIFEST_DIGEST = (
    "eed692224f0aeb82c1a64e8edb39b3b553a709c67cf5d37ae50090bcb466b65c"
)
EPIC9_HISTORICAL_CERTIFICATION_DIGEST = (
    "39790b95d061a7631e6d1b61dab14b227f02038873ee87b5c6a34658bb01d2d8"
)

#: The Epic 9 release record, the file both digest states are read from.
EPIC9_RELEASE_DOC = "docs/releases/epic9-billing-platform-v1.0.md"

#: Epic 9 certification, freeze and release artifacts. M10.13 must leave every one of them exactly
#: as `HEAD` has it (ADR-0043 CD16).
EPIC9_FROZEN_ARTIFACTS = (
    "platform/tests/certification/catalogue.py",
    "platform/tests/certification/runner.py",
    "platform/tests/test_billing_certification.py",
    "platform/tests/test_billing_freeze.py",
    "platform/tests/test_billing_release.py",
    f"{EPIC9_RELEASE_DOC}",
)

#: Epic 10's own migration versions. The chain the epic owns, and its floor.
EPIC10_MIGRATION_FLOOR = "0034"
FROZEN_MIGRATION_CEILING = "0033"


@dataclass(frozen=True, slots=True)
class BillingChangeException:
    """One exactly-named, already-authorized `billing/**` change.

    Three fields, all load-bearing: the **path** confines it to one file, the **symbol** confines
    it to one addition inside that file, and the **authority** names the decision that permitted
    it. A path glob, a file-level waiver or a diff-size tolerance would each turn a historical
    exception into a standing permission — which ADR-0043 CD5 forbids and CR4 rejects by name."""

    path: str
    symbol: str
    value: str
    authority: str


#: The **only** permitted `billing/**` delta since `v4.0.0` (ADR-0043 CD5; ADR-0039 Amendment 1
#: MB14, authorized at M10.10). Closed, exact, one member. **It does not grow.**
MB14_BILLING_EXCEPTION = BillingChangeException(
    path="platform/billing/foundation/catalog.py",
    symbol="OBSERVABILITY",
    value="observability",
    authority="ADR-0039 Amendment 1 / MB14",
)

PERMITTED_BILLING_EXCEPTIONS: tuple[BillingChangeException, ...] = (MB14_BILLING_EXCEPTION,)


class Epic10ControlFamily(StrEnum):
    """Which ratified boundary a control certifies against.

    A closed vocabulary, so *"what authority says this?"* is answerable from the control alone.
    Epic 10's families are its own; they are not Epic 9's renamed."""

    FROZEN_PLANE = "frozen_plane"  # planes Epic 10 was forbidden to touch
    SCHEMA = "schema"  # migrations and table/index naming
    VOCABULARY = "vocabulary"  # EventType and metric namespaces
    HISTORY = "history"  # what the record must still be able to show
    GOVERNANCE = "governance"  # declared debt, ownership, honest reporting


class Epic10ControlSeverity(StrEnum):
    """Whether a violation blocks certification.

    `BLOCKING` means Epic 10 is **not certified** while it stands. `ADVISORY` records something a
    reader should know that does not by itself invalidate the boundary — and an advisory is never
    silently dropped, because "nobody mentioned it" is how debt becomes permanent."""

    BLOCKING = "blocking"
    ADVISORY = "advisory"


class Epic10ControlOutcome(StrEnum):
    """Four outcomes, and the last two are the point.

    `NOT_APPLICABLE` is a positive statement: the control was evaluated and the condition it guards
    cannot arise. `INCONCLUSIVE` is an admission: the evidence could not be gathered — a missing
    tag, an unreadable tree — so nothing is claimed. Neither collapses into `SATISFIED`."""

    SATISFIED = "satisfied"
    VIOLATED = "violated"
    NOT_APPLICABLE = "not_applicable"
    INCONCLUSIVE = "inconclusive"


#: The outcomes that let a control pass. `INCONCLUSIVE` is deliberately absent (ADR-0043 CD4/CI6):
#: a control that could not look has not passed, and a suite that treated it as a pass would be
#: certifying its own blind spots.
EPIC10_PASSING_OUTCOMES: frozenset[Epic10ControlOutcome] = frozenset(
    {Epic10ControlOutcome.SATISFIED, Epic10ControlOutcome.NOT_APPLICABLE}
)


@dataclass(frozen=True, slots=True)
class Epic10Control:
    """One certifiable statement, bound to the clause that ratified it.

    `clauses` is never empty. A control with no ratified authority behind it is somebody's opinion
    rendered as a test, and this suite exists precisely to distinguish the two."""

    id: str
    family: Epic10ControlFamily
    severity: Epic10ControlSeverity
    statement: str
    clauses: tuple[str, ...]

    def __post_init__(self) -> None:
        if not self.clauses:
            raise ValueError(
                f"control {self.id!r} cites no ratified clause; a control without an authority "
                "is an opinion, not a certification"
            )
        if not self.statement:
            raise ValueError(f"control {self.id!r} has no statement")


@dataclass(frozen=True, slots=True)
class Epic10ControlResult:
    """What one control found. Immutable.

    `violations` names the offending path, symbol or value — never a count alone. A result that
    says "3 violations" and cannot say which three is not actionable."""

    control_id: str
    outcome: Epic10ControlOutcome
    detail: str = ""
    violations: tuple[str, ...] = ()
    examined: int = 0

    @property
    def passed(self) -> bool:
        return self.outcome in EPIC10_PASSING_OUTCOMES

    def __post_init__(self) -> None:
        if self.outcome is Epic10ControlOutcome.VIOLATED and not self.violations:
            raise ValueError(
                f"control {self.control_id!r} reports a violation with nothing named; "
                "a violation that cannot be located cannot be fixed"
            )
        if self.outcome is Epic10ControlOutcome.INCONCLUSIVE and not self.detail:
            raise ValueError(
                f"control {self.control_id!r} is inconclusive with no reason; "
                "'could not check' must always say why"
            )


class Epic10Verdict(StrEnum):
    """The honest verdict.

    `NOT_CERTIFIED` follows from any blocking violation **or any inconclusive blocking control** —
    a boundary nobody could verify is not a boundary."""

    CERTIFIED = "certified"
    CERTIFIED_WITH_ADVISORIES = "certified_with_advisories"
    NOT_CERTIFIED = "not_certified"


@dataclass(frozen=True, slots=True)
class Epic10DeclaredLimitation:
    """A limitation Epic 10 carries, with the milestone that owns closing it.

    `inherited_from` names an Epic 9 limitation this one inherits **honestly** rather than
    re-owns: Epic 10 states the same condition in its own vocabulary and leaves Epic 9's entry,
    and its owner, untouched (ADR-0043 CD10)."""

    id: str
    milestone: str
    owner: str
    statement: str
    inherited_from: str = ""
    self_administered: bool = False

    def __post_init__(self) -> None:
        if not self.owner:
            raise ValueError(f"limitation {self.id!r} names no owning milestone")
        if not self.statement:
            raise ValueError(f"limitation {self.id!r} has no statement")


#: What Epic 10 knows it does not do. Asserted non-empty and fully-owned: an epic that claims zero
#: limitations at certification is an epic that stopped looking.
EPIC10_DECLARED_LIMITATIONS: tuple[Epic10DeclaredLimitation, ...] = (
    Epic10DeclaredLimitation(
        id="E10-LIM-1",
        milestone="M10.13",
        owner="M10.13",
        statement="Epic 10's certification is self-administered: the same author implemented the "
        "architecture and the suite that certifies it. Inherited honestly from Epic 9's LIM-7, "
        "which keeps its own statement and its own owner.",
        inherited_from="LIM-7",
        self_administered=True,
    ),
    Epic10DeclaredLimitation(
        id="E10-LIM-2",
        milestone="M10.13",
        owner="Epic 11",
        statement="The controls are structural, not behavioural. They certify that boundaries, "
        "prefixes, vocabularies and artifacts are what they claim to be; they certify nothing "
        "about the durable spine's behaviour under load, in production, or against real "
        "providers.",
    ),
    Epic10DeclaredLimitation(
        id="E10-LIM-3",
        milestone="M10.13",
        owner="M10.13",
        statement="Every 'unchanged since' control depends on the v4.0.0 tag remaining reachable. "
        "In a clone without tags or history those controls are INCONCLUSIVE — which does not "
        "pass, by design.",
    ),
    Epic10DeclaredLimitation(
        id="E10-LIM-4",
        milestone="M10.13",
        owner="separate governance decision",
        statement="The three structural Epic 9 release-suite assertions recorded in DL-0139 are "
        "not resolved here. Roadmap §M10.13 states that adding an Epic 10 suite does not resolve "
        "them; they require their own Owner-authorized governance decision.",
    ),
)


# ── the control catalogue ───────────────────────────────────────────────────────
#
# Declared as data rather than as a list of test functions, so "what is certified?" is answerable
# by reading one table — and so a control cannot be quietly deleted to make a suite green: the
# catalogue is asserted complete, and every control in it must produce a result.

EPIC10_CERTIFICATION_CONTROLS: tuple[Epic10Control, ...] = (
    Epic10Control(
        id="E10-1",
        family=Epic10ControlFamily.FROZEN_PLANE,
        severity=Epic10ControlSeverity.BLOCKING,
        statement="`platform/billing/**` is unchanged since v4.0.0 except the single additive "
        "`MeterSource.OBSERVABILITY` member authorized by ADR-0039 Amendment 1 (MB14).",
        clauses=("plan §M10.13", "ADR-0043 CD5", "ADR-0039 Amendment 1 MB14"),
    ),
    Epic10Control(
        id="E10-2",
        family=Epic10ControlFamily.SCHEMA,
        severity=Epic10ControlSeverity.BLOCKING,
        statement="Migrations 0001–0033 are unchanged since v4.0.0, and every Epic 10 migration "
        "is numbered 0034 or higher.",
        clauses=("plan §M10.13", "ADR-0043 CD13", "ADR-0031 DD4"),
    ),
    Epic10Control(
        id="E10-3",
        family=Epic10ControlFamily.SCHEMA,
        severity=Epic10ControlSeverity.BLOCKING,
        statement="Every table and index Epic 10 creates is `evt_`-prefixed.",
        clauses=("plan §M10.13", "ADR-0034 DS9"),
    ),
    Epic10Control(
        id="E10-4",
        family=Epic10ControlFamily.VOCABULARY,
        severity=Epic10ControlSeverity.BLOCKING,
        statement="Every `EventType` member present at v4.0.0 is unchanged in name and value; "
        "Epic 10's additions are additive only.",
        clauses=("plan §M10.13", "AR-32"),
    ),
    Epic10Control(
        id="E10-5",
        family=Epic10ControlFamily.VOCABULARY,
        severity=Epic10ControlSeverity.BLOCKING,
        statement="Epic 10 defines no `metric.billing.*` event type; the namespace stays reserved "
        "to Billing.",
        clauses=("plan §M10.13", "ADR-0033 MS9", "ADR-0031 IR9"),
    ),
    Epic10Control(
        id="E10-6",
        family=Epic10ControlFamily.FROZEN_PLANE,
        severity=Epic10ControlSeverity.BLOCKING,
        statement="Billing holds no `EventBus` subscription and never subscribes to a runtime bus.",
        clauses=("plan §M10.13", "ADR-0039 MB1", "ADR-0031 IR4"),
    ),
    Epic10Control(
        id="E10-7",
        family=Epic10ControlFamily.FROZEN_PLANE,
        severity=Epic10ControlSeverity.BLOCKING,
        statement="No `platform/api/**` file that existed at v4.0.0 has been modified or deleted; "
        "Epic 10's api-side work is additions only.",
        clauses=("plan §M10.13", "ADR-0043 CD7"),
    ),
    Epic10Control(
        id="E10-8",
        family=Epic10ControlFamily.FROZEN_PLANE,
        severity=Epic10ControlSeverity.BLOCKING,
        statement="`platform/replay/**` is unchanged since v4.0.0.",
        clauses=("plan §M10.13",),
    ),
    Epic10Control(
        id="E10-9",
        family=Epic10ControlFamily.FROZEN_PLANE,
        severity=Epic10ControlSeverity.BLOCKING,
        statement="Every `platform/api/**observability*` file is unchanged since v4.0.0.",
        clauses=("plan §M10.13",),
    ),
    Epic10Control(
        id="E10-10",
        family=Epic10ControlFamily.HISTORY,
        severity=Epic10ControlSeverity.BLOCKING,
        statement="Epic 9's original manifest and certification digests remain reproducible from "
        "the v4.0.0 baseline, the current post-M10.10 values are recorded separately, and no "
        "current Epic 9 artifact was modified to recreate the historical ones.",
        clauses=("plan §M10.13", "ADR-0043 CD6", "ADR-0043 CD16"),
    ),
    Epic10Control(
        id="E10-11",
        family=Epic10ControlFamily.GOVERNANCE,
        severity=Epic10ControlSeverity.BLOCKING,
        statement="Epic 9's advisories NS-5 and ST-3 and all nine declared limitations are still "
        "declared, still open, and still carry their existing owners.",
        clauses=("plan §M10.13", "ADR-0043 CD9"),
    ),
    Epic10Control(
        id="E10-12",
        family=Epic10ControlFamily.GOVERNANCE,
        severity=Epic10ControlSeverity.BLOCKING,
        statement="Digest and verdict are computed independently: the digest reads findings alone, "
        "the verdict reads outcomes and severities alone, and neither derives from the other.",
        clauses=("plan §M10.13", "ADR-0043 CD8"),
    ),
    Epic10Control(
        id="E10-13",
        family=Epic10ControlFamily.GOVERNANCE,
        severity=Epic10ControlSeverity.BLOCKING,
        statement="LIM-7 is inherited honestly: Epic 10 declares its certification "
        "self-administered in its own vocabulary, and Epic 9's LIM-7 keeps its statement and its "
        "owner.",
        clauses=("plan §M10.13", "ADR-0043 CD10"),
    ),
)

EPIC10_CONTROLS_BY_ID: dict[str, Epic10Control] = {
    c.id: c for c in EPIC10_CERTIFICATION_CONTROLS
}


class Epic10CertificationService:
    """Pure. Turns control results into a verdict, and nothing else.

    No I/O, no clock, no digest: evidence gathering belongs to the evaluators, and the digest is a
    separate function that this class never consults (ADR-0043 CD8)."""

    @staticmethod
    def verdict(results: Sequence[Epic10ControlResult]) -> Epic10Verdict:
        """A blocking control that could not be evaluated fails certification.

        The alternative — treating `INCONCLUSIVE` as a pass — would let the suite certify exactly
        the boundaries it was unable to inspect."""
        blocking_failed = False
        advisory_failed = False
        for result in results:
            control = EPIC10_CONTROLS_BY_ID.get(result.control_id)
            if control is None:
                raise ValueError(
                    f"result cites unknown control {result.control_id!r}; the catalogue is the "
                    "authority on what is certified"
                )
            if result.passed:
                continue
            if control.severity is Epic10ControlSeverity.BLOCKING:
                blocking_failed = True
            else:
                advisory_failed = True
        if blocking_failed:
            return Epic10Verdict.NOT_CERTIFIED
        if advisory_failed:
            return Epic10Verdict.CERTIFIED_WITH_ADVISORIES
        return Epic10Verdict.CERTIFIED

    @staticmethod
    def guard_complete(results: Sequence[Epic10ControlResult]) -> None:
        """Every declared control must produce a result.

        Without this, deleting a control and its result would silently shrink what "certified"
        means — the cheapest possible way to make a certification suite green."""
        produced = {r.control_id for r in results}
        missing = sorted(set(EPIC10_CONTROLS_BY_ID) - produced)
        if missing:
            raise ValueError(
                f"Epic 10 certification is incomplete: {missing} produced no result. "
                "A control that did not run has not passed."
            )
        unknown = sorted(produced - set(EPIC10_CONTROLS_BY_ID))
        if unknown:
            raise ValueError(
                f"Epic 10 certification produced results for unknown controls: {unknown}"
            )

    @classmethod
    def report(cls, results: Sequence[Epic10ControlResult]) -> Epic10CertificationReport:
        """The one place digest and verdict meet — each computed from its own input.

        Composition is not derivation: `verdict()` never sees a digest and
        `epic10_certification_digest()` never sees a verdict."""
        cls.guard_complete(results)
        ordered = tuple(sorted(results, key=lambda r: r.control_id))
        return Epic10CertificationReport(
            verdict=cls.verdict(ordered),
            results=ordered,
            report_digest=epic10_certification_digest(ordered),
        )


def epic10_certification_digest(results: Sequence[Epic10ControlResult]) -> str:
    """A content digest over **what was found**.

    Deliberately excludes the clock, the host, the process and the run order — the same repository
    certifies to the same digest on any machine, forever. It also excludes the **verdict**, which
    is what keeps evidence and interpretation separable (ADR-0043 CD8): two runs that found the
    same thing digest identically, whatever anyone concludes from it."""
    payload = [
        {
            "control": r.control_id,
            "outcome": r.outcome.value,
            "violations": sorted(r.violations),
            "examined": r.examined,
        }
        for r in sorted(results, key=lambda r: r.control_id)
    ]
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class Epic10CertificationReport:
    """The full result. Content-addressed on what it examined, never on when it ran."""

    verdict: Epic10Verdict
    results: tuple[Epic10ControlResult, ...]
    certification_version: str = EPIC10_CERTIFICATION_VERSION
    report_digest: str = ""
    limitations: tuple[Epic10DeclaredLimitation, ...] = EPIC10_DECLARED_LIMITATIONS

    @property
    def blocking_violations(self) -> tuple[Epic10ControlResult, ...]:
        return tuple(
            r
            for r in self.results
            if r.outcome is Epic10ControlOutcome.VIOLATED
            and EPIC10_CONTROLS_BY_ID[r.control_id].severity is Epic10ControlSeverity.BLOCKING
        )

    @property
    def advisories(self) -> tuple[Epic10ControlResult, ...]:
        return tuple(
            r
            for r in self.results
            if r.outcome is Epic10ControlOutcome.VIOLATED
            and EPIC10_CONTROLS_BY_ID[r.control_id].severity is Epic10ControlSeverity.ADVISORY
        )

    @property
    def inconclusive(self) -> tuple[Epic10ControlResult, ...]:
        return tuple(r for r in self.results if r.outcome is Epic10ControlOutcome.INCONCLUSIVE)

    @property
    def is_certified(self) -> bool:
        return self.verdict is not Epic10Verdict.NOT_CERTIFIED


# ── evidence gathering ──────────────────────────────────────────────────────────


class GitUnavailable(RuntimeError):
    """Raised when the baseline cannot be read, so a control reports INCONCLUSIVE, never a pass."""


def _git(*args: str) -> str:
    """Run git at the repository root and return stdout, or raise `GitUnavailable`.

    Every failure mode — git missing, tag absent, object unreadable — arrives here as an exception
    and leaves the calling control `INCONCLUSIVE`. None of them can become a pass."""
    try:
        completed = subprocess.run(
            ["git", *args],
            capture_output=True,
            text=True,
            cwd=REPO_ROOT,
            check=False,
        )
    except OSError as exc:  # pragma: no cover — git absent from the machine
        raise GitUnavailable(f"git could not be executed: {exc}") from exc
    if completed.returncode != 0:
        raise GitUnavailable(
            f"`git {' '.join(args)}` failed ({completed.returncode}): "
            f"{completed.stderr.strip() or 'no stderr'}"
        )
    return completed.stdout


def _require_baseline() -> None:
    """The baseline tag must exist and must still name the commit it named at release."""
    resolved = _git("rev-parse", f"{BASELINE_TAG}^{{commit}}").strip()
    if resolved != BASELINE_COMMIT:
        raise GitUnavailable(
            f"{BASELINE_TAG} resolves to {resolved}, not the pinned baseline {BASELINE_COMMIT}"
        )


def _changed_since_baseline(*pathspecs: str) -> tuple[tuple[str, str], ...]:
    """`(status, path)` for everything under `pathspecs` that differs from the baseline.

    Compares the baseline against the **working tree**, not against `HEAD`: certification is a
    statement about the repository as it currently stands, and an uncommitted violation is still a
    violation."""
    _require_baseline()
    return _name_status(_git("diff", "--name-status", BASELINE_TAG, "--", *pathspecs))


def _name_status(raw: str) -> tuple[tuple[str, str], ...]:
    """Parse `git diff --name-status` output into sorted `(status, path)` pairs."""
    changes = []
    for line in raw.splitlines():
        if not line.strip():
            continue
        parts = line.split("\t")
        changes.append((parts[0].strip(), parts[-1].strip()))
    return tuple(sorted(changes, key=lambda c: c[1]))


#: **The M10.13 certification commit and its parent** — the range that *is* M10.13.
CERTIFICATION_COMMIT = "db8c50828c91567aa9a7c518e58f804593a592f9"
CERTIFICATION_PARENT = "8ee7d3c1bf57ae862307c357c75e4c2008665e0f"


def _changed_by_the_certification(*pathspecs: str) -> tuple[str, ...]:
    """Paths under `pathspecs` that **the M10.13 certification commit** changed.

    Asked between two pinned commits (M11.2.1, **ADR-0052**). The original form compared `HEAD`
    against the **working tree**, which answers *"is anyone editing these right now?"* — a question
    about today's checkout, not about M10.13. Under that form any later epic authorized to touch a
    path here would retroactively un-certify Epic 10, which is not a power Epic 10 has: later work
    is governed by its own accepted ADRs and its own certification gates.

    The claim this control makes — *"M10.13 changed no Epic 9 artifact"* — is a permanent fact
    about one commit, and is now stated as one."""
    raw = _git("diff", "--name-only", CERTIFICATION_PARENT, CERTIFICATION_COMMIT, "--", *pathspecs)
    return tuple(sorted(p.strip() for p in raw.splitlines() if p.strip()))


def _added_and_removed_lines(path: str) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """The `+` and `-` payload lines for one path, baseline → working tree, without context."""
    _require_baseline()
    raw = _git("diff", "-U0", BASELINE_TAG, "--", path)
    added, removed = [], []
    for line in raw.splitlines():
        if line.startswith("+++") or line.startswith("---"):
            continue
        if line.startswith("+"):
            added.append(line[1:])
        elif line.startswith("-"):
            removed.append(line[1:])
    return tuple(added), tuple(removed)


def _baseline_source(path: str) -> str:
    """A file's contents as of the baseline tag."""
    _require_baseline()
    return _git("show", f"{BASELINE_TAG}:{path}")



def _certified_source(path: str) -> str:
    """A file's contents **as of the certified commit** — the tree M10.13 certified.

    Distinct from `_baseline_source`, which reads `v4.0.0`: that is the *Epic 9* baseline every
    "unchanged since" comparison starts from, whereas this is the tree Epic 10's certification is
    a statement **about** (M11.5.1, ADR-0052 Amendment 1 **HC9**)."""
    return _git("show", f"{CERTIFICATION_COMMIT}:{path}")


def _certified_changes_since_baseline(*pathspecs: str) -> tuple[tuple[str, str], ...]:
    """`(status, path)` for everything under `pathspecs` that differs between the baseline and
    **the certified commit** — the historical counterpart of `_changed_since_baseline`.

    Both endpoints are pinned (ADR-0052 **HC2**), so the answer is a permanent fact about the tree
    M10.13 certified, and later additions to the checkout cannot move it (**HC9**, M12.1.1)."""
    _require_baseline()
    return _name_status(
        _git("diff", "--name-status", BASELINE_TAG, CERTIFICATION_COMMIT, "--", *pathspecs)
    )


def _certified_python_sources(prefix: str) -> dict[str, str]:
    """Every `.py` file under `prefix` as of the certified commit, keyed by repo-relative path.

    The historical counterpart of `_python_sources`, which walks the **live** filesystem. Reading
    through `git ls-tree`/`git show` rather than the checkout is what makes the count a permanent
    fact about the certified tree instead of a measurement of whoever last edited the repository
    (ADR-0052 **HC9**)."""
    listing = _git("ls-tree", "-r", "--name-only", CERTIFICATION_COMMIT, "--", prefix)
    sources: dict[str, str] = {}
    for line in listing.splitlines():
        path = line.strip()
        if not path.endswith(".py") or "__pycache__" in path:
            continue
        sources[path] = _git("show", f"{CERTIFICATION_COMMIT}:{path}")
    return sources


def _evt_names_in(sources: dict) -> tuple[list[str], list[str]]:
    """`(names, violations)` for the `evt_` prefix rule over one set of sources.

    Factored so the **historical count** and the **live invariant** run identical extraction
    logic against different trees — two questions, one implementation, no chance of the two
    drifting apart (M11.5.1)."""
    names: list[str] = []
    violations: list[str] = []
    for path, source in sources.items():
        label = getattr(path, "name", str(path).rsplit("/", 1)[-1])
        try:
            tree = ast.parse(source)
        except SyntaxError:  # pragma: no cover
            continue
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Assign)
                and any(
                    isinstance(t, ast.Name) and t.id == "__tablename__" for t in node.targets
                )
                and isinstance(node.value, ast.Constant)
                and isinstance(node.value.value, str)
            ):
                names.append(node.value.value)
                if not node.value.value.startswith("evt_"):
                    violations.append(f"{label}: table {node.value.value!r}")
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id == "Index"
                and node.args
                and isinstance(node.args[0], ast.Constant)
                and isinstance(node.args[0].value, str)
            ):
                names.append(node.args[0].value)
                if not node.args[0].value.startswith("ix_evt_"):
                    violations.append(f"{label}: index {node.args[0].value!r}")
    return names, violations


def _created_names_in(sources: dict) -> tuple[list[str], list[str]]:
    """`(names, violations)` for objects a migration creates, over one set of sources."""
    names: list[str] = []
    violations: list[str] = []
    for path, source in sources.items():
        label = getattr(path, "name", str(path).rsplit("/", 1)[-1])
        for created in _created_object_names(source):
            names.append(created)
            if not created.startswith("evt_") and not created.startswith("ix_evt_"):
                violations.append(f"{label}: created object {created!r}")
    return names, violations


def _enum_members(source: str, class_name: str) -> dict[str, str]:
    """`{member: value}` for a `StrEnum` class, read structurally rather than textually.

    AST rather than regex, so a member named in a docstring or a comment is not mistaken for a
    declaration — the failure mode that has cost this epic several guards already."""
    members: dict[str, str] = {}
    for node in ast.walk(ast.parse(source)):
        if not isinstance(node, ast.ClassDef) or node.name != class_name:
            continue
        for stmt in node.body:
            if (
                isinstance(stmt, ast.Assign)
                and len(stmt.targets) == 1
                and isinstance(stmt.targets[0], ast.Name)
                and isinstance(stmt.value, ast.Constant)
                and isinstance(stmt.value.value, str)
            ):
                members[stmt.targets[0].id] = stmt.value.value
    return members


def _python_sources(root: Path) -> dict[Path, str]:
    """Every readable `.py` file under `root`, excluding caches."""
    sources: dict[Path, str] = {}
    for path in sorted(root.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        try:
            sources[path] = path.read_text(encoding="utf-8")
        except OSError:  # pragma: no cover — unreadable file
            continue
    return sources


def _ok(control_id: str, examined: int, detail: str) -> Epic10ControlResult:
    return Epic10ControlResult(
        control_id, Epic10ControlOutcome.SATISFIED, detail=detail, examined=examined
    )


def _bad(
    control_id: str, violations: Sequence[str], examined: int, detail: str
) -> Epic10ControlResult:
    if violations:
        return Epic10ControlResult(
            control_id,
            Epic10ControlOutcome.VIOLATED,
            detail=detail,
            violations=tuple(sorted(violations)),
            examined=examined,
        )
    return _ok(control_id, examined, detail)


# ── the evaluators ──────────────────────────────────────────────────────────────


def _e10_1() -> Epic10ControlResult:
    """`billing/**` unchanged since the baseline, except exactly MB14.

    The exception is applied **per symbol**, not per file: the file may differ only by the one
    named member and the comment block that cites its authority. Anything else — a second member,
    a modified line, a removed line, a different file — is a violation."""
    changes = _changed_since_baseline("platform/billing")
    permitted_by_path = {e.path: e for e in PERMITTED_BILLING_EXCEPTIONS}
    violations: list[str] = []

    for status, path in changes:
        exception = permitted_by_path.get(path)
        if exception is None:
            violations.append(f"{path}: unauthorized billing change ({status})")
            continue
        if status != "M":
            violations.append(
                f"{path}: the {exception.authority} exception permits modification only, got "
                f"status {status!r}"
            )
            continue
        added, removed = _added_and_removed_lines(path)
        if removed:
            violations.append(
                f"{path}: the {exception.authority} exception is additive; "
                f"{len(removed)} line(s) were removed"
            )
        code_added = [line.strip() for line in added if line.strip()]
        code_added = [line for line in code_added if not line.startswith("#")]
        expected = f'{exception.symbol} = "{exception.value}"'
        if code_added != [expected]:
            violations.append(
                f"{path}: the {exception.authority} exception admits exactly "
                f"[{expected!r}], found {code_added!r}"
            )

    return _bad(
        "E10-1",
        violations,
        len(changes),
        f"{len(changes)} billing path(s) differ from {BASELINE_TAG}; the only permitted delta is "
        f"{MB14_BILLING_EXCEPTION.symbol} in {MB14_BILLING_EXCEPTION.path} "
        f"({MB14_BILLING_EXCEPTION.authority})",
    )


def _migration_version(path: str) -> str:
    match = re.search(r"/migrations/v(\d{4})", path)
    return match.group(1) if match else ""


def _e10_2() -> Epic10ControlResult:
    """Frozen migrations untouched, and Epic 10's own chain at or above the floor."""
    changes = _changed_since_baseline("platform")
    violations: list[str] = []
    for status, path in changes:
        version = _migration_version(path)
        if version and version <= FROZEN_MIGRATION_CEILING:
            violations.append(f"{path}: frozen migration {version} changed ({status})")

    from events.persistence import migrations as epic10_migrations

    versions = tuple(m.VERSION for m in epic10_migrations.MIGRATIONS)
    for version in versions:
        if version < EPIC10_MIGRATION_FLOOR:
            violations.append(
                f"Epic 10 migration {version} is below the {EPIC10_MIGRATION_FLOOR} floor"
            )

    return _bad(
        "E10-2",
        violations,
        len(versions),
        f"migrations 0001–{FROZEN_MIGRATION_CEILING} unchanged; Epic 10 owns {list(versions)}",
    )


#: A literal `CREATE TABLE` / `CREATE INDEX` target. The negative lookahead matters: without it,
#: an f-string DDL such as `CREATE INDEX IF NOT EXISTS {name} ON …` backtracks past the optional
#: `IF NOT EXISTS` — which cannot be followed by an identifier, because the name is a placeholder —
#: and captures the keyword `IF` as the object name. Placeholder DDL is deliberately left to
#: `_declared_index_names`, which reads the values the placeholder is filled from.
_CREATED_OBJECT_RE = re.compile(
    r"CREATE\s+(?:UNIQUE\s+)?(?:TABLE|INDEX)\s+(?:IF\s+NOT\s+EXISTS\s+)?"
    r"(?!IF\b|NOT\b|EXISTS\b)([A-Za-z_]\w*)",
    re.IGNORECASE,
)


def _declared_index_names(source: str) -> list[str]:
    """Index names a migration declares as data, for DDL built from an f-string placeholder.

    Migrations bind `INDEXES = ((name, table, columns), …)` at module level and interpolate the
    name into the statement. The literal scan cannot see through the placeholder, so the names are
    read from the binding instead — structurally, not textually."""
    names: list[str] = []
    try:
        tree = ast.parse(source)
    except SyntaxError:  # pragma: no cover
        return names
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        if not any(isinstance(t, ast.Name) and t.id == "INDEXES" for t in node.targets):
            continue
        for element in getattr(node.value, "elts", []):
            entries = getattr(element, "elts", [])
            if entries and isinstance(entries[0], ast.Constant):
                if isinstance(entries[0].value, str):
                    names.append(entries[0].value)
    return names


def _created_object_names(source: str) -> list[str]:
    """Every table or index name a migration module creates, literal or placeholder-driven."""
    return _CREATED_OBJECT_RE.findall(source) + _declared_index_names(source)


def _e10_3() -> Epic10ControlResult:
    """Every Epic 10 table and index name carries the `evt_` prefix (ADR-0034 DS9).

    **Two trees, two questions** (M11.5.1, ADR-0052 **HC9**):

    * the **examined count** is the number of names in the **certified tree** — a permanent fact
      about what M10.13 certified, so a later epic legitimately adding a table cannot move the
      Epic 10 certification digest;
    * the **violation scan** stays on the **live tree**, because *"no table under `events/` may
      escape the prefix"* is an ongoing invariant that must keep biting on new code.
    """
    historical_names, _ = _evt_names_in(_certified_python_sources("platform/events"))
    historical_created, _ = _created_names_in(
        _certified_python_sources("platform/events/persistence/migrations")
    )
    historical = historical_names + historical_created

    live_names, violations = _evt_names_in(_python_sources(PLATFORM_ROOT / "events"))
    live_created, created_violations = _created_names_in(
        _python_sources(PLATFORM_ROOT / "events" / "persistence" / "migrations")
    )
    violations = violations + created_violations

    # **Both** halves need evidence, and neither substitutes for the other (ADR-0043 CD4). An
    # unreadable certified tree means the count cannot be stated; an empty live scan means the
    # prefix invariant had nothing to check, which is a vacuous pass rather than a real one.
    if not historical:
        return Epic10ControlResult(
            "E10-3",
            Epic10ControlOutcome.INCONCLUSIVE,
            detail="no table or index declaration was found under events/ at the certified commit",
        )
    if not (live_names or live_created):
        return Epic10ControlResult(
            "E10-3",
            Epic10ControlOutcome.INCONCLUSIVE,
            detail="no table or index declaration was found under events/ in the current tree",
        )
    return _bad(
        "E10-3", violations, len(historical), f"{len(historical)} evt_-prefixed name(s) examined"
    )


def _e10_4() -> Epic10ControlResult:
    """Every baseline `EventType` member survives unchanged; Epic 10 only appended."""
    historical = _enum_members(_baseline_source("platform/core/events.py"), "EventType")
    current = _enum_members(
        (PLATFORM_ROOT / "core" / "events.py").read_text(encoding="utf-8"), "EventType"
    )
    if not historical:
        return Epic10ControlResult(
            "E10-4",
            Epic10ControlOutcome.INCONCLUSIVE,
            detail=f"no EventType members were readable at {BASELINE_TAG}",
        )
    violations = []
    for name, value in sorted(historical.items()):
        if name not in current:
            violations.append(f"{name}: removed")
        elif current[name] != value:
            violations.append(f"{name}: {value!r} became {current[name]!r}")
    added = sorted(set(current) - set(historical))
    return _bad(
        "E10-4",
        violations,
        len(historical),
        f"{len(historical)} baseline member(s) unchanged; Epic 10 added {added}",
    )


def _e10_5() -> Epic10ControlResult:
    """`metric.billing.*` stays reserved to Billing (ADR-0033 MS9).

    **The control this milestone was called in to repair** (M11.5.1, ADR-0052 **HC9**). Its
    examined count was `len(current)` — the number of `EventType` members in the **live tree** —
    and `examined` is a digest input. So a later epic adding a legitimately additive event type
    moved Epic 10's published certification digest **while every control still reported
    `satisfied`**: the digest was measuring the repository's growth rather than the certified
    tree. M11.5 hit exactly that and correctly stopped.

    * **examined** → the member count in the **certified tree** (stable forever);
    * **violations** → still scanned on the **live tree**, because *"no `metric.billing.*` member
      may ever appear outside Billing"* is an ongoing reservation, not a historical observation.
    """
    historical = _enum_members(_certified_source("platform/core/events.py"), "EventType")
    current = _enum_members(
        (PLATFORM_ROOT / "core" / "events.py").read_text(encoding="utf-8"), "EventType"
    )
    if not historical:
        return Epic10ControlResult(
            "E10-5",
            Epic10ControlOutcome.INCONCLUSIVE,
            detail="no EventType members were readable at the certified commit",
        )
    if not current:
        return Epic10ControlResult(
            "E10-5",
            Epic10ControlOutcome.INCONCLUSIVE,
            detail="no EventType members were readable in the current tree",
        )
    violations = [
        f"{name} = {value!r}"
        for name, value in sorted(current.items())
        if value.startswith("metric.billing")
    ]
    return _bad(
        "E10-5",
        violations,
        len(historical),
        f"{len(historical)} event type(s) examined; the metric.billing.* namespace is unused",
    )


def _e10_6() -> Epic10ControlResult:
    """Billing subscribes to no bus — checked structurally, so prose cannot trip it.

    A textual scan fires on `commercial_subscriptions.py` and on docstrings that use the word
    correctly. Only a `.subscribe(...)` **call** or a reference to the `EventBus` **symbol** is a
    subscription."""
    # Historical count, live invariant (M11.5.1, ADR-0052 **HC9**): the number of Billing modules
    # M10.13 examined is a fact about the certified tree, while "no Billing module subscribes to a
    # bus" must keep being checked against whatever Billing looks like today.
    historical = _certified_python_sources("platform/billing")
    sources = _python_sources(PLATFORM_ROOT / "billing")
    violations: list[str] = []
    for path, source in sources.items():
        try:
            tree = ast.parse(source)
        except SyntaxError:  # pragma: no cover
            continue
        relative = path.relative_to(PLATFORM_ROOT)
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "subscribe"
            ):
                violations.append(f"{relative}: calls .subscribe(...)")
            if isinstance(node, ast.Name) and node.id == "EventBus":
                violations.append(f"{relative}: references EventBus")
            if isinstance(node, ast.Attribute) and node.attr == "EventBus":
                violations.append(f"{relative}: references EventBus")
            if isinstance(node, ast.ImportFrom):
                for alias in node.names:
                    if alias.name == "EventBus":
                        violations.append(f"{relative}: imports EventBus")
    # Both halves need evidence (ADR-0043 CD4), for the reason E10-3 states: an empty live scan
    # is a vacuous pass, not a clean one.
    if not historical:
        return Epic10ControlResult(
            "E10-6",
            Epic10ControlOutcome.INCONCLUSIVE,
            detail="no Billing modules were readable at the certified commit",
        )
    if not sources:
        return Epic10ControlResult(
            "E10-6",
            Epic10ControlOutcome.INCONCLUSIVE,
            detail="no Billing modules were readable in the current tree",
        )
    return _bad(
        "E10-6",
        violations,
        len(historical),
        f"{len(historical)} billing module(s) examined; none subscribes to a bus",
    )


def _e10_7() -> Epic10ControlResult:
    """Pre-existing `api/` files untouched — additions are not modifications (ADR-0043 CD7).

    **Repaired at M12.1.1** (ADR-0052 Amendment 2, **HC9**). Its examined count was
    `len(changes)` over the **live** tree, and `examined` is a digest input. CD7 *permits*
    additions, so every legitimate later `api/` file (M11's WebRTC surface, M12's console) moved
    Epic 10's certification digest while the control stayed `satisfied` — the same defect M11.5.1
    repaired in E10-3/5/6 and missed here.

    * **examined** → the `api/` paths the **certified commit** changed since the baseline
      (stable forever);
    * **violations** → still read from the **live tree**, because *"no pre-existing `api/` file
      is modified or deleted"* is an ongoing prohibition, not a historical observation.
    """
    historical = _certified_changes_since_baseline("platform/api")
    changes = _changed_since_baseline("platform/api")
    if not historical:
        return Epic10ControlResult(
            "E10-7",
            Epic10ControlOutcome.INCONCLUSIVE,
            detail="no api change was readable between the baseline and the certified commit",
        )
    violations = [
        f"{path}: pre-existing api file {('deleted' if status.startswith('D') else 'modified')}"
        for status, path in changes
        if not status.startswith("A")
    ]
    additions = [path for status, path in changes if status.startswith("A")]
    return _bad(
        "E10-7",
        violations,
        len(historical),
        f"{len(historical)} api change(s) certified; {len(additions)} api addition(s) permitted "
        f"in the current tree: {additions}; no pre-existing file modified",
    )


def _e10_8() -> Epic10ControlResult:
    """`platform/replay/**` is untouched."""
    changes = _changed_since_baseline("platform/replay")
    return _bad(
        "E10-8",
        [f"{path}: replay plane changed ({status})" for status, path in changes],
        len(changes),
        f"platform/replay/** is identical to {BASELINE_TAG}",
    )


def _e10_9() -> Epic10ControlResult:
    """Every `api/**observability*` file is untouched."""
    changes = _changed_since_baseline("platform/api/**observability*")
    return _bad(
        "E10-9",
        [f"{path}: observability file changed ({status})" for status, path in changes],
        len(changes),
        f"every api observability file is identical to {BASELINE_TAG}",
    )


_MANIFEST_DIGEST_RE = re.compile(r"\*\*Manifest digest:\*\*\s*`([0-9a-f]{64})`")
_CERTIFICATION_DIGEST_RE = re.compile(r"\*\*Certification digest:\*\*\s*`([0-9a-f]{64})`")


def _e10_10() -> Epic10ControlResult:
    """The historical digests are **proven**, not restored (ADR-0043 CD6).

    Three separate assertions, and the third is the one that keeps the other two honest:

    1. the original values are reproducible from the baseline tree;
    2. the current post-M10.10 values are readable and recorded as a distinct state;
    3. **M10.13 changed no Epic 9 artifact** — compared across the certification commit itself,
       not against the baseline, because the M10.10 re-baseline is authorized history and only
       edits made *by M10.13* are forbidden (M11.2.1, ADR-0052).
    """
    historical_doc = _baseline_source(EPIC9_RELEASE_DOC)
    historical_manifest = _MANIFEST_DIGEST_RE.search(historical_doc)
    historical_certification = _CERTIFICATION_DIGEST_RE.search(historical_doc)
    if not historical_manifest or not historical_certification:
        return Epic10ControlResult(
            "E10-10",
            Epic10ControlOutcome.INCONCLUSIVE,
            detail=f"the {BASELINE_TAG} release record did not yield both digests",
        )

    violations: list[str] = []
    if historical_manifest.group(1) != EPIC9_HISTORICAL_MANIFEST_DIGEST:
        violations.append(
            f"historical manifest digest is {historical_manifest.group(1)}, "
            f"expected {EPIC9_HISTORICAL_MANIFEST_DIGEST}"
        )
    if historical_certification.group(1) != EPIC9_HISTORICAL_CERTIFICATION_DIGEST:
        violations.append(
            f"historical certification digest is {historical_certification.group(1)}, "
            f"expected {EPIC9_HISTORICAL_CERTIFICATION_DIGEST}"
        )

    current_doc = (REPO_ROOT / EPIC9_RELEASE_DOC).read_text(encoding="utf-8")
    current_manifest = _MANIFEST_DIGEST_RE.search(current_doc)
    current_certification = _CERTIFICATION_DIGEST_RE.search(current_doc)
    if not current_manifest or not current_certification:
        return Epic10ControlResult(
            "E10-10",
            Epic10ControlOutcome.INCONCLUSIVE,
            detail="the current release record did not yield both digests",
        )

    touched = _changed_by_the_certification(*EPIC9_FROZEN_ARTIFACTS)
    violations.extend(f"{path}: Epic 9 artifact modified by M10.13" for path in touched)

    return _bad(
        "E10-10",
        violations,
        4,
        f"historical {EPIC9_HISTORICAL_CERTIFICATION_DIGEST[:16]}… reproduced from "
        f"{BASELINE_TAG}; current {current_certification.group(1)[:16]}… recorded separately; "
        f"{len(EPIC9_FROZEN_ARTIFACTS)} Epic 9 artifact(s) untouched",
    )


#: Epic 9's two advisory controls, which §M10.13 requires remain open.
EPIC9_REQUIRED_ADVISORIES = ("NS-5", "ST-3")

#: Epic 9's nine declared limitations, with the owners they must still carry.
EPIC9_REQUIRED_LIMITATION_OWNERS = {
    "LIM-1": "M9.15",
    "LIM-2": "M9.16",
    "LIM-3": "M9.18",
    "LIM-4": "M9.8",
    "LIM-5": "M9.8",
    "LIM-6": "M9.13/M9.14",
    "LIM-7": "M9.24",
    "LIM-8": "M9.5/M9.6/M9.9/M9.10/M9.11",
    "LIM-9": "M9.8/M9.7",
}


def _epic9_declared_state() -> tuple[dict[str, str], dict[str, str]]:
    """Epic 9's declared limitations and control severities, **read as data**.

    This is the single point where Epic 10 looks at Epic 9's catalogue, and it looks at values
    only. No Epic 9 evaluator is imported, referenced or executed (ADR-0043 CD2/CD3)."""
    from tests.certification.catalogue import CONTROLS_BY_ID, DECLARED_LIMITATIONS

    owners = {limitation.id: limitation.owner for limitation in DECLARED_LIMITATIONS}
    severities = {control_id: c.severity.value for control_id, c in CONTROLS_BY_ID.items()}
    return owners, severities


def _e10_11() -> Epic10ControlResult:
    """NS-5, ST-3 and the nine limitations are still declared, still owned."""
    try:
        owners, severities = _epic9_declared_state()
    except ImportError as exc:  # pragma: no cover — catalogue unimportable
        return Epic10ControlResult(
            "E10-11",
            Epic10ControlOutcome.INCONCLUSIVE,
            detail=f"Epic 9's declared state could not be read: {exc}",
        )

    violations: list[str] = []
    for advisory in EPIC9_REQUIRED_ADVISORIES:
        if advisory not in severities:
            violations.append(f"{advisory}: no longer declared")
        elif severities[advisory] != "advisory":
            violations.append(f"{advisory}: severity is {severities[advisory]!r}, not advisory")

    for limitation_id, owner in sorted(EPIC9_REQUIRED_LIMITATION_OWNERS.items()):
        if limitation_id not in owners:
            violations.append(f"{limitation_id}: no longer declared")
        elif owners[limitation_id] != owner:
            violations.append(
                f"{limitation_id}: owner is {owners[limitation_id]!r}, expected {owner!r}"
            )

    if len(owners) != len(EPIC9_REQUIRED_LIMITATION_OWNERS):
        violations.append(
            f"Epic 9 declares {len(owners)} limitation(s), expected "
            f"{len(EPIC9_REQUIRED_LIMITATION_OWNERS)}"
        )

    return _bad(
        "E10-11",
        violations,
        len(owners) + len(EPIC9_REQUIRED_ADVISORIES),
        f"{len(owners)} limitation(s) and {len(EPIC9_REQUIRED_ADVISORIES)} advisory control(s) "
        "still declared with their owners",
    )


#: The two functions whose independence *is* the digest/verdict separation.
_DIGEST_FUNCTION = "epic10_certification_digest"
_VERDICT_FUNCTION = "verdict"

#: The module `E10-12` inspects — this one. Named rather than inlined so the control can be run
#: against a synthetic module that *does* entangle the two, which is how the guard is proven to
#: detect the forbidden condition rather than merely to agree with the current file.
_SELF_SOURCE_PATH = Path(__file__)


def _identifiers_in(node: ast.AST) -> set[str]:
    """Every name, attribute and string constant a function body mentions."""
    found: set[str] = set()
    for child in ast.walk(node):
        if isinstance(child, ast.Name):
            found.add(child.id)
        elif isinstance(child, ast.Attribute):
            found.add(child.attr)
        elif isinstance(child, ast.Constant) and isinstance(child.value, str):
            found.add(child.value)
    return found


def _function_body(tree: ast.AST, name: str) -> ast.FunctionDef | None:
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return node
    return None


def _e10_12() -> Epic10ControlResult:
    """Digest ⟂ verdict, proven **structurally** rather than by inspection of behaviour.

    The docstrings of both functions discuss the separation, so a textual scan would fire on the
    prose that documents the property. This reads the bodies as syntax: the digest function must
    mention no verdict identifier, and the verdict function must mention no digest identifier."""
    tree = ast.parse(_SELF_SOURCE_PATH.read_text(encoding="utf-8"))
    digest_fn = _function_body(tree, _DIGEST_FUNCTION)
    verdict_fn = _function_body(tree, _VERDICT_FUNCTION)
    if digest_fn is None or verdict_fn is None:
        return Epic10ControlResult(
            "E10-12",
            Epic10ControlOutcome.INCONCLUSIVE,
            detail=f"{_DIGEST_FUNCTION} or {_VERDICT_FUNCTION} was not found in this module",
        )

    violations: list[str] = []
    digest_body = ast.Module(body=digest_fn.body[1:], type_ignores=[])
    verdict_body = ast.Module(body=verdict_fn.body[1:], type_ignores=[])
    for identifier in sorted(_identifiers_in(digest_body)):
        if "verdict" in identifier.lower():
            violations.append(f"{_DIGEST_FUNCTION} references {identifier!r}")
    for identifier in sorted(_identifiers_in(verdict_body)):
        if "digest" in identifier.lower():
            violations.append(f"{_VERDICT_FUNCTION} references {identifier!r}")

    return _bad(
        "E10-12",
        violations,
        2,
        "the digest reads findings alone and the verdict reads outcomes and severities alone",
    )


def _e10_13() -> Epic10ControlResult:
    """LIM-7 is inherited, not re-owned (ADR-0043 CD10)."""
    try:
        owners, _ = _epic9_declared_state()
    except ImportError as exc:  # pragma: no cover
        return Epic10ControlResult(
            "E10-13",
            Epic10ControlOutcome.INCONCLUSIVE,
            detail=f"Epic 9's declared state could not be read: {exc}",
        )

    violations: list[str] = []
    inherited = [
        limitation
        for limitation in EPIC10_DECLARED_LIMITATIONS
        if limitation.inherited_from == "LIM-7"
    ]
    if not inherited:
        violations.append("Epic 10 declares no limitation inheriting LIM-7")
    for limitation in inherited:
        if not limitation.self_administered:
            violations.append(f"{limitation.id}: does not declare certification self-administered")
    if owners.get("LIM-7") != "M9.24":
        violations.append(
            f"Epic 9's LIM-7 owner is {owners.get('LIM-7')!r}, expected 'M9.24' — inheriting must "
            "not re-own it"
        )
    if not EPIC10_DECLARED_LIMITATIONS:
        violations.append("Epic 10 declares no limitations at all")

    return _bad(
        "E10-13",
        violations,
        len(EPIC10_DECLARED_LIMITATIONS),
        f"{len(EPIC10_DECLARED_LIMITATIONS)} Epic 10 limitation(s) declared; LIM-7 inherited "
        "honestly and left owned by M9.24",
    )


#: Every control's evaluator, keyed by control id. Asserted complete against the catalogue, so a
#: control cannot be declared and quietly never run.
EPIC10_EVALUATORS = {
    "E10-1": _e10_1,
    "E10-2": _e10_2,
    "E10-3": _e10_3,
    "E10-4": _e10_4,
    "E10-5": _e10_5,
    "E10-6": _e10_6,
    "E10-7": _e10_7,
    "E10-8": _e10_8,
    "E10-9": _e10_9,
    "E10-10": _e10_10,
    "E10-11": _e10_11,
    "E10-12": _e10_12,
    "E10-13": _e10_13,
}


def certify_epic10() -> Epic10CertificationReport:
    """Run every Epic 10 control and return the report.

    An evaluator that raises becomes `INCONCLUSIVE`, **never** a pass. A certification suite that
    swallowed its own errors as successes would certify its blind spots — the one failure mode
    that makes certification worse than nothing."""
    results = []
    for control_id, evaluator in EPIC10_EVALUATORS.items():
        try:
            results.append(evaluator())
        except Exception as exc:  # noqa: BLE001 — deliberate: any failure to look is inconclusive
            results.append(
                Epic10ControlResult(
                    control_id,
                    Epic10ControlOutcome.INCONCLUSIVE,
                    detail=f"{type(exc).__name__}: {exc}",
                )
            )
    return Epic10CertificationService.report(results)


def render_epic10(report: Epic10CertificationReport) -> str:
    """A readable rendering, for assertion messages and for a human reading a failure."""
    lines = [
        f"Epic 10 certification ({report.certification_version}) — {report.verdict.value}",
        f"digest: {report.report_digest}",
        "",
    ]
    for result in report.results:
        control = EPIC10_CONTROLS_BY_ID[result.control_id]
        lines.append(
            f"  {result.control_id:<8} {result.outcome.value:<15} "
            f"[{control.severity.value}] {result.detail}"
        )
        lines.extend(f"      ! {v}" for v in result.violations)
    lines.append("")
    lines.append("declared limitations:")
    for limitation in report.limitations:
        inherited = f" (inherits {limitation.inherited_from})" if limitation.inherited_from else ""
        lines.append(f"  {limitation.id} [{limitation.owner}]{inherited} {limitation.statement}")
    return "\n".join(lines)


__all__ = [
    "EPIC10_CERTIFICATION_CONTROLS",
    "EPIC10_CERTIFICATION_VERSION",
    "EPIC10_CONTROLS_BY_ID",
    "EPIC10_DECLARED_LIMITATIONS",
    "EPIC10_EVALUATORS",
    "EPIC10_PASSING_OUTCOMES",
    "EPIC9_HISTORICAL_CERTIFICATION_DIGEST",
    "EPIC9_HISTORICAL_MANIFEST_DIGEST",
    "MB14_BILLING_EXCEPTION",
    "PERMITTED_BILLING_EXCEPTIONS",
    "BillingChangeException",
    "Epic10CertificationReport",
    "Epic10CertificationService",
    "Epic10Control",
    "Epic10ControlFamily",
    "Epic10ControlOutcome",
    "Epic10ControlResult",
    "Epic10ControlSeverity",
    "Epic10DeclaredLimitation",
    "Epic10Verdict",
    "GitUnavailable",
    "certify_epic10",
    "epic10_certification_digest",
    "render_epic10",
]
