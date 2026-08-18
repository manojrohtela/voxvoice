"""Billing Certification catalogue (M9.24) — the architecture review, rendered as a verdict.

The roadmap specifies M9.24 exactly (`docs/roadmap/epic9-implementation-plan.md` §10, P6):

> **M9.24 Certification** — independent certification against ADR-0023, the Blueprint, and the
> Provider Strategy. Output: *a certification report with an honest verdict*. Test strategy:
> *an executable certification suite*. DoD: **"Certification is reproducible as tests, not a
> one-off document."**

So this milestone owns exactly one fact, and it is not a financial one:

> **Does this repository satisfy the architecture it ratified?**

`M9.24 owns no revenue fact, no money, no account, no period and no document.` It reads structure —
module graphs, export namespaces, schema metadata, route tables, FSM definitions — and returns a
verdict. It writes nothing at all: there is no table, no migration, no repository and no command
bus in this milestone, and the absence is the design (see the architecture document).

## Why the verdict is a value and not a stored row

A certification stored in a database is a claim about a repository state that has already moved on.
The DoD sentence above is the whole argument: the authority is **the suite re-run against the
current tree**, so the report is derived, content-addressed on the evidence it examined, and
regenerated rather than updated. A row would be a second, staler answer to a question the tests
already answer definitively.

## The three outcomes, and why the third one exists

`SATISFIED` and `VIOLATED` are not enough. A control whose evidence could not be gathered — an
optional seam absent, a package not importable in this configuration — is **`INCONCLUSIVE`**, never
`SATISFIED`. This is the same rule M9.21 wrote for evidence, M9.22 for report sources and M9.23 for
consolidation sources, applied one altitude up: *"could not check" must never render as "checked
and fine."* A certification that quietly passes what it failed to examine is worse than no
certification, because it is believed.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum

from billing.foundation import canonical_digest

#: Bumped when a change here would produce a different verdict from the same repository. Recorded
#: on the report, so a digest that does not reproduce is explained rather than investigated.
CERTIFICATION_VERSION = "m9.24.1"


class ControlFamily(StrEnum):
    """Which ratified document a control certifies against.

    A closed vocabulary so *"what authority says this?"* is answerable from the control alone,
    rather than from prose somebody has to go and read."""

    OWNERSHIP = "ownership"  # ADR-0023 §5/§6/§7 — who may write what
    REVENUE_SAFETY = "revenue_safety"  # ADR-0023 RS1–RS6
    PAYMENT = "payment"  # ADR-0023 PY1–PY9
    TAX = "tax"  # ADR-0023 TX1–TX6
    PLACEMENT = "placement"  # ADR-0031 — where the plane lives, import direction
    CONSUMER = "consumer"  # ADR-0021 — Studio owns nothing
    NAMESPACE = "namespace"  # repository hygiene the epic learned the hard way
    DETERMINISM = "determinism"  # identity, digests, reproducibility


class ControlSeverity(StrEnum):
    """Whether a violation blocks certification.

    `BLOCKING` means the repository is **not certified** while it stands. `ADVISORY` records
    something a reader should know that does not by itself invalidate the architecture — and an
    advisory is never silently dropped, because "nobody mentioned it" is how debt becomes
    permanent."""

    BLOCKING = "blocking"
    ADVISORY = "advisory"


class ControlOutcome(StrEnum):
    """**Four outcomes, and the last two are the point.**

    `NOT_APPLICABLE` is a positive statement: the control was evaluated and the condition it guards
    cannot arise here. `INCONCLUSIVE` is an admission: the evidence could not be gathered, so
    nothing is claimed. Neither is ever collapsed into `SATISFIED`."""

    SATISFIED = "satisfied"
    VIOLATED = "violated"
    NOT_APPLICABLE = "not_applicable"
    INCONCLUSIVE = "inconclusive"


#: The outcomes that let a control pass. `INCONCLUSIVE` is deliberately absent: a control that
#: could not look has not passed, and a certification suite that treated it as a pass would be
#: certifying its own blind spots.
PASSING_OUTCOMES: frozenset[ControlOutcome] = frozenset(
    {ControlOutcome.SATISFIED, ControlOutcome.NOT_APPLICABLE}
)


@dataclass(frozen=True, slots=True)
class CertificationControl:
    """One certifiable statement, bound to the clause that ratified it.

    `clauses` is never empty. A control with no ratified authority behind it is somebody's opinion
    rendered as a test, and this suite exists precisely to distinguish the two."""

    id: str
    family: ControlFamily
    severity: ControlSeverity
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
class ControlResult:
    """What one control found. Immutable.

    `violations` names the offending symbols, tables, modules or routes — never a count alone. A
    result that says "3 violations" and cannot say which three is not actionable, and an
    unactionable finding is how a certification becomes a ritual."""

    control_id: str
    outcome: ControlOutcome
    detail: str = ""
    violations: tuple[str, ...] = ()
    examined: int = 0

    @property
    def passed(self) -> bool:
        return self.outcome in PASSING_OUTCOMES

    def __post_init__(self) -> None:
        if self.outcome is ControlOutcome.VIOLATED and not self.violations:
            raise ValueError(
                f"control {self.control_id!r} reports a violation with nothing named; "
                "a violation that cannot be located cannot be fixed"
            )
        if self.outcome is ControlOutcome.INCONCLUSIVE and not self.detail:
            raise ValueError(
                f"control {self.control_id!r} is inconclusive with no reason; "
                "'could not check' must always say why"
            )


class CertificationVerdict(StrEnum):
    """The honest verdict the roadmap asks for.

    `CERTIFIED_WITH_ADVISORIES` is a real answer, not a softened failure: the architecture holds and
    there is recorded debt. `NOT_CERTIFIED` follows from any blocking violation **or any
    inconclusive blocking control** — because a blocking guarantee nobody could verify is not a
    guarantee."""

    CERTIFIED = "certified"
    CERTIFIED_WITH_ADVISORIES = "certified_with_advisories"
    NOT_CERTIFIED = "not_certified"


@dataclass(frozen=True, slots=True)
class CertificationReport:
    """The full result. Content-addressed on what it examined, never on when it ran."""

    verdict: CertificationVerdict
    results: tuple[ControlResult, ...]
    certification_version: str = CERTIFICATION_VERSION
    report_digest: str = ""

    @property
    def blocking_violations(self) -> tuple[ControlResult, ...]:
        return tuple(
            r
            for r in self.results
            if r.outcome is ControlOutcome.VIOLATED
            and CONTROLS_BY_ID[r.control_id].severity is ControlSeverity.BLOCKING
        )

    @property
    def advisories(self) -> tuple[ControlResult, ...]:
        return tuple(
            r
            for r in self.results
            if r.outcome is ControlOutcome.VIOLATED
            and CONTROLS_BY_ID[r.control_id].severity is ControlSeverity.ADVISORY
        )

    @property
    def inconclusive(self) -> tuple[ControlResult, ...]:
        return tuple(r for r in self.results if r.outcome is ControlOutcome.INCONCLUSIVE)

    @property
    def is_certified(self) -> bool:
        return self.verdict is not CertificationVerdict.NOT_CERTIFIED


# ── the control catalogue ───────────────────────────────────────────────────────
#
# Declared as data rather than as a list of test functions, so "what is certified?" is answerable
# by reading one table — and so a control cannot be quietly deleted to make a suite green: the
# catalogue is asserted complete, and every control in it must produce a result.

CERTIFICATION_CONTROLS: tuple[CertificationControl, ...] = (
    # ── ownership (ADR-0023 §5–§7) ──────────────────────────────────────────────
    CertificationControl(
        id="OWN-1",
        family=ControlFamily.OWNERSHIP,
        severity=ControlSeverity.BLOCKING,
        statement="Billing writes only tables in its own `cp_billing_*` namespace.",
        clauses=("ADR-0023 §6",),
    ),
    CertificationControl(
        id="OWN-2",
        family=ControlFamily.OWNERSHIP,
        severity=ControlSeverity.BLOCKING,
        statement="No Billing table names a Forbidden-Write concept it would author.",
        clauses=("ADR-0023 §7",),
    ),
    CertificationControl(
        id="OWN-3",
        family=ControlFamily.OWNERSHIP,
        severity=ControlSeverity.BLOCKING,
        statement="Billing emits no `metric.*` event; it consumes that stream and never produces "
            "on it.",
        clauses=("ADR-0023 §7",),
    ),
    CertificationControl(
        id="OWN-4",
        family=ControlFamily.OWNERSHIP,
        severity=ControlSeverity.BLOCKING,
        statement="Every Billing domain event is namespaced `billing.*`.",
        clauses=("ADR-0023 §6.18",),
    ),
    # ── revenue safety (ADR-0023 RS1–RS6) ───────────────────────────────────────
    CertificationControl(
        id="RS-1",
        family=ControlFamily.REVENUE_SAFETY,
        severity=ControlSeverity.BLOCKING,
        statement="Append-only aggregates expose no update or delete method on their repository "
            "contract.",
        clauses=("ADR-0023 RS1", "ADR-0023 RS3"),
    ),
    CertificationControl(
        id="RS-5",
        family=ControlFamily.REVENUE_SAFETY,
        severity=ControlSeverity.BLOCKING,
        statement="No float reaches a money field: money is integer minor units paired with a "
            "currency.",
        clauses=("ADR-0023 RS5",),
    ),
    CertificationControl(
        id="RS-6",
        family=ControlFamily.REVENUE_SAFETY,
        severity=ControlSeverity.BLOCKING,
        statement="No currency conversion or FX rate exists anywhere in the Billing plane.",
        clauses=("ADR-0023 RS5",),
    ),
    # ── payment (ADR-0023 PY5, PY6) ─────────────────────────────────────────────
    CertificationControl(
        id="PY-5",
        family=ControlFamily.PAYMENT,
        severity=ControlSeverity.BLOCKING,
        statement="No integrity finding, incident or divergence is auto-resolved by a state "
            "machine edge.",
        clauses=("ADR-0023 PY5",),
    ),
    CertificationControl(
        id="PY-6",
        family=ControlFamily.PAYMENT,
        severity=ControlSeverity.BLOCKING,
        statement="No payment instrument data appears in any model, DTO, event or Studio page.",
        clauses=("ADR-0023 PY6",),
    ),
    # ── tax (ADR-0023 TX1, TX4) ─────────────────────────────────────────────────
    CertificationControl(
        id="TX-1",
        family=ControlFamily.TAX,
        severity=ControlSeverity.BLOCKING,
        statement="Billing hard-codes no tax rate table; determination is a rented edge.",
        clauses=("ADR-0023 TX1", "ADR-0023 TX5"),
    ),
    # ── placement (ADR-0031 IR1–IR4) ────────────────────────────────────────────
    CertificationControl(
        id="PL-1",
        family=ControlFamily.PLACEMENT,
        severity=ControlSeverity.BLOCKING,
        statement="Billing imports no other plane: no runtime, actuator, api or product import.",
        clauses=("ADR-0031 IR1", "ADR-0031 IR3"),
    ),
    CertificationControl(
        id="PL-2",
        family=ControlFamily.PLACEMENT,
        severity=ControlSeverity.BLOCKING,
        statement="No frozen Epic imports Billing; the dependency direction is one-way.",
        clauses=("ADR-0031 IR4",),
    ),
    CertificationControl(
        id="PL-3",
        family=ControlFamily.PLACEMENT,
        severity=ControlSeverity.BLOCKING,
        statement="Foundation is pure: no clock, no database, no network, no provider, no "
            "authorization.",
        clauses=("ADR-0031 T1", "ADR-0031 PS1"),
    ),
    CertificationControl(
        id="PL-4",
        family=ControlFamily.PLACEMENT,
        severity=ControlSeverity.BLOCKING,
        statement="No vendor or provider SDK type appears outside an adapter boundary.",
        clauses=("ADR-0031 PS2",),
    ),
    # ── consumer (ADR-0021) ─────────────────────────────────────────────────────
    CertificationControl(
        id="ST-1",
        family=ControlFamily.CONSUMER,
        severity=ControlSeverity.BLOCKING,
        statement="No Studio page computes a new financial value: no total, aggregation, tax or "
        "percentage is derived client-side.",
        clauses=("ADR-0021 Forbidden Writes", "ADR-0023 §5.4"),
    ),
    CertificationControl(
        id="ST-3",
        family=ControlFamily.CONSUMER,
        severity=ControlSeverity.ADVISORY,
        statement="No Studio page rescales a currency by a hardcoded exponent when rendering "
        "integer minor units.",
        clauses=("ADR-0023 RS5", "ADR-0021"),
    ),
    CertificationControl(
        id="ST-2",
        family=ControlFamily.CONSUMER,
        severity=ControlSeverity.BLOCKING,
        statement="No Studio page stores domain state in localStorage, sessionStorage or a cookie.",
        clauses=("ADR-0021",),
    ),
    # ── namespace hygiene ───────────────────────────────────────────────────────
    #
    # The class this epic learned the hard way: M9.20 a duplicate identity helper, M9.21 a wire
    # collision, M9.22 seven symbol collisions, M9.23 an index collision. These controls are the
    # structural answer.
    CertificationControl(
        id="NS-1",
        family=ControlFamily.NAMESPACE,
        severity=ControlSeverity.BLOCKING,
        statement="No exported symbol is defined by two Billing modules as two different objects.",
        clauses=("CODING_STANDARDS §3", "M9.22 D-1", "M9.23 D-4"),
    ),
    CertificationControl(
        id="NS-2",
        family=ControlFamily.NAMESPACE,
        severity=ControlSeverity.BLOCKING,
        statement="Every SQL index name in the whole schema is unique.",
        clauses=("M9.22 D-2", "M9.23 D-2"),
    ),
    CertificationControl(
        id="NS-3",
        family=ControlFamily.NAMESPACE,
        severity=ControlSeverity.BLOCKING,
        statement="Every SQL table name in the whole schema is unique and `cp_billing_*`-scoped.",
        clauses=("ADR-0031 PL4",),
    ),
    CertificationControl(
        id="NS-4",
        family=ControlFamily.NAMESPACE,
        severity=ControlSeverity.BLOCKING,
        statement="No API route path is served by two routers; every prefix is disjoint.",
        clauses=("ADR-0031 CR3",),
    ),
    CertificationControl(
        id="NS-5",
        family=ControlFamily.NAMESPACE,
        # **Advisory, and the reason is the mechanism, not the count.** NS-1 is blocking because
        # Python *silently* serves one of two objects under one name. FastAPI does the opposite:
        # it detects the collision and qualifies the schema name, so no client is ever handed an
        # ambiguous contract — the cost is a generated document that reads badly. No ratified
        # clause requires unqualified schema names; this is repository hygiene with a named owner,
        # recorded as LIM-8 rather than treated as an architecture violation.
        severity=ControlSeverity.ADVISORY,
        statement="No OpenAPI schema name is module-qualified by a Pydantic model collision.",
        clauses=("M9.21 D-2",),
    ),
    CertificationControl(
        id="NS-6",
        family=ControlFamily.NAMESPACE,
        severity=ControlSeverity.ADVISORY,
        statement="No package `__all__` lists the same name twice.",
        clauses=("CODING_STANDARDS §3",),
    ),
    # ── determinism ─────────────────────────────────────────────────────────────
    CertificationControl(
        id="DT-1",
        family=ControlFamily.DETERMINISM,
        severity=ControlSeverity.BLOCKING,
        statement="Every declared lifecycle has reachable terminal states and no unreachable "
            "state.",
        clauses=("ADR-0023 RS2", "ADR-0031 T5"),
    ),
    CertificationControl(
        id="DT-2",
        family=ControlFamily.DETERMINISM,
        severity=ControlSeverity.BLOCKING,
        statement="No identity is derived from wall-clock time, a random value or a UUID alone.",
        clauses=("ADR-0023 RS2",),
    ),
    CertificationControl(
        id="DT-3",
        family=ControlFamily.DETERMINISM,
        severity=ControlSeverity.BLOCKING,
        statement="Exactly one canonicalizer exists; no module declares a second one.",
        clauses=("M9.21 §3", "ADR-0023 RS2"),
    ),
    CertificationControl(
        id="DT-4",
        family=ControlFamily.DETERMINISM,
        severity=ControlSeverity.BLOCKING,
        statement="Every migration is additive and the chain is contiguous and ordered.",
        clauses=("ADR-0031 DD4",),
    ),
)

CONTROLS_BY_ID: dict[str, CertificationControl] = {c.id: c for c in CERTIFICATION_CONTROLS}


class CertificationService:
    """Pure. Turns control results into a verdict, and nothing else.

    No I/O, no clock, no import machinery: evidence gathering belongs to the Application, because
    reading a module graph is I/O and Foundation does none."""

    @staticmethod
    def verdict(results: Sequence[ControlResult]) -> CertificationVerdict:
        """**A blocking control that could not be evaluated fails certification.**

        The alternative — treating `INCONCLUSIVE` as a pass — would let the suite certify exactly
        the parts of the architecture it was unable to inspect."""
        blocking_failed = False
        advisory_failed = False
        for result in results:
            control = CONTROLS_BY_ID.get(result.control_id)
            if control is None:
                raise ValueError(
                    f"result cites unknown control {result.control_id!r}; the catalogue is the "
                    "authority on what is certified"
                )
            if result.passed:
                continue
            if control.severity is ControlSeverity.BLOCKING:
                blocking_failed = True
            else:
                advisory_failed = True
        if blocking_failed:
            return CertificationVerdict.NOT_CERTIFIED
        if advisory_failed:
            return CertificationVerdict.CERTIFIED_WITH_ADVISORIES
        return CertificationVerdict.CERTIFIED

    @staticmethod
    def guard_complete(results: Sequence[ControlResult]) -> None:
        """Every declared control must produce a result.

        Without this, deleting a control and its result would silently shrink what "certified"
        means — the cheapest possible way to make a certification suite green."""
        produced = {r.control_id for r in results}
        missing = sorted(set(CONTROLS_BY_ID) - produced)
        if missing:
            raise ValueError(
                f"certification is incomplete: {missing} produced no result. "
                "A control that did not run has not passed."
            )
        unknown = sorted(produced - set(CONTROLS_BY_ID))
        if unknown:
            raise ValueError(f"certification produced results for unknown controls: {unknown}")

    @classmethod
    def report(cls, results: Sequence[ControlResult]) -> CertificationReport:
        cls.guard_complete(results)
        ordered = tuple(sorted(results, key=lambda r: r.control_id))
        return CertificationReport(
            verdict=cls.verdict(ordered),
            results=ordered,
            report_digest=certification_digest(ordered),
        )


def certification_digest(results: Sequence[ControlResult]) -> str:
    """A content digest over **what was found**, using M9.21's single canonicalizer.

    Deliberately excludes the clock, the host, the process and the run order: the same repository
    certifies to the same digest on any machine, forever, which is what makes a certification
    reproducible rather than an event that happened once."""
    return canonical_digest(
        [
            {
                "control": r.control_id,
                "outcome": r.outcome.value,
                "violations": sorted(r.violations),
                "examined": r.examined,
            }
            for r in sorted(results, key=lambda r: r.control_id)
        ]
    )


@dataclass(frozen=True, slots=True)
class DeclaredLimitation:
    """A limitation Epic 9 carries into the freeze, with the milestone that owns closing it.

    Recorded as data so the freeze cannot silently inherit debt nobody named. `owner` is never
    empty: a limitation with no owner is a limitation nobody will ever close."""

    id: str
    milestone: str
    owner: str
    statement: str

    def __post_init__(self) -> None:
        if not self.owner:
            raise ValueError(f"limitation {self.id!r} names no owning milestone")


#: What Epic 9 knows it does not do, and who would have to fix it. Asserted non-empty and
#: fully-owned by the certification suite: an epic that claims zero limitations at freeze is an
#: epic that stopped looking.
DECLARED_LIMITATIONS: tuple[DeclaredLimitation, ...] = (
    DeclaredLimitation(
        id="LIM-1",
        milestone="M9.23",
        owner="M9.15",
        statement="Tax publishes no period-scoped assessment read, so a consolidation requiring "
        "tax resolves UNAVAILABLE rather than a figure.",
    ),
    DeclaredLimitation(
        id="LIM-2",
        milestone="M9.23",
        owner="M9.16",
        statement="Payment publishes no period-scoped intent read.",
    ),
    DeclaredLimitation(
        id="LIM-3",
        milestone="M9.23",
        owner="M9.18",
        statement="Revenue recognition publishes no period-scoped read.",
    ),
    DeclaredLimitation(
        id="LIM-4",
        milestone="M9.23",
        owner="M9.8",
        statement="No paginated account read exists, so organization-wide scope resolution is "
        "capped at MAX_SCOPE_MEMBERS rather than streamed.",
    ),
    DeclaredLimitation(
        id="LIM-5",
        milestone="M9.23",
        owner="M9.8",
        statement="No reverse payer index exists, so PAYER_GROUP resolution reads the "
        "organization's accounts rather than an indexed subtree.",
    ),
    DeclaredLimitation(
        id="LIM-6",
        milestone="M9.22",
        owner="M9.13/M9.14",
        statement="No organization-wide period read exists, so a consolidated regulatory filing "
        "must be assembled from an M9.23 scope rather than read directly.",
    ),
    DeclaredLimitation(
        id="LIM-8",
        milestone="M9.24",
        owner="M9.5/M9.6/M9.9/M9.10/M9.11",
        statement="29 Pydantic model names collide across customer schema modules, so FastAPI "
        "emits module-qualified OpenAPI schema names. The contract is unambiguous but the "
        "generated document reads badly; closing it is a rename in the owning schema modules.",
    ),
    DeclaredLimitation(
        id="LIM-9",
        milestone="M9.24",
        owner="M9.8/M9.7",
        statement="Two Studio pages rescale integer minor units by a hardcoded 100. Correct for "
        "all eight supported currencies (every one has exponent 2) and wrong the day a "
        "0-exponent (JPY) or 3-exponent (KWD) currency is added — which must therefore be "
        "preceded by a Studio formatting fix.",
    ),
    DeclaredLimitation(
        id="LIM-7",
        milestone="M9.24",
        owner="M9.24",
        statement="Certification is self-administered: the same author implemented the "
        "architecture and the suite that certifies it.",
    ),
)


__all__ = [
    "CERTIFICATION_CONTROLS",
    "CERTIFICATION_VERSION",
    "CONTROLS_BY_ID",
    "DECLARED_LIMITATIONS",
    "PASSING_OUTCOMES",
    "CertificationControl",
    "CertificationReport",
    "CertificationService",
    "CertificationVerdict",
    "ControlFamily",
    "ControlOutcome",
    "ControlResult",
    "ControlSeverity",
    "DeclaredLimitation",
    "certification_digest",
]
