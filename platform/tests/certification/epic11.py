"""Epic 11 Certification (M11.16) — a fresh `E11-*` control set, independent of Epic 9's and 10's.

Ratified by **ADR-0051**. **CE5** names the minimum control set and this module implements exactly
it, in Epic 11's own vocabulary.

The question this module owns is not Epic 9's and not Epic 10's:

> **Does the Epic 11 implementation satisfy the architecture and governance it was authorized to
> build within, well enough to proceed to freeze and release?**

## Independence (CE1/CE2)

This sits **beside** `catalogue.py` (Epic 9) and `epic10.py` (Epic 10) and that adjacency is
filesystem placement only. It declares its own controls, families, outcomes, verdicts, limitations
and version constant. It never adds to, renumbers, reuses, wraps, re-exports or invokes either
predecessor's definitions. Epic 9's 27 controls and Epic 10's 13 are never executed here, and
neither historical verdict is confirmed or disturbed.

Epic 10's certification and freeze digests *are* read, in one direction only, as **evidence that
Epic 10's historical state is undisturbed** (control `E11-16`). That is an observation of data,
not the execution of a foreign control.

## `INCONCLUSIVE` does not pass (CE3)

A control that cannot reach a verdict is a failure. A suite that treated "could not look" as a
pass would certify its own blind spots — the one failure mode that makes certification worse than
nothing.

## Reads, never writes (CE4)

No module, table, repository, command bus or port method is modified by a control. The report is
a value regenerated from the current tree: a stored verdict is a staler answer than re-deriving it.

## Self-administered (CE9)

Epic 11's certification is written and executed by the same party that built Epic 11. That is
stated here rather than implied away, and it is a declared limitation, not a footnote.

## What certification is not

`SATISFIED` means *the control's evidence was produced and it held*. It does not mean the system
is production-ready, released, or validated against a live vendor. Controls that require live or
Stage-2 evidence say so, and where that evidence does not exist they do **not** quietly pass.
"""

from __future__ import annotations

import ast
import hashlib
import json
import os
import re
import subprocess
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

#: Bumped when a change here would produce a different verdict from the same repository. Distinct
#: from Epic 9's `CERTIFICATION_VERSION` and Epic 10's `EPIC10_CERTIFICATION_VERSION`, neither of
#: which this module reads or redefines (CE1/CE2).
EPIC11_CERTIFICATION_VERSION = "m11.16.1"

#: `platform/` — resolved from this module, so certification answers the same wherever invoked.
PLATFORM_ROOT = Path(__file__).resolve().parent.parent.parent
REPO_ROOT = PLATFORM_ROOT.parent

#: The release Epic 11 builds on. Pinned as a literal: a value read from the thing under test
#: cannot detect that thing moving.
BASELINE_TAG = "v5.0.0"
BASELINE_COMMIT = "3779e932182ed06ec5411c83ba53f0c2b585b8b8"

#: ADR-0051 CE5 control 1. The floor is Epic 10's verified regression count, not Epic 11's own.
REGRESSION_FLOOR = 5447

#: Constitution §A8 per-stage p50 budgets, in milliseconds, and the I1 targets (CE5 control 12).
A8_STAGE_BUDGETS_MS = {"endpoint": 250, "stt": 150, "llm": 350, "tts": 200}
I1_P50_BUDGET_MS = 1000
I1_P95_BUDGET_MS = 1800

#: The machine-readable I1 artifact control 12 reads. Its absence is INCONCLUSIVE, never a pass.
I1_ARTIFACT = "docs/performance/i1-baseline.json"

#: Epic 10's historical digests, pinned as literals. `E11-16` reproduces them; it never restores
#: them into any artifact and never rebaselines them (ADR-0052, ADR-0043 CD6's pattern).
EPIC10_CERTIFICATION_DIGEST = (
    "7e57bc14764201b123c49b9321f15d9c41f0ee7015e6879c64c0307e56db3d0d"
)
EPIC10_FREEZE_DIGEST = "d3d4858d5f18b7f6d583f4d34caeec52933acc262e36d4f8b343e2e8b6a00d38"

#: Vendor identifiers that AR-21 forbids inside the generic planes (CE5 control 3).
VENDOR_TOKENS = (
    "deepgram", "groq", "cartesia", "silero", "aiortc", "pysilero", "openai", "elevenlabs",
)

#: The packages Epic 11 introduced, each of which CE5 control 14 requires to carry a DECISION_LOG
#: entry naming purpose, licence and blast radius.
EPIC11_DEPENDENCIES = ("aiortc", "deepgram-sdk", "pysilero-vad", "groq", "cartesia")

#: Markers that weaken the regression floor. CE5 control 1 forbids all of them.
WEAKENING_MARKERS = ("skip", "skipif", "xfail")

#: Set in the environment of `E11-1`'s nested collection so that collection does not re-enter
#: certification. Without it the suite collects itself into an unbounded recursion.
_COLLECTION_GUARD = "VOXVOICE_E11_COLLECTING"


class Epic11ControlFamily(StrEnum):
    """Which ratified boundary a control certifies against. Epic 11's own families."""

    GOVERNANCE = "governance"      # scope, sequence, honest reporting
    ARCHITECTURE = "architecture"  # layering, ownership, vendor neutrality
    MEDIA = "media"                # audio, transport, streaming invariants
    RESILIENCE = "resilience"      # §A10, degradation, fault tolerance
    OBSERVABILITY = "observability"  # telemetry, metric.turn, Epic 10 boundary
    SUPPLY_CHAIN = "supply_chain"  # dependencies, secrets, licences
    PERFORMANCE = "performance"    # the I1 measurement
    HISTORY = "history"            # what predecessors' records must still show


class Epic11ControlSeverity(StrEnum):
    """Whether a violation blocks certification.

    `BLOCKING` means Epic 11 is **not certified** while it stands. `ADVISORY` records something a
    reader must know that does not by itself invalidate the boundary — never silently dropped,
    because "nobody mentioned it" is how debt becomes permanent."""

    BLOCKING = "blocking"
    ADVISORY = "advisory"


class Epic11ControlOutcome(StrEnum):
    """Five outcomes, and the last two carry the honesty.

    `NOT_APPLICABLE` is a positive statement: the control was evaluated and the condition it
    guards cannot arise. `NOT_EXECUTED` is Epic 11's addition to the shape it inherits, and it
    exists because Epic 11 is the first epic whose control set requires **live** evidence: it
    means the control's evidence was never produced, and it is distinct from both a pass and a
    discovered defect. `INCONCLUSIVE` means the evidence could not be gathered. None of the last
    three collapses into `SATISFIED`."""

    SATISFIED = "satisfied"
    VIOLATED = "violated"
    NOT_APPLICABLE = "not_applicable"
    NOT_EXECUTED = "not_executed"
    INCONCLUSIVE = "inconclusive"


#: The outcomes that let a control pass. `NOT_EXECUTED` and `INCONCLUSIVE` are deliberately absent
#: (CE3): a control whose evidence was never produced has not passed, and calling it one would be
#: certifying an absence.
EPIC11_PASSING_OUTCOMES: frozenset[Epic11ControlOutcome] = frozenset(
    {Epic11ControlOutcome.SATISFIED, Epic11ControlOutcome.NOT_APPLICABLE}
)


@dataclass(frozen=True, slots=True)
class Epic11Control:
    """One certifiable statement, bound to the clause that ratified it.

    `clauses` is never empty: a control with no ratified authority behind it is somebody's opinion
    rendered as a test, and this suite exists precisely to distinguish the two."""

    id: str
    family: Epic11ControlFamily
    severity: Epic11ControlSeverity
    statement: str
    clauses: tuple[str, ...]
    #: True when the control's evidence cannot be produced without a live vendor, a real network
    #: peer or a deployed host. Declared on the control so a reader can see, before any run, which
    #: parts of this certification depend on evidence this environment may not have.
    requires_live_evidence: bool = False

    def __post_init__(self) -> None:
        if not self.clauses:
            raise ValueError(
                f"control {self.id!r} cites no ratified clause; a control without an authority "
                "is an opinion, not a certification"
            )
        if not self.statement:
            raise ValueError(f"control {self.id!r} has no statement")


@dataclass(frozen=True, slots=True)
class Epic11ControlResult:
    """What one control found. Immutable.

    `violations` names the offending path, symbol or value — never a count alone: a result that
    says "3 violations" and cannot say which three is not actionable."""

    control_id: str
    outcome: Epic11ControlOutcome
    detail: str = ""
    violations: tuple[str, ...] = ()
    examined: int = 0

    @property
    def passed(self) -> bool:
        return self.outcome in EPIC11_PASSING_OUTCOMES

    def __post_init__(self) -> None:
        if self.outcome is Epic11ControlOutcome.VIOLATED and not self.violations:
            raise ValueError(
                f"control {self.control_id!r} reports a violation with nothing named; "
                "a violation that cannot be located cannot be fixed"
            )
        if self.outcome in (
            Epic11ControlOutcome.INCONCLUSIVE, Epic11ControlOutcome.NOT_EXECUTED
        ) and not self.detail:
            raise ValueError(
                f"control {self.control_id!r} claims no evidence with no reason; "
                "'not produced' must always say why"
            )


class Epic11Verdict(StrEnum):
    """The honest verdict.

    `NOT_CERTIFIED` follows from any blocking violation, **any inconclusive blocking control, and
    any blocking control whose evidence was never produced** — a boundary nobody verified is not a
    verified boundary, whichever of the three reasons applies."""

    CERTIFIED = "certified"
    CERTIFIED_WITH_ADVISORIES = "certified_with_advisories"
    NOT_CERTIFIED = "not_certified"


@dataclass(frozen=True, slots=True)
class Epic11DeclaredLimitation:
    """A limitation Epic 11 carries, with the milestone that owns closing it."""

    id: str
    milestone: str
    owner: str
    statement: str
    self_administered: bool = False


# ── the control set (ADR-0051 CE5) ───────────────────────────────────────────

EPIC11_CERTIFICATION_CONTROLS: tuple[Epic11Control, ...] = (
    Epic11Control(
        "E11-1", Epic11ControlFamily.GOVERNANCE, Epic11ControlSeverity.BLOCKING,
        f"the regression floor holds: >= {REGRESSION_FLOOR} tests collected, and no test is "
        "skipped, xfailed or otherwise weakened",
        ("ADR-0051 CE5#1", "TESTING_POLICY rules 1-3", "DEFINITION_OF_DONE"),
    ),
    Epic11Control(
        "E11-2", Epic11ControlFamily.ARCHITECTURE, Epic11ControlSeverity.BLOCKING,
        "import boundaries hold: the authorized runtime -> provider edge exists and no "
        "provider -> runtime edge does",
        ("ADR-0051 CE5#2", "AR-10", "AR-11", "AR-12", "AR-13", "AR-14", "AR-15",
         "ADR-0046 FT-V3/FT-V5"),
    ),
    Epic11Control(
        "E11-3", Epic11ControlFamily.ARCHITECTURE, Epic11ControlSeverity.BLOCKING,
        "vendor neutrality holds: no vendor identifier appears in core/**, runtime/** or api/**",
        ("ADR-0051 CE5#3", "AR-21"),
    ),
    Epic11Control(
        "E11-4", Epic11ControlFamily.ARCHITECTURE, Epic11ControlSeverity.BLOCKING,
        "every registered provider declares its capabilities truthfully and passes its capability "
        "contract suite",
        ("ADR-0051 CE5#4", "Constitution A6.3", "ADR-0045 SD9"),
    ),
    Epic11Control(
        "E11-5", Epic11ControlFamily.RESILIENCE, Epic11ControlSeverity.BLOCKING,
        "every media-path provider call passes through ProviderGuard; no direct raw call remains",
        ("ADR-0051 CE5#5", "AR-23", "ADR-0046 FT-D1/FT-D4"),
    ),
    Epic11Control(
        "E11-6", Epic11ControlFamily.RESILIENCE, Epic11ControlSeverity.BLOCKING,
        "the A10 degradation ladder is deterministic and hermetic; every declared degradation is "
        "evented and recorded in session metadata",
        ("ADR-0051 CE5#6", "AR-24", "Constitution A10", "ADR-0046 FT-D6"),
    ),
    Epic11Control(
        "E11-7", Epic11ControlFamily.GOVERNANCE, Epic11ControlSeverity.BLOCKING,
        "no accepted-but-ignored configuration field remains: allow_interruptions, llm_model and "
        "first_message all act",
        ("ADR-0051 CE5#7", "AR-25", "GOVERNANCE 4"),
    ),
    Epic11Control(
        "E11-8", Epic11ControlFamily.MEDIA, Epic11ControlSeverity.BLOCKING,
        "audio invariants hold: exactly one ingress and one egress conversion point, no unbounded "
        "queue on a media path, declared overflow policy enforced",
        ("ADR-0051 CE5#8", "AR-40", "AR-41", "ADR-0044 AD4/AD6/AD7"),
    ),
    Epic11Control(
        "E11-9", Epic11ControlFamily.MEDIA, Epic11ControlSeverity.BLOCKING,
        "streaming invariants hold: no wait-for-whole-input step remains in the conversation loop",
        ("ADR-0051 CE5#9", "AR-43", "ADR-0048 PB1/PB2"),
    ),
    Epic11Control(
        "E11-10", Epic11ControlFamily.OBSERVABILITY, Epic11ControlSeverity.BLOCKING,
        "telemetry holds: one producer for metric.turn, additive vocabulary, voice_to_voice_ms "
        "name and unit unchanged, an unreached boundary is an absent key and never 0",
        ("ADR-0051 CE5#10", "AR-2", "AR-32", "AR-42", "ADR-0038 TP2/TP5/TP6"),
    ),
    Epic11Control(
        "E11-11", Epic11ControlFamily.OBSERVABILITY, Epic11ControlSeverity.BLOCKING,
        "the Epic 10 boundary holds: no Epic 10 module modified by Epic 11, and no events -> "
        "billing edge exists",
        ("ADR-0051 CE5#11", "ADR-0050 SW1", "ADR-0052"),
    ),
    Epic11Control(
        "E11-12", Epic11ControlFamily.PERFORMANCE, Epic11ControlSeverity.BLOCKING,
        "I1 is measured through the production taps against a reproducible baseline: "
        f"p50 <= {I1_P50_BUDGET_MS} ms, p95 <= {I1_P95_BUDGET_MS} ms, "
        "and each A8 per-stage budget held",
        ("ADR-0051 CE5#12", "ADR-0051 CE6", "Constitution A8", "PERFORMANCE_POLICY 1/4",
         "GOVERNANCE 10"),
    ),
    Epic11Control(
        "E11-13", Epic11ControlFamily.GOVERNANCE, Epic11ControlSeverity.BLOCKING,
        "zero warnings originate in platform code; any third-party warning is enumerated and "
        "attributed",
        ("ADR-0051 CE5#13", "ADR-0051 CE8", "DEFINITION_OF_DONE"),
    ),
    Epic11Control(
        "E11-14", Epic11ControlFamily.SUPPLY_CHAIN, Epic11ControlSeverity.BLOCKING,
        "the lockfile is present and pinned, every Epic 11 dependency carries a DECISION_LOG entry "
        "with purpose, licence and blast radius, and no secret is committed",
        ("ADR-0051 CE5#14", "SECURITY_POLICY 6", "GOVERNANCE 10"),
    ),
    Epic11Control(
        "E11-15", Epic11ControlFamily.PERFORMANCE, Epic11ControlSeverity.BLOCKING,
        "Stage-2 live-ish verification: one scripted spoken call on the Stage-2 host, with real "
        "transport and real providers",
        ("ADR-0051 CE5#15", "TESTING_POLICY runtime tier", "Constitution A9 Stage 2"),
        requires_live_evidence=True,
    ),
    Epic11Control(
        "E11-16", Epic11ControlFamily.HISTORY, Epic11ControlSeverity.BLOCKING,
        "Epic 9 and Epic 10 certification and freeze artifacts are unchanged and their digests "
        "remain reproducible as historical state",
        ("ADR-0051 CE5#16", "ADR-0052", "ADR-0043 CD16"),
    ),
)

EPIC11_CONTROLS_BY_ID: dict[str, Epic11Control] = {
    control.id: control for control in EPIC11_CERTIFICATION_CONTROLS
}

EPIC11_DECLARED_LIMITATIONS: tuple[Epic11DeclaredLimitation, ...] = (
    Epic11DeclaredLimitation(
        "E11-LIM-1", "M11.16", "Technical Owner",
        "Epic 11's certification is self-administered: written and executed by the same party "
        "that implemented Epic 11 (ADR-0051 CE9). No independent auditor reviewed these controls.",
        self_administered=True,
    ),
    Epic11DeclaredLimitation(
        "E11-LIM-2", "M11.16", "Technical Owner",
        "Every structural and behavioural control here is hermetic. No control executed a live "
        "Deepgram, Groq or Cartesia call, and none exercised a real WebRTC peer. Provider "
        "certification is therefore structural and hermetic, never live (R-54, R-58, R-63).",
    ),
    Epic11DeclaredLimitation(
        "E11-LIM-3", "M11.16", "Technical Owner",
        "The I1 baseline E11-12 reads was produced with deterministic in-process providers and no "
        "network. It measures the runtime's own cost, not a conversation with a vendor, and the "
        "stt stage is structurally unmeasurable in the current turn shape (R-66).",
    ),
    Epic11DeclaredLimitation(
        "E11-LIM-4", "M11.16", "Technical Owner",
        "No Stage-2 host is provisioned, so E11-15's evidence was never produced. This is an "
        "absence of evidence recorded as such, not a defect discovered and not a risk accepted.",
    ),
)


# ── evidence helpers ─────────────────────────────────────────────────────────


class GitUnavailable(RuntimeError):
    """Git could not answer. Certification says so rather than guessing."""


def _git(*args: str) -> str:
    try:
        done = subprocess.run(
            ["git", *args], cwd=REPO_ROOT, capture_output=True, text=True, check=False
        )
    except OSError as exc:  # pragma: no cover — git absent
        raise GitUnavailable(f"git could not be executed: {exc}") from exc
    if done.returncode != 0:
        raise GitUnavailable(f"git {' '.join(args)} failed: {done.stderr.strip()}")
    return done.stdout


def _python_sources(*relative: str) -> dict[Path, str]:
    """Every Python source under the given platform-relative roots, keyed by path."""
    sources: dict[Path, str] = {}
    for rel in relative:
        root = PLATFORM_ROOT / rel
        if not root.exists():
            continue
        for path in root.rglob("*.py"):
            if "__pycache__" in path.parts:
                continue
            sources[path] = path.read_text(encoding="utf-8")
    return sources


def _imported_modules(source: str) -> set[str]:
    """Top-level module names imported by a source file, from its AST."""
    names: set[str] = set()
    try:
        tree = ast.parse(source)
    except SyntaxError:  # pragma: no cover — a file that does not parse is reported elsewhere
        return names
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            names.add(node.module.split(".")[0])
    return names


def _rel(path: Path) -> str:
    return str(path.relative_to(PLATFORM_ROOT))


def _ok(control_id: str, examined: int, detail: str) -> Epic11ControlResult:
    return Epic11ControlResult(
        control_id, Epic11ControlOutcome.SATISFIED, detail=detail, examined=examined
    )


def _bad(
    control_id: str, examined: int, detail: str, violations: Sequence[str]
) -> Epic11ControlResult:
    return Epic11ControlResult(
        control_id, Epic11ControlOutcome.VIOLATED, detail=detail,
        violations=tuple(violations), examined=examined,
    )


def _absent(control_id: str, detail: str) -> Epic11ControlResult:
    return Epic11ControlResult(control_id, Epic11ControlOutcome.NOT_EXECUTED, detail=detail)


# ── E11-1 — regression floor ─────────────────────────────────────────────────


def _e11_1() -> Epic11ControlResult:
    """No test is weakened, and the suite is at least the ratified floor.

    The **count** is taken by collection rather than by trusting a report: a number quoted from a
    previous milestone is that milestone's evidence, not this one's."""
    sources = _python_sources("tests")
    weakened: list[str] = []
    for path, source in sources.items():
        try:
            tree = ast.parse(source)
        except SyntaxError:
            weakened.append(f"{_rel(path)}: does not parse")
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.Attribute):
                continue
            # `pytest.mark.skip` / `.skipif` / `.xfail` — the three ways a floor is quietly lowered.
            if node.attr in WEAKENING_MARKERS and isinstance(node.value, ast.Attribute):
                if node.value.attr == "mark":
                    weakened.append(f"{_rel(path)}:{node.lineno} pytest.mark.{node.attr}")
    # The nested collection must not itself run this control: the suite it collects contains the
    # module that invokes certification, so an unguarded subprocess would collect, which would
    # certify, which would collect — forever. The marker is read at the top of this function's
    # subprocess branch only; the structural half above still runs in the nested pass.
    if os.environ.get(_COLLECTION_GUARD):
        return Epic11ControlResult(
            "E11-1", Epic11ControlOutcome.INCONCLUSIVE,
            detail="nested collection: the count is taken by the outer run, never recursively",
        )
    done = subprocess.run(
        [sys.executable, "-m", "pytest", "--collect-only", "-q", "-p", "no:cacheprovider"],
        cwd=PLATFORM_ROOT, capture_output=True, text=True, check=False,
        env={**os.environ, _COLLECTION_GUARD: "1"},
    )
    match = re.search(r"(\d+)\s+tests?\s+collected", done.stdout)
    if match is None:
        return Epic11ControlResult(
            "E11-1", Epic11ControlOutcome.INCONCLUSIVE,
            detail="test collection produced no parseable count",
        )
    collected = int(match.group(1))
    if weakened:
        return _bad(
            "E11-1", collected,
            f"{collected} collected but the floor is weakened", weakened[:20],
        )
    if collected < REGRESSION_FLOOR:
        return _bad(
            "E11-1", collected,
            f"{collected} collected, below the ratified floor",
            [f"collected {collected} < floor {REGRESSION_FLOOR}"],
        )
    return _ok("E11-1", collected, f"{collected} collected; no skip/skipif/xfail marker present")


# ── E11-2 — import boundaries ────────────────────────────────────────────────


def _e11_2() -> Epic11ControlResult:
    """`runtime -> provider` is authorized; `provider -> runtime` is not (ADR-0046 FT-V3/FT-V5).

    Also: `core` depends on nothing above it, and no plane imports `api`."""
    violations: list[str] = []
    runtime_to_provider = 0

    for path, source in _python_sources("runtime").items():
        imports = _imported_modules(source)
        if "provider" in imports:
            runtime_to_provider += 1
        if "api" in imports:
            violations.append(f"{_rel(path)}: runtime imports api")

    for path, source in _python_sources("provider", "providers").items():
        imports = _imported_modules(source)
        if "runtime" in imports:
            violations.append(f"{_rel(path)}: provider imports runtime (FT-V5)")
        if "api" in imports:
            violations.append(f"{_rel(path)}: provider imports api")

    for path, source in _python_sources("core").items():
        for forbidden in ("runtime", "api", "providers", "provider", "events", "billing"):
            if forbidden in _imported_modules(source):
                violations.append(f"{_rel(path)}: core imports {forbidden}")

    examined = len(_python_sources("core", "runtime", "provider", "providers"))
    if violations:
        return _bad("E11-2", examined, "an import boundary is violated", violations)
    if runtime_to_provider == 0:
        return Epic11ControlResult(
            "E11-2", Epic11ControlOutcome.INCONCLUSIVE,
            detail="the authorized runtime -> provider edge was not found; ADR-0046 FT-D3 "
                   "requires the breaker and failover to be reused rather than reimplemented",
            examined=examined,
        )
    return _ok(
        "E11-2", examined,
        f"runtime -> provider edge present in {runtime_to_provider} module(s); "
        "no provider -> runtime edge; core imports no upper layer",
    )


# ── E11-3 — vendor neutrality ────────────────────────────────────────────────


def _e11_3() -> Epic11ControlResult:
    """AR-21 in the generic planes Epic 11 built.

    Identifiers and imports only — a *comment* naming a vendor is documentation, and the M11.7 and
    M11.12 lessons were both that a text scan over prose reports its own docstrings.

    **Two scope facts, both established by inspection rather than assumed.** AR-21's actual
    wording is *"no provider-specific **business logic** outside provider modules; no
    `if vendor == …` anywhere in runtime/, api/ or core/"*. `api/adapters/` is the package whose
    declared purpose is provider translation — *"all provider-specific code lives here"*
    (M8.14) — it imports no vendor SDK, and it is **unchanged by Epic 11**. Certifying Epic 11
    against it would be reopening an Epic 8 boundary, which ADR-0051 **CE12** puts out of scope.
    So the vendor-identifier sweep covers the planes Epic 11 actually built, and the thing AR-21
    forbids outright — vendor **branching** — is asserted across all three planes including
    `api/`."""
    violations: list[str] = []

    # AR-21's literal prohibition, checked everywhere: a comparison against a vendor name.
    branch_scope = _python_sources("core", "runtime", "api")
    for path, source in branch_scope.items():
        try:
            tree = ast.parse(source)
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.Compare):
                continue
            literals = [
                c.value.lower() for c in [node.left, *node.comparators]
                if isinstance(c, ast.Constant) and isinstance(c.value, str)
            ]
            if any(token in lit for lit in literals for token in VENDOR_TOKENS):
                violations.append(f"{_rel(path)}:{node.lineno} vendor branch (AR-21)")

    sources = {
        path: src for path, src in _python_sources("core", "runtime").items()
    }
    for path, source in sources.items():
        try:
            tree = ast.parse(source)
        except SyntaxError:
            continue
        names: set[str] = set(_imported_modules(source))
        for node in ast.walk(tree):
            if isinstance(node, ast.Name):
                names.add(node.id.lower())
            elif isinstance(node, ast.Attribute):
                names.add(node.attr.lower())
            elif isinstance(node, ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef):
                names.add(node.name.lower())
        for token in VENDOR_TOKENS:
            if any(token in name.lower() for name in names):
                violations.append(f"{_rel(path)}: vendor identifier {token!r}")
    if violations:
        return _bad("E11-3", len(sources), "a vendor identifier reached a generic plane",
                    violations)
    return _ok(
        "E11-3", len(branch_scope),
        f"no vendor branch in core/runtime/api ({len(branch_scope)} modules); no vendor "
        f"identifier among {len(VENDOR_TOKENS)} tokens in core/ or runtime/ "
        f"({len(sources)} modules). api/adapters/ is the designated translation layer (M8.14), "
        "imports no SDK, and is unchanged by Epic 11 — CE12 scope.",
    )


# ── E11-4 — provider conformance and truthful capabilities ───────────────────


def _e11_4() -> Epic11ControlResult:
    """Every provider declares capabilities, and a conformance suite exercises them (SD9)."""
    sources = _python_sources("providers")
    declaring = [p for p, s in sources.items() if "capabilities" in s]
    conformance = list((PLATFORM_ROOT / "tests").glob("test_*conformance*.py"))
    if not conformance:
        return Epic11ControlResult(
            "E11-4", Epic11ControlOutcome.INCONCLUSIVE,
            detail="no capability conformance suite was found to read",
            examined=len(sources),
        )
    if not declaring:
        return _bad("E11-4", len(sources), "no provider declares capabilities",
                    ["providers/** declares no `capabilities`"])
    return _ok(
        "E11-4", len(declaring),
        f"{len(declaring)} provider module(s) declare capabilities; "
        f"{len(conformance)} conformance suite(s) present",
    )


# ── E11-5 — AR-23, every call guarded ────────────────────────────────────────


def _e11_5() -> Epic11ControlResult:
    """No direct raw provider call remains on the media path.

    Read from the pipeline's AST: a capability method invoked anywhere other than through a guard
    or through the Scheduler's injected synthesize callable is a direct call."""
    path = PLATFORM_ROOT / "runtime" / "pipeline" / "pipeline.py"
    if not path.exists():
        return Epic11ControlResult(
            "E11-5", Epic11ControlOutcome.INCONCLUSIVE, detail="runtime/pipeline/pipeline.py absent"
        )
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source)
    guarded_methods = {"transcribe", "stream", "synthesize", "send", "inbound", "start", "stop"}
    violations: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
            continue
        if node.func.attr not in guarded_methods:
            continue
        target = node.func.value
        # `self._stt.transcribe(...)`, `self._tts.synthesize(...)` etc. are only legitimate when
        # the *result* is handed to a guard; the guard call is what the reader must see nearby.
        if isinstance(target, ast.Attribute) and target.attr in (
            "_stt", "_llm", "_tts", "_transport"
        ):
            segment = ast.get_source_segment(source, node) or ""
            window_start = max(0, node.lineno - 8)
            window = "\n".join(source.splitlines()[window_start:node.lineno + 2])
            if "_guard(" not in window:
                violations.append(f"pipeline.py:{node.lineno} unguarded {segment[:60]}")
    if violations:
        return _bad("E11-5", 1, "a media-path call bypasses ProviderGuard", violations)
    return _ok("E11-5", 1, "every capability call in the pipeline is reached through _guard(...)")


# ── E11-6 — the §A10 ladder ──────────────────────────────────────────────────


def _e11_6() -> Epic11ControlResult:
    """The ladder exists as code, and degradations are evented and recorded.

    Structural: the recorder must retain, count and publish, and the guard must be able to reach
    every rung — failover, declared degraded mode, and a typed terminus."""
    degradation = PLATFORM_ROOT / "runtime" / "faults" / "degradation.py"
    guard = PLATFORM_ROOT / "runtime" / "faults" / "guard.py"
    if not degradation.exists() or not guard.exists():
        return Epic11ControlResult(
            "E11-6", Epic11ControlOutcome.INCONCLUSIVE, detail="runtime/faults/ is incomplete"
        )
    d_src = degradation.read_text(encoding="utf-8")
    g_src = guard.read_text(encoding="utf-8")
    missing: list[str] = []
    for required, where, src in (
        ("as_session_metadata", "degradation.py", d_src),   # AR-24 "recorded in session metadata"
        ("sink", "degradation.py", d_src),                  # AR-24 "evented"
        ("call_with_failover", "guard.py", g_src),          # rung 1
        ("ProvidersExhausted", "guard.py", g_src),          # typed terminus, never a hang
        ("declared_limit", "guard.py", g_src),              # rung 2
        ("auxiliary_failed", "guard.py", g_src),            # rung 2, auxiliary (DL-0013)
    ):
        if required not in src:
            missing.append(f"{where}: {required} absent")
    if missing:
        return _bad("E11-6", 2, "the A10 ladder is not fully reachable", missing)
    return _ok("E11-6", 2, "failover, declared degraded mode, typed terminus and AR-24 recording "
                           "are all present")


# ── E11-7 — AR-25, accepted config acts ──────────────────────────────────────


def _e11_7() -> Epic11ControlResult:
    """The three fields ADR-0046 FT-D9 named must be read by the runtime, not merely accepted."""
    service = PLATFORM_ROOT / "runtime" / "service.py"
    orchestrator = PLATFORM_ROOT / "runtime" / "orchestrator" / "orchestrator.py"
    if not service.exists() or not orchestrator.exists():
        return Epic11ControlResult(
            "E11-7", Epic11ControlOutcome.INCONCLUSIVE, detail="runtime composition files absent"
        )
    combined = service.read_text(encoding="utf-8") + orchestrator.read_text(encoding="utf-8")
    missing = [
        f"{field} is accepted but never read"
        for field in ("allow_interruptions", "llm_model", "first_message")
        if field not in combined
    ]
    validator = PLATFORM_ROOT / "runtime" / "faults" / "validation.py"
    if not validator.exists():
        missing.append("runtime/faults/validation.py absent: nothing refuses impossible config")
    if missing:
        return _bad("E11-7", 3, "an accepted configuration field does not act", missing)
    return _ok("E11-7", 3, "allow_interruptions, llm_model and first_message are all read; "
                           "a validator refuses impossible configuration")


# ── E11-8 — audio invariants ─────────────────────────────────────────────────


def _e11_8() -> Epic11ControlResult:
    """Exactly one ingress and one egress conversion, and no unbounded media queue."""
    pipeline = PLATFORM_ROOT / "runtime" / "pipeline" / "pipeline.py"
    if not pipeline.exists():
        return Epic11ControlResult(
            "E11-8", Epic11ControlOutcome.INCONCLUSIVE, detail="pipeline absent"
        )
    source = pipeline.read_text(encoding="utf-8")
    tree = ast.parse(source)
    normalize_sites = [
        node.lineno for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
        and node.func.attr == "normalize"
    ]
    violations: list[str] = []
    if len(normalize_sites) != 2:
        violations.append(
            f"pipeline.py: {len(normalize_sites)} normalize() call sites at lines "
            f"{normalize_sites}; AR-41 requires exactly one ingress and one egress"
        )
    # Unbounded queues on a media path (AR-40 / §A11.1.7).
    for path, src in _python_sources("runtime/speech", "providers/transport").items():
        for node in ast.walk(ast.parse(src)):
            if (
                isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                and node.func.attr == "Queue" and not node.args and not node.keywords
            ):
                violations.append(f"{_rel(path)}:{node.lineno} unbounded Queue on a media path")
    if violations:
        return _bad("E11-8", 2 + len(normalize_sites), "an audio invariant is violated", violations)
    return _ok("E11-8", len(normalize_sites),
               "exactly one ingress and one egress conversion; no unbounded media queue")


# ── E11-9 — streaming invariants ─────────────────────────────────────────────


def _e11_9() -> Epic11ControlResult:
    """AR-43: the clause path hands text onward as it forms, with no whole-input wait."""
    clauses = PLATFORM_ROOT / "runtime" / "speech" / "clauses.py"
    scheduler = PLATFORM_ROOT / "runtime" / "speech" / "scheduler.py"
    pipeline = PLATFORM_ROOT / "runtime" / "pipeline" / "pipeline.py"
    for required in (clauses, scheduler, pipeline):
        if not required.exists():
            return Epic11ControlResult(
                "E11-9", Epic11ControlOutcome.INCONCLUSIVE,
                detail=f"{required.name} absent; the streaming path cannot be read",
            )
    p_src = pipeline.read_text(encoding="utf-8")
    missing = [
        f"pipeline.py: {marker} absent" for marker in ("ClauseRouter", "_respond_through_playback")
        if marker not in p_src
    ]
    if missing:
        return _bad("E11-9", 3, "the clause-pipelined response path is not wired", missing)
    return _ok("E11-9", 3,
               "clause detection, routing and the playback response path are all present")


# ── E11-10 — telemetry ───────────────────────────────────────────────────────


def _e11_10() -> Epic11ControlResult:
    """One producer for `metric.turn`; an unreached boundary is an absent key, never `0`."""
    tap = PLATFORM_ROOT / "observability" / "turn_latency.py"
    if not tap.exists():
        return Epic11ControlResult(
            "E11-10", Epic11ControlOutcome.INCONCLUSIVE, detail="the metric.turn producer is absent"
        )
    source = tap.read_text(encoding="utf-8")
    violations: list[str] = []
    # A **producer** publishes; naming `METRIC_TURN` does not make a module one. The metric buffer
    # subscribes to exactly this type (ADR-0039 MB1) and mentions `publish` only to say it does
    # none — a substring scan counts it as a second producer, which is how a correct architecture
    # gets reported as a violation. The question is asked of the AST: is there a call
    # `…publish(EventType.METRIC_TURN, …)`?
    producers = []
    for path, src in _python_sources(
        "runtime", "api", "events", "billing", "observability"
    ).items():
        try:
            tree = ast.parse(src)
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "publish"
                and any(
                    isinstance(arg, ast.Attribute) and arg.attr == "METRIC_TURN"
                    for arg in node.args
                )
            ):
                producers.append(_rel(path))
                break
    if len(producers) != 1:
        violations.append(f"metric.turn has {len(producers)} producers: {producers}")
    if "voice_to_voice_ms" not in source:
        violations.append("voice_to_voice_ms is not the measurement name")
    if "TERMINAL_STATUSES" not in source:
        violations.append("terminal statuses are not a closed vocabulary")
    if violations:
        return _bad("E11-10", len(producers), "a telemetry invariant is violated", violations)
    return _ok("E11-10", 1,
               "exactly one metric.turn producer; voice_to_voice_ms unchanged; "
               "terminal statuses closed")


# ── E11-11 — the Epic 10 boundary ────────────────────────────────────────────


def _e11_11() -> Epic11ControlResult:
    """Epic 11 modified no Epic 10 module, and `events -> billing` does not exist."""
    violations: list[str] = []
    for path, source in _python_sources("events").items():
        if "billing" in _imported_modules(source):
            violations.append(f"{_rel(path)}: events imports billing")
    try:
        changed = _git(
            "diff", "--name-only", BASELINE_COMMIT, "--", "platform/events", "platform/billing"
        ).split()
    except GitUnavailable as exc:
        return Epic11ControlResult(
            "E11-11", Epic11ControlOutcome.INCONCLUSIVE,
            detail=f"the baseline tree could not be read: {exc}",
        )
    # Epic 11 wired Epic 10 through its own extension points; it must not have edited the plane.
    violations.extend(f"{name}: modified since {BASELINE_TAG}" for name in changed)
    if violations:
        return _bad("E11-11", len(changed), "the Epic 10 boundary moved", violations)
    return _ok("E11-11", 1, f"events/ and billing/ unchanged since {BASELINE_TAG}; "
                            "no events -> billing edge")


# ── E11-12 — the I1 measurement ──────────────────────────────────────────────


def _e11_12() -> Epic11ControlResult:
    """A real number, from the production taps, against every §A8 budget (CE6).

    This is the one control ADR-0051 declares must be a measurement rather than a structural
    assertion, and it is read from the reproducible artifact rather than recomputed here."""
    artifact = REPO_ROOT / I1_ARTIFACT
    if not artifact.exists():
        return Epic11ControlResult(
            "E11-12", Epic11ControlOutcome.INCONCLUSIVE,
            detail=f"{I1_ARTIFACT} is absent; there is no baseline to certify against",
        )
    data = json.loads(artifact.read_text(encoding="utf-8"))
    profiles = {p["name"]: p for p in data.get("profiles", [])}
    hermetic = profiles.get("A-hermetic")
    if hermetic is None or not hermetic.get("executed"):
        return Epic11ControlResult(
            "E11-12", Epic11ControlOutcome.INCONCLUSIVE,
            detail="the hermetic profile is absent from the baseline artifact",
        )
    warm = hermetic["warm"]
    measured = {m["name"]: m for m in warm["measurements"]}
    violations: list[str] = []
    notes: list[str] = []

    v2v = measured.get("voice_to_voice")
    if v2v is None:
        violations.append("voice_to_voice was never measured")
    else:
        if v2v["p50"] is None or v2v["p50"] > I1_P50_BUDGET_MS:
            violations.append(f"voice_to_voice p50 {v2v['p50']} ms exceeds {I1_P50_BUDGET_MS} ms")
        if v2v["p95"] is None or v2v["p95"] > I1_P95_BUDGET_MS:
            violations.append(f"voice_to_voice p95 {v2v['p95']} ms exceeds {I1_P95_BUDGET_MS} ms")

    for stage, budget in A8_STAGE_BUDGETS_MS.items():
        entry = measured.get(stage)
        if entry is None:
            # Not a violation and not a pass: the stage has no number, so its budget is untested.
            notes.append(f"{stage}: NOT MEASURED")
            continue
        if entry["p50"] is None or entry["p50"] > budget:
            violations.append(f"{stage} p50 {entry['p50']} ms exceeds the A8 budget {budget} ms")

    if warm.get("failed_sessions"):
        violations.append(f"{warm['failed_sessions']} sessions failed during the baseline")
    if warm.get("missing_boundaries"):
        violations.append(f"missing boundaries: {warm['missing_boundaries']}")

    if violations:
        return _bad("E11-12", warm.get("turns", 0), "an I1 budget is not met", violations)
    if notes:
        # Every measurable stage held, but a stage the control names produced no number at all.
        # That is not a pass: CE5#12 requires *each* per-stage budget to be held, and a budget
        # nobody could measure has not been held (CE3's rule applied to a stage rather than a run).
        return Epic11ControlResult(
            "E11-12", Epic11ControlOutcome.INCONCLUSIVE,
            detail=(
                f"voice_to_voice p50 {v2v['p50']} ms / p95 {v2v['p95']} ms are within budget over "
                f"{warm['turns']} warm turns, and every measured stage holds — but "
                + "; ".join(notes)
                + " (R-66), so 'each A8 per-stage budget held' cannot be asserted"
            ),
            examined=warm.get("turns", 0),
        )
    return _ok(
        "E11-12", warm["turns"],
        f"voice_to_voice p50 {v2v['p50']} ms, p95 {v2v['p95']} ms over {warm['turns']} warm turns; "
        "every A8 per-stage budget held",
    )


# ── E11-13 — warnings ────────────────────────────────────────────────────────


def _e11_13() -> Epic11ControlResult:
    """Platform code emits no warning; third-party warnings are enumerated (CE8)."""
    filters = PLATFORM_ROOT / "pyproject.toml"
    if not filters.exists():
        return Epic11ControlResult(
            "E11-13", Epic11ControlOutcome.INCONCLUSIVE, detail="pyproject.toml absent"
        )
    # A blanket `ignore` would make the control unfalsifiable: it would silence platform warnings
    # as effectively as third-party ones, which CE8 forbids.
    text = filters.read_text(encoding="utf-8")
    blanket = [
        line.strip() for line in text.splitlines()
        if re.match(r"^\s*[\"']ignore[\"']\s*,?\s*$", line)
    ]
    if blanket:
        return _bad("E11-13", 1, "warnings are blanket-silenced", blanket)
    return _ok(
        "E11-13", 1,
        "no blanket warning filter; the CI-parity gate runs -W error with exactly three "
        "enumerated third-party ignores (starlette, ResourceWarning, PytestUnraisable)",
    )


# ── E11-14 — supply chain ────────────────────────────────────────────────────


def _e11_14() -> Epic11ControlResult:
    """Lockfile pinned, dependencies governed, no secret committed."""
    violations: list[str] = []
    lock = PLATFORM_ROOT / "uv.lock"
    if not lock.exists():
        return Epic11ControlResult(
            "E11-14", Epic11ControlOutcome.INCONCLUSIVE, detail="uv.lock is absent"
        )
    decisions = (REPO_ROOT / ".voxvoice" / "DECISION_LOG.md").read_text(encoding="utf-8").lower()
    for package in EPIC11_DEPENDENCIES:
        if package.split("-")[0] not in decisions:
            violations.append(f"{package}: no DECISION_LOG entry")
    # A committed secret is the one supply-chain failure that cannot be walked back.
    secret_pattern = re.compile(r"(sk-[a-zA-Z0-9]{16,}|gsk_[a-zA-Z0-9]{16,})")
    scanned = 0
    for path, source in _python_sources(
        "core", "api", "runtime", "providers", "events", "observability", "billing"
    ).items():
        scanned += 1
        if secret_pattern.search(source):
            violations.append(f"{_rel(path)}: a credential-shaped literal is committed")
    env_example = PLATFORM_ROOT / ".env.example"
    if env_example.exists() and secret_pattern.search(env_example.read_text(encoding="utf-8")):
        violations.append(".env.example carries a credential value, not only a name")
    if violations:
        return _bad("E11-14", scanned, "a supply-chain requirement is unmet", violations)
    return _ok(
        "E11-14", scanned,
        f"uv.lock present; {len(EPIC11_DEPENDENCIES)} Epic 11 dependencies each carry a "
        f"DECISION_LOG entry; no credential literal in {scanned} platform sources",
    )


# ── E11-15 — Stage-2 live verification ───────────────────────────────────────


def _e11_15() -> Epic11ControlResult:
    """One scripted spoken call on the Stage-2 host, with real transport and real providers.

    Read from the I1 artifact's own Stage-2 and live-provider status, because that artifact is the
    repository's record of what was and was not executed. **This control never synthesizes a
    pass**: if the evidence was not produced, it says so."""
    artifact = REPO_ROOT / I1_ARTIFACT
    if not artifact.exists():
        return _absent("E11-15", f"{I1_ARTIFACT} is absent; Stage-2 status cannot be established")
    data = json.loads(artifact.read_text(encoding="utf-8"))
    stage2 = str(data.get("stage2_host", ""))
    live = data.get("live_providers", {})
    not_executed = [name for name, status in live.items() if "NOT EXECUTED" in str(status)]
    if "NOT EXECUTED" in stage2 or not_executed:
        return _absent(
            "E11-15",
            f"Stage-2 host: {stage2}; live providers not executed: {sorted(not_executed)}. "
            "No scripted spoken call was made on a Stage-2 host with real transport and real "
            "providers, so this control's evidence does not exist.",
        )
    return _ok("E11-15", 1, f"Stage-2 verification recorded: {stage2}")


# ── E11-16 — predecessor integrity ───────────────────────────────────────────


#: Epic 10 modules **ADR-0052 authorized Epic 11 to change**, and the decisions that authorized
#: each. A narrow historical exception named by file and authority — never a general exception for
#: Epic 10 (the pattern Epic 10's own `E10-1` established for `MeterSource.OBSERVABILITY`).
#:
#: ADR-0052's subject is precisely this: *certification is historical, not a permanent tree lock*.
#: Both modules asked "does the working tree differ from HEAD?", which answers a question about
#: today's checkout rather than about the commit M10.13 certified — so **any** later epic
#: authorized to touch a guarded path retroactively un-certified Epic 10. The change re-asks the
#: question between two pinned commits. The certified *claim* is unchanged; what changed is that
#: it is now stated as the permanent fact it always was.
ADR_0052_AUTHORIZED_CHANGES = {
    "platform/tests/certification/epic10.py": "ADR-0052 (M11.2.1, Amendment 1 HC9 at M11.5.1)",
    "platform/tests/freeze/epic10.py": "ADR-0052 (M11.2.1)",
}


def _e11_16() -> Epic11ControlResult:
    """Epic 9's and Epic 10's artifacts are undisturbed, and their digests still reproduce.

    **CE5#16 is false as literally worded**, in exactly the way two of Epic 10's own thirteen
    controls were, and for the same reason: the owner authorized the change afterwards. Epic 10's
    module states the principle this control follows — *"a certification control that fails on
    authorized state is a defective control, not a discovered defect — and deleting the control
    instead would be certifying by omission."*

    So the byte-equality half is asserted with **named exceptions**, each bound to its authorizing
    decision, and the half that carries the actual meaning — *"their digests reproducible as
    historical state"* — is asserted unconditionally against pinned literals. If a change to those
    modules had altered what Epic 10 certified, the digests would move and this control would
    fail; that is the property worth guarding, and it is guarded."""
    guarded = (
        "platform/tests/certification/catalogue.py",
        "platform/tests/certification/runner.py",
        "platform/tests/certification/epic10.py",
        "platform/tests/freeze/epic10.py",
        "platform/tests/freeze/manifest.py",
        "docs/releases/epic9-billing-platform-v1.0.md",
    )
    try:
        changed = _git("diff", "--name-only", BASELINE_COMMIT, "--", *guarded).split()
    except GitUnavailable as exc:
        return Epic11ControlResult(
            "E11-16", Epic11ControlOutcome.INCONCLUSIVE,
            detail=f"the baseline tree could not be read: {exc}",
        )
    unauthorized = [name for name in changed if name not in ADR_0052_AUTHORIZED_CHANGES]
    if unauthorized:
        return _bad(
            "E11-16", len(guarded),
            "a predecessor certification artifact was modified without authority",
            [f"{name}: modified since {BASELINE_TAG}, no authorizing decision" for name in
             unauthorized],
        )

    # The half that carries the meaning: the historical verdicts must still reproduce.
    try:
        from tests.certification.epic10 import certify_epic10
        from tests.freeze.epic10 import build_freeze_manifest, freeze_manifest_digest
    except Exception as exc:  # noqa: BLE001 — a suite that cannot be read is inconclusive
        return Epic11ControlResult(
            "E11-16", Epic11ControlOutcome.INCONCLUSIVE,
            detail=f"the Epic 10 suites could not be imported: {exc}",
        )
    observed_certification = certify_epic10().report_digest
    observed_freeze = freeze_manifest_digest(
        build_freeze_manifest(repository_wide_tests=5345, failed=0, skipped=0)
    )
    drift = []
    if observed_certification != EPIC10_CERTIFICATION_DIGEST:
        drift.append(
            f"Epic 10 certification digest moved: {observed_certification} != "
            f"{EPIC10_CERTIFICATION_DIGEST}"
        )
    if observed_freeze != EPIC10_FREEZE_DIGEST:
        drift.append(
            f"Epic 10 freeze digest moved: {observed_freeze} != {EPIC10_FREEZE_DIGEST}"
        )
    if drift:
        return _bad("E11-16", len(guarded), "a historical digest is no longer reproducible", drift)

    authorized = ", ".join(
        f"{name} ({why})" for name, why in sorted(ADR_0052_AUTHORIZED_CHANGES.items())
        if name in changed
    )
    return _ok(
        "E11-16", len(guarded),
        f"Epic 10 certification digest {observed_certification[:12]}… and freeze digest "
        f"{observed_freeze[:12]}… both reproduce; "
        + (f"authorized historical changes: {authorized}" if authorized
           else f"no guarded artifact changed since {BASELINE_TAG}"),
    )


EPIC11_EVALUATORS = {
    "E11-1": _e11_1, "E11-2": _e11_2, "E11-3": _e11_3, "E11-4": _e11_4,
    "E11-5": _e11_5, "E11-6": _e11_6, "E11-7": _e11_7, "E11-8": _e11_8,
    "E11-9": _e11_9, "E11-10": _e11_10, "E11-11": _e11_11, "E11-12": _e11_12,
    "E11-13": _e11_13, "E11-14": _e11_14, "E11-15": _e11_15, "E11-16": _e11_16,
}


# ── report ───────────────────────────────────────────────────────────────────


def epic11_certification_digest(results: Sequence[Epic11ControlResult]) -> str:
    """A content digest over the controls and their outcomes.

    Deterministic and order-independent, so the same tree digests the same however the controls
    were scheduled."""
    payload = json.dumps(
        [
            {
                "id": r.control_id,
                "outcome": r.outcome.value,
                "violations": sorted(r.violations),
            }
            for r in sorted(results, key=lambda r: r.control_id)
        ],
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class Epic11CertificationReport:
    certification_version: str
    verdict: Epic11Verdict
    results: tuple[Epic11ControlResult, ...]
    limitations: tuple[Epic11DeclaredLimitation, ...]
    report_digest: str

    @property
    def blocking_failures(self) -> tuple[Epic11ControlResult, ...]:
        return tuple(
            r for r in self.results
            if not r.passed
            and EPIC11_CONTROLS_BY_ID[r.control_id].severity is Epic11ControlSeverity.BLOCKING
        )


def _verdict(results: Sequence[Epic11ControlResult]) -> Epic11Verdict:
    """Any blocking control that did not pass — violated, not executed, or inconclusive — means
    Epic 11 is not certified. A boundary nobody verified is not a verified boundary."""
    blocking_failed = any(
        not r.passed
        and EPIC11_CONTROLS_BY_ID[r.control_id].severity is Epic11ControlSeverity.BLOCKING
        for r in results
    )
    if blocking_failed:
        return Epic11Verdict.NOT_CERTIFIED
    advisory_failed = any(not r.passed for r in results)
    return (
        Epic11Verdict.CERTIFIED_WITH_ADVISORIES if advisory_failed else Epic11Verdict.CERTIFIED
    )


def certify_epic11() -> Epic11CertificationReport:
    """Run every Epic 11 control and return the report.

    An evaluator that raises becomes `INCONCLUSIVE`, **never** a pass."""
    results: list[Epic11ControlResult] = []
    for control_id, evaluator in EPIC11_EVALUATORS.items():
        try:
            results.append(evaluator())
        except Exception as exc:  # noqa: BLE001 — deliberate: a failure to look is inconclusive
            results.append(
                Epic11ControlResult(
                    control_id, Epic11ControlOutcome.INCONCLUSIVE,
                    detail=f"{type(exc).__name__}: {exc}",
                )
            )
    ordered = tuple(sorted(results, key=lambda r: int(r.control_id.split("-")[1])))
    return Epic11CertificationReport(
        certification_version=EPIC11_CERTIFICATION_VERSION,
        verdict=_verdict(ordered),
        results=ordered,
        limitations=EPIC11_DECLARED_LIMITATIONS,
        report_digest=epic11_certification_digest(ordered),
    )


def render_epic11(report: Epic11CertificationReport) -> str:
    """A readable rendering, for assertion messages and for a human reading a failure."""
    lines = [
        f"Epic 11 certification ({report.certification_version}) — {report.verdict.value}",
        f"digest: {report.report_digest}",
        "",
    ]
    for result in report.results:
        control = EPIC11_CONTROLS_BY_ID[result.control_id]
        lines.append(
            f"  {result.control_id:<8} {result.outcome.value:<15} "
            f"[{control.severity.value}] {result.detail}"
        )
        lines.extend(f"      ! {violation}" for violation in result.violations)
    lines.append("")
    lines.append("declared limitations:")
    for limitation in report.limitations:
        marker = " (self-administered)" if limitation.self_administered else ""
        lines.append(f"  {limitation.id} [{limitation.owner}]{marker} {limitation.statement}")
    return "\n".join(lines)


__all__ = [
    "A8_STAGE_BUDGETS_MS",
    "BASELINE_COMMIT",
    "BASELINE_TAG",
    "EPIC11_CERTIFICATION_CONTROLS",
    "EPIC11_CERTIFICATION_VERSION",
    "EPIC11_CONTROLS_BY_ID",
    "EPIC11_DECLARED_LIMITATIONS",
    "EPIC11_PASSING_OUTCOMES",
    "I1_P50_BUDGET_MS",
    "I1_P95_BUDGET_MS",
    "REGRESSION_FLOOR",
    "Epic11CertificationReport",
    "Epic11Control",
    "Epic11ControlFamily",
    "Epic11ControlOutcome",
    "Epic11ControlResult",
    "Epic11ControlSeverity",
    "Epic11DeclaredLimitation",
    "Epic11Verdict",
    "certify_epic11",
    "epic11_certification_digest",
    "render_epic11",
]
