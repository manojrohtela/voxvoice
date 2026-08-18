"""Financial Integrity Correlation & Audit — the **incident history** domain (M9.20).

M9.19 answers *do these two financial facts agree?* — one relationship, one pair, one verdict. It
was honest about what it could not do, and M9.20 exists to close exactly that gap:

> at real volume, one root cause generates findings across several relationships, and nothing
> groups them.

So M9.20 answers a different question:

> **Which reconciliation findings belong to the same financial integrity incident, what is the
> common evidence, what has happened to that incident over time, and can an auditor reconstruct
> the complete chain?**

And it answers it under one constraint that shapes everything else:

> **Correlation is an evidence relationship. It never manufactures a root cause.**

---

## The audit that shaped this vocabulary

The pre-implementation sweep across M9.2–M9.19 found **no existing incident or correlation
aggregate** — the concept is genuinely new. Two near-misses were deliberately *not* reused:

| Existing artifact | Owner | Meaning | M9.20 action |
|---|---|---|---|
| `IntegrityFinding` | M9.19 | one divergence between two facts | **read-only**; never written |
| `IntegrityRun` | M9.19 | one evaluation, keyed by digest | **read-only**; supplies the scope |
| `IntegrityResolution` | M9.19 | evidence one *finding* was fixed | **read-only**; separate |
| `IntegrityActivity` | M9.19 | audit trail of one run | untouched — M9.20 keeps its own |
| `BillingEvent.correlation_id` | M9.2 | **transport trace id** | **not reused** — see below |
| `ProviderReport.correlation_id` | M9.16 | provider request trace | **not reused** |

`correlation_id` already exists and means *"which HTTP request produced this"*. Reusing it for
*"which financial cause produced this"* would give one field two meanings, and the next reader would
join on it. M9.20 declares `CorrelationKey`, which is a hash over immutable financial identifiers
and has nothing to do with a request.

## Correlation must not invent facts

The single largest risk in this milestone is a plausible-sounding root cause nobody can prove. So
the rules are stated as refusals:

* **an anchor is extracted, never inferred.** `ANCHOR_PRECEDENCE` is a declared table over
  identifiers the *owning* domains already made immutable — a ledger source identity, a document
  revision, a payment attempt id, a recognition entry id;
* **amount, timestamp, account, invoice number, description and payment method correlate nothing**,
  alone or together. None of them appears in a correlation key, and a test asserts it;
* when the evidence does not establish a cause, the answer is **`RootCause.UNKNOWN`** rather than a
  guess. There is no `DATABASE_FAILURE`, no `NETWORK_FAILURE`, no `PROVIDER_FAILURE` in the
  vocabulary, because Billing has no evidence for any of them.

## Why precedence, and why it is conservative

Two findings correlate when their **highest-precedence resolvable anchors are equal**. The order is
most-specific-first:

1. `LEDGER_SOURCE` — the cause the Ledger itself names (`source`, `source_id`). The most specific
   evidence that exists: two findings naming the same ledger cause *are* about the same fact;
2. `DOCUMENT` — the immutable document identity, traced through the owning domain's own facts;
3. `FINANCIAL_FACT` — the source fact's own `(kind, identity)`;
4. `NONE` — a singleton incident with `RootCause.UNKNOWN`.

The consequence is deliberate: when two findings resolve at *different* precedence levels they do
**not** correlate. That under-correlates rather than over-correlates, and the asymmetry is the right
one — an incident that should have been one and is two is a reporting inconvenience, while an
incident that should have been two and is one has silently merged two unrelated financial problems
and closed both when somebody fixed one.

## History is never rewritten

Every mutation of *meaning* is an append, not an edit:

* an incident that turns out to duplicate another is **merged by a new `IncidentMerge` fact** that
  names a canonical incident. Both originals stay readable, at their original severity;
* a correlation that turns out to be too broad is corrected by a new `CorrelationCorrection` fact
  carrying the previous key, the corrected key, the reason and the actor. The old observation
  remains part of the trail;
* the finding→incident link is never deleted; it is *detached*, which is itself an event.

Current status is a **projection over the event history**, not the authority. The lifecycle FSM has
no edge out of `RESOLVED` or `CLOSED`, so a settled incident cannot be reopened by a stale worker —
the same structural device M9.19 used, for the same reason.

## What is deliberately absent

No balance, no total, no amount this module computed, and **no monetary arithmetic of any kind**.
Impact is *classified*, never calculated: `expected − actual` is a figure the owning domain owns,
and
computing it here would make correlation a second opinion about money. No price, rate, tax, usage or
recognized figure. No provider, no HTTP, no SDK. No scheduler, no clock, no ambient anything. And no
write path into any other domain, including M9.19's.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from enum import StrEnum

from core.fsm import StateMachineDefinition, define

from .commercial_reconciliation import (
    DivergenceKind,
    FindingSeverity,
    RelationshipKind,
    RepairOwner,
)

# ── identity ────────────────────────────────────────────────────────────────────


def _digest(*parts: str) -> str:
    return hashlib.sha256("\x1f".join(parts).encode("utf-8")).hexdigest()


class AnchorKind(StrEnum):
    """What immutable identity a finding was correlated on.

    Every member names evidence an *owning domain* already made immutable. There is no member for
    an amount, a timestamp, an account, a document number or a description — not because they are
    hard to read, but because correlating on any of them pairs a refund with an unrelated capture
    and reports two problems as one."""

    LEDGER_SOURCE = "ledger_source"  # the cause the Ledger itself names: (source, source_id)
    DOCUMENT = "document"  # the immutable document identity + issued revision
    FINANCIAL_FACT = "financial_fact"  # the source fact's own (kind, identity)
    NONE = "none"  # nothing resolvable — a singleton incident, cause UNKNOWN


#: Most specific first. Two findings correlate only when their **highest resolvable anchor** is
#: equal, so a finding anchored on a ledger cause never correlates with one anchored only on a
#: document — under-correlating rather than over-correlating, which is the safe direction.
ANCHOR_PRECEDENCE: tuple[AnchorKind, ...] = (
    AnchorKind.LEDGER_SOURCE,
    AnchorKind.DOCUMENT,
    AnchorKind.FINANCIAL_FACT,
)


@dataclass(frozen=True, slots=True)
class CorrelationAnchor:
    """One immutable identity a finding can be correlated on.

    `value` is always composed from identifiers the owning domain minted. Nothing here is derived
    from an amount, an instant, or free text."""

    kind: AnchorKind
    value: str
    detail: str = ""

    @property
    def is_resolvable(self) -> bool:
        return self.kind is not AnchorKind.NONE and bool(self.value)


def derive_correlation_key(org_id: str, anchor_kind: str, anchor_value: str) -> str:
    """**The correlation identity.** Org-first, so two organizations sharing an invoice number,
    a payment operation id or a ledger source id can never land on the same key.

    Composed only from `(org, anchor kind, anchor value)` — no timestamp, no status, no balance, no
    `latest()`, no provider text. A volatile component would make the same evidence produce a
    different key on every pass, and correlation would never converge."""
    return "cor_" + _digest(org_id, anchor_kind, anchor_value)[:32]


def derive_incident_id(org_id: str, correlation_key: str) -> str:
    """An incident **is** its correlation key.

    Two workers correlating the same finding compute the same id and one loses a primary-key race,
    rather than both opening an incident about the same cause."""
    return "inc_" + _digest(org_id, correlation_key)[:32]


def derive_link_id(incident_id: str, finding_id: str) -> str:
    """One finding links to one incident once. **The duplicate-attachment defence.**"""
    return "lnk_" + _digest(incident_id, finding_id)[:32]


def derive_incident_event_id(incident_id: str, kind: str, subject: str, sequence: int) -> str:
    """An audit line's identity, derived so a replayed command appends nothing twice."""
    return "iev_" + _digest(incident_id, kind, subject, str(sequence))[:32]


def derive_merge_id(org_id: str, canonical_id: str, merged_id: str) -> str:
    return "img_" + _digest(org_id, canonical_id, merged_id)[:32]


def derive_correlation_correction_id(org_id: str, finding_id: str, previous_key: str, corrected_key:
str) -> str:
    return "icc_" + _digest(org_id, finding_id, previous_key, corrected_key)[:32]


def derive_incident_repair_id(incident_id: str, owner: str, action: str, request_key: str) -> str:
    return "irp_" + _digest(incident_id, owner, action, request_key)[:32]


def derive_incident_evidence_digest(parts: tuple[str, ...]) -> str:
    """A digest over the incident's constituent evidence, order-independent.

    Evidence that two facts were correlated — **never a financial value**, and nothing downstream
    reads an amount out of it."""
    return _digest(*sorted(parts))


# ── classification ──────────────────────────────────────────────────────────────


class RootCause(StrEnum):
    """Why the books disagree, **stated only from evidence the owning domain recorded**.

    Note what is absent: no `DATABASE_FAILURE`, no `NETWORK_FAILURE`, no `PROVIDER_FAILURE`. Billing
    has no evidence for any of them — a missing ledger entry looks identical whether the database
    rejected it, the process died, or nobody ever tried. Naming a cause we cannot prove is worse
    than `UNKNOWN`, because somebody downstream will believe it and stop investigating."""

    SOURCE_FACT_MISSING = "source_fact_missing"
    SOURCE_FACT_DUPLICATED = "source_fact_duplicated"
    AMOUNT_DIVERGENCE = "amount_divergence"
    CURRENCY_DIVERGENCE = "currency_divergence"
    IDENTITY_DIVERGENCE = "identity_divergence"
    PERIOD_DIVERGENCE = "period_divergence"
    STATE_DIVERGENCE = "state_divergence"
    VERSION_DIVERGENCE = "version_divergence"
    TIMING_DIVERGENCE = "timing_divergence"
    UNKNOWN = "unknown"


#: How M9.19's divergence classes project onto a root cause. A **table**, so the classification is
#: derived from recorded evidence by lookup rather than by a chain of `if`s somebody can extend with
#: a guess. Every `DivergenceKind` has a row; a test iterates the enum to prove it.
#:
#: `EVIDENCE_MALFORMED` and `STALE_EVIDENCE` map to `UNKNOWN` deliberately: both mean the evidence
#: could not be read, which is precisely the state in which naming a cause would be invention.
ROOT_CAUSE_BY_DIVERGENCE: dict[DivergenceKind, RootCause] = {
    DivergenceKind.MISSING_COUNTERPART: RootCause.SOURCE_FACT_MISSING,
    DivergenceKind.MISSING_SOURCE: RootCause.SOURCE_FACT_MISSING,
    DivergenceKind.DUPLICATE_SOURCE: RootCause.SOURCE_FACT_DUPLICATED,
    DivergenceKind.DUPLICATE_COUNTERPART: RootCause.SOURCE_FACT_DUPLICATED,
    DivergenceKind.UNEXPECTED_COUNTERPART: RootCause.IDENTITY_DIVERGENCE,
    DivergenceKind.SOURCE_REFERENCE_MISMATCH: RootCause.IDENTITY_DIVERGENCE,
    DivergenceKind.ACCOUNT_MISMATCH: RootCause.IDENTITY_DIVERGENCE,
    DivergenceKind.AMOUNT_MISMATCH: RootCause.AMOUNT_DIVERGENCE,
    DivergenceKind.OVER_ACCOUNTED: RootCause.AMOUNT_DIVERGENCE,
    DivergenceKind.CURRENCY_MISMATCH: RootCause.CURRENCY_DIVERGENCE,
    DivergenceKind.PERIOD_MISMATCH: RootCause.PERIOD_DIVERGENCE,
    DivergenceKind.AMBIGUOUS: RootCause.STATE_DIVERGENCE,
    DivergenceKind.EVIDENCE_MALFORMED: RootCause.UNKNOWN,
    DivergenceKind.STALE_EVIDENCE: RootCause.UNKNOWN,
}


class ImpactClass(StrEnum):
    """What the incident means for the books. **Classified, never calculated.**

    There is no member carrying a figure and no method here that subtracts one amount from another:
    `expected − actual` is arithmetic the owning domain owns, and performing it here would make
    correlation a second opinion about money."""

    NO_FINANCIAL_IMPACT = "no_financial_impact"
    POTENTIAL_FINANCIAL_IMPACT = "potential_financial_impact"
    CONFIRMED_FINANCIAL_IMPACT = "confirmed_financial_impact"
    FINANCIAL_FACT_MISSING = "financial_fact_missing"
    DUPLICATE_FINANCIAL_FACT = "duplicate_financial_fact"
    REGULATORY_EVIDENCE_IMPACT = "regulatory_evidence_impact"


#: How a root cause classifies impact. Derived from the owning domains' own facts by table lookup.
IMPACT_BY_ROOT_CAUSE: dict[RootCause, ImpactClass] = {
    RootCause.SOURCE_FACT_MISSING: ImpactClass.FINANCIAL_FACT_MISSING,
    RootCause.SOURCE_FACT_DUPLICATED: ImpactClass.DUPLICATE_FINANCIAL_FACT,
    RootCause.AMOUNT_DIVERGENCE: ImpactClass.CONFIRMED_FINANCIAL_IMPACT,
    RootCause.CURRENCY_DIVERGENCE: ImpactClass.CONFIRMED_FINANCIAL_IMPACT,
    RootCause.IDENTITY_DIVERGENCE: ImpactClass.POTENTIAL_FINANCIAL_IMPACT,
    RootCause.VERSION_DIVERGENCE: ImpactClass.POTENTIAL_FINANCIAL_IMPACT,
    RootCause.STATE_DIVERGENCE: ImpactClass.POTENTIAL_FINANCIAL_IMPACT,
    RootCause.TIMING_DIVERGENCE: ImpactClass.POTENTIAL_FINANCIAL_IMPACT,
    # A period disposition is M9.13 working as designed, so it carries no financial impact — but it
    # *does* change which filing a figure lands in, which is a regulatory-evidence concern.
    RootCause.PERIOD_DIVERGENCE: ImpactClass.REGULATORY_EVIDENCE_IMPACT,
    RootCause.UNKNOWN: ImpactClass.POTENTIAL_FINANCIAL_IMPACT,
}


class IncidentSeverity(StrEnum):
    """Incident severity, **separate from the severity of any one finding**.

    Separate because they answer different questions: a finding's severity is how bad *that
    disagreement* is, an incident's is how bad the *situation* is. One resolved finding must never
    make the situation look better while another remains open — see `SEVERITY_FLOOR`."""

    INFO = "info"
    WARNING = "warning"
    ERROR = "error"
    CRITICAL = "critical"


#: Finding severity → the incident floor it imposes. The aggregation rule, stated as a table.
#:
#: **The rule that matters**: an incident's severity is the maximum floor imposed by any *attached*
#: finding, resolved or not, and it is **monotonic — it never decreases**. A critical finding that
#: has been repaired still means a critical thing happened; downgrading the incident because one of
#: three findings was fixed is how an unresolved financial fact stops being visible on a dashboard.
SEVERITY_FLOOR: dict[FindingSeverity, IncidentSeverity] = {
    FindingSeverity.INFO: IncidentSeverity.INFO,
    FindingSeverity.WARNING: IncidentSeverity.WARNING,
    FindingSeverity.ERROR: IncidentSeverity.ERROR,
    FindingSeverity.CRITICAL: IncidentSeverity.CRITICAL,
}

#: Ascending order, so "the worst of these" is an index comparison rather than a sort key nobody
#: can check.
SEVERITY_ORDER: tuple[IncidentSeverity, ...] = (
    IncidentSeverity.INFO,
    IncidentSeverity.WARNING,
    IncidentSeverity.ERROR,
    IncidentSeverity.CRITICAL,
)


# ── lifecycle ───────────────────────────────────────────────────────────────────


class IncidentStatus(StrEnum):
    """Where an incident is in its investigation.

    `VERIFYING` is a distinct state rather than a flag, and that separation is load-bearing: it is
    the difference between *a repair was requested* and *the books were re-read and agree*. Without
    it, "we asked somebody to fix it" and "it is fixed" become the same status, which is exactly how
    an unrepaired incident gets reported as closed."""

    OPEN = "open"
    ACKNOWLEDGED = "acknowledged"
    INVESTIGATING = "investigating"
    REPAIR_IN_PROGRESS = "repair_in_progress"
    VERIFYING = "verifying"
    RESOLVED = "resolved"
    CLOSED = "closed"
    MERGED = "merged"  # superseded by a canonical incident; readable forever


class IncidentTrigger(StrEnum):
    ACKNOWLEDGE = "acknowledge"
    INVESTIGATE = "investigate"
    REQUEST_REPAIR = "request_repair"
    VERIFY = "verify"
    RESOLVE = "resolve"
    CLOSE = "close"
    MERGE = "merge"


#: The incident lifecycle. **Read it by what is missing.**
#:
#:   * there is **no edge out of `RESOLVED`, `CLOSED` or `MERGED`**. ADR-0023 PY5 said a divergence
#:     is never auto-resolved; M9.19 made the corollary structural at the finding level and this
#:     does the same at the incident level. A stale worker holding an `OPEN` read cannot reopen a
#:     resolved incident, because the machine has nowhere to put it;
#:   * there is **no `REPAIR_IN_PROGRESS → RESOLVED`**. Requesting a repair is not evidence the
#:     repair happened; the path to `RESOLVED` runs through `VERIFYING`, which re-reads the books.
#:     **Repair never equals resolution**, and here that is an absent edge rather than a comment;
#:   * `VERIFYING → INVESTIGATING` **exists**, because verification can legitimately fail: the
#:     re-read finds the divergence still there, and the incident must go back rather than sit in a
#:     state that implies progress;
#:   * every non-terminal state can `MERGE`, because discovering two incidents share a cause is
#:     independent of how far either investigation had got.
INCIDENT_LIFECYCLE: StateMachineDefinition = define(
    [
        (IncidentStatus.OPEN, IncidentTrigger.ACKNOWLEDGE, IncidentStatus.ACKNOWLEDGED),
        (IncidentStatus.OPEN, IncidentTrigger.CLOSE, IncidentStatus.CLOSED),
        (IncidentStatus.OPEN, IncidentTrigger.MERGE, IncidentStatus.MERGED),
        (
            IncidentStatus.ACKNOWLEDGED,
            IncidentTrigger.INVESTIGATE,
            IncidentStatus.INVESTIGATING,
        ),
        (IncidentStatus.ACKNOWLEDGED, IncidentTrigger.CLOSE, IncidentStatus.CLOSED),
        (IncidentStatus.ACKNOWLEDGED, IncidentTrigger.MERGE, IncidentStatus.MERGED),
        (
            IncidentStatus.INVESTIGATING,
            IncidentTrigger.REQUEST_REPAIR,
            IncidentStatus.REPAIR_IN_PROGRESS,
        ),
        (IncidentStatus.INVESTIGATING, IncidentTrigger.VERIFY, IncidentStatus.VERIFYING),
        (IncidentStatus.INVESTIGATING, IncidentTrigger.CLOSE, IncidentStatus.CLOSED),
        (IncidentStatus.INVESTIGATING, IncidentTrigger.MERGE, IncidentStatus.MERGED),
        (
            IncidentStatus.REPAIR_IN_PROGRESS,
            IncidentTrigger.VERIFY,
            IncidentStatus.VERIFYING,
        ),
        (
            IncidentStatus.REPAIR_IN_PROGRESS,
            IncidentTrigger.REQUEST_REPAIR,
            IncidentStatus.REPAIR_IN_PROGRESS,
        ),
        (IncidentStatus.REPAIR_IN_PROGRESS, IncidentTrigger.CLOSE, IncidentStatus.CLOSED),
        (IncidentStatus.REPAIR_IN_PROGRESS, IncidentTrigger.MERGE, IncidentStatus.MERGED),
        (IncidentStatus.VERIFYING, IncidentTrigger.RESOLVE, IncidentStatus.RESOLVED),
        # Verification can fail. Going back is honest; staying in VERIFYING implies progress.
        (IncidentStatus.VERIFYING, IncidentTrigger.INVESTIGATE, IncidentStatus.INVESTIGATING),
        (IncidentStatus.VERIFYING, IncidentTrigger.CLOSE, IncidentStatus.CLOSED),
        (IncidentStatus.VERIFYING, IncidentTrigger.MERGE, IncidentStatus.MERGED),
    ]
)

#: Incidents no worker should advance again. All three are terminal in `INCIDENT_LIFECYCLE`; this
#: names the concept so a query filters on it rather than re-deriving it.
TERMINAL_INCIDENT_STATES: frozenset[IncidentStatus] = frozenset(
    {IncidentStatus.RESOLVED, IncidentStatus.CLOSED, IncidentStatus.MERGED}
)

#: Incidents still awaiting somebody.
LIVE_INCIDENT_STATES: frozenset[IncidentStatus] = frozenset(
    set(IncidentStatus) - TERMINAL_INCIDENT_STATES
)


# ── the evidence graph ──────────────────────────────────────────────────────────


class EdgeKind(StrEnum):
    """Why two nodes in the evidence graph are connected.

    Every member requires **deterministic evidence**. There is no `RELATED` member: a link created
    "for UI grouping" is a claim about the books that nobody can check, and it would be
    indistinguishable on screen from one that was proved."""

    SAME_SOURCE = "same_source"  # both name the same ledger source identity
    SAME_DOCUMENT = "same_document"  # both trace to the same immutable document revision
    SAME_TRANSACTION = "same_transaction"  # both name the same payment operation identity
    SAME_FINANCIAL_CAUSE = "same_financial_cause"  # both name the same source fact
    SUPERSEDES = "supersedes"  # a merge: canonical over merged
    RESOLVES = "resolves"  # resolution evidence over an incident


#: Which edge an anchor kind justifies. A table, so an edge can only exist where the anchor that
#: proved it exists — there is no path that creates an edge from anything weaker.
EDGE_BY_ANCHOR: dict[AnchorKind, EdgeKind] = {
    AnchorKind.LEDGER_SOURCE: EdgeKind.SAME_SOURCE,
    AnchorKind.DOCUMENT: EdgeKind.SAME_DOCUMENT,
    AnchorKind.FINANCIAL_FACT: EdgeKind.SAME_FINANCIAL_CAUSE,
}


@dataclass(frozen=True, slots=True)
class EvidenceNode:
    """One node: a finding, a financial fact, a repair request, or resolution evidence."""

    id: str
    kind: str  # finding | financial_fact | repair_request | resolution | incident
    label: str = ""
    detail: str = ""


@dataclass(frozen=True, slots=True)
class EvidenceEdge:
    """One connection, and the anchor that proved it. An edge with no anchor cannot be built."""

    source_id: str
    target_id: str
    kind: EdgeKind
    anchor: AnchorKind
    evidence: str = ""


@dataclass(frozen=True, slots=True)
class EvidenceGraph:
    """The incident as a graph of evidence rather than a flat list of findings.

    A flat list can say *these five things are wrong*. A graph says *these five things are wrong
    because they all name this one ledger entry*, which is the difference between a queue and an
    explanation."""

    incident_id: str
    nodes: tuple[EvidenceNode, ...] = ()
    edges: tuple[EvidenceEdge, ...] = ()

    @property
    def is_connected(self) -> bool:
        """Every node reachable from the incident node. A disconnected node is a finding attached
        without evidence, which the correlation service refuses to produce."""
        if not self.nodes:
            return True
        linked = {self.incident_id}
        for edge in self.edges:
            linked.add(edge.source_id)
            linked.add(edge.target_id)
        return all(node.id in linked for node in self.nodes)


# ── aggregates ──────────────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class IntegrityIncident:
    """**One financial integrity incident**: a group of reconciliation findings that deterministic
    evidence proves share a cause.

    Carries no authoritative figure. There is no balance, no invoice total, no tax amount, no
    payment amount and no recognized amount on this type — every one of those stays owned by the
    domain that decided it, and this record references rather than copies them.

    `severity` is **monotonic**: it never decreases, because a critical thing having happened is not
    undone by one of its findings being repaired."""

    id: str
    org_id: str
    correlation_key: str
    anchor_kind: AnchorKind
    anchor_value: str
    status: IncidentStatus = IncidentStatus.OPEN
    severity: IncidentSeverity = IncidentSeverity.INFO
    root_cause: RootCause = RootCause.UNKNOWN
    impact: ImpactClass = ImpactClass.POTENTIAL_FINANCIAL_IMPACT
    finding_count: int = 0
    unresolved_findings: int = 0
    relationships: tuple[RelationshipKind, ...] = ()
    owners: tuple[RepairOwner, ...] = ()
    account_id: str = ""
    evidence_digest: str = ""
    first_detected_at: float = 0.0
    last_observed_at: float = 0.0
    canonical_id: str = ""  # set when this incident was merged into another
    version: int = 0
    closed_at: float | None = None
    closed_reason: str = ""

    @property
    def is_terminal(self) -> bool:
        return self.status in TERMINAL_INCIDENT_STATES

    @property
    def is_merged(self) -> bool:
        return self.status is IncidentStatus.MERGED

    @property
    def needs_attention(self) -> bool:
        return not self.is_terminal and self.severity in (
            IncidentSeverity.ERROR,
            IncidentSeverity.CRITICAL,
        )


@dataclass(frozen=True, slots=True)
class FindingLink:
    """One M9.19 finding attached to one incident, **with the anchor that proved it**.

    Append-only in meaning: a link is never deleted. Detaching sets `detached_at` and records a
    `FINDING_DETACHED` event, so "this finding was once believed to belong here" stays in the
    trail — which is what an auditor asks about after a correlation correction."""

    id: str
    org_id: str
    incident_id: str
    finding_id: str
    run_id: str
    relationship: RelationshipKind
    divergence: DivergenceKind
    severity: FindingSeverity
    anchor_kind: AnchorKind
    anchor_value: str
    edge: EdgeKind
    subject_key: str = ""
    attached_at: float = 0.0
    attached_by: str = ""
    detached_at: float | None = None
    detached_reason: str = ""

    @property
    def is_attached(self) -> bool:
        return self.detached_at is None


class IncidentEventKind(StrEnum):
    """The audit vocabulary. Every significant action produces exactly one of these.

    A **rejected** command produces none — a refusal is not a business fact, and emitting one would
    make an audit trail in which nothing happened indistinguishable from one in which something
    did."""

    INCIDENT_CREATED = "incident_created"
    FINDING_ATTACHED = "finding_attached"
    FINDING_DETACHED = "finding_detached"
    CORRELATION_CONFIRMED = "correlation_confirmed"
    CORRELATION_REJECTED = "correlation_rejected"
    ROOT_CAUSE_CLASSIFIED = "root_cause_classified"
    ACKNOWLEDGED = "acknowledged"
    INVESTIGATION_STARTED = "investigation_started"
    REPAIR_REQUESTED = "repair_requested"
    REPAIR_OBSERVED = "repair_observed"
    VERIFICATION_STARTED = "verification_started"
    VERIFIED = "verified"
    VERIFICATION_FAILED = "verification_failed"
    RESOLVED = "resolved"
    CLOSED = "closed"
    INCIDENT_MERGED = "incident_merged"
    CORRELATION_CORRECTED = "correlation_corrected"


@dataclass(frozen=True, slots=True)
class IncidentEvent:
    """One immutable line of the audit trail. **The source of explanation, not the log.**

    An application log is a debugging artifact: it rotates, it is sampled, and nobody can prove it
    was not edited. An auditor asking *when did Billing first observe this, and who acknowledged
    it?* is answered from these rows."""

    id: str
    org_id: str
    incident_id: str
    sequence: int
    kind: IncidentEventKind
    at: float = 0.0
    actor_id: str = ""
    finding_id: str = ""
    subject: str = ""
    summary: str = ""
    detail: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class IncidentMerge:
    """**A merge as a new fact.** Neither original incident is rewritten.

    The merged incident moves to `MERGED` and gains `canonical_id`; its severity, its findings and
    its history stay exactly as they were. An auditor reading it later sees what was believed at the
    time and what superseded it."""

    id: str
    org_id: str
    canonical_id: str
    merged_id: str
    reason: str = ""
    evidence: str = ""
    anchor_kind: AnchorKind = AnchorKind.NONE
    decided_at: float = 0.0
    decided_by: str = ""


@dataclass(frozen=True, slots=True)
class CorrelationCorrection:
    """**A correction as a new fact.** The previous correlation stays in the trail.

    Carries both keys deliberately: an auditor's question is never "what is the correlation?" but
    "what did you believe, when, and on what grounds did you change your mind?"."""

    id: str
    org_id: str
    finding_id: str
    previous_incident_id: str
    previous_key: str
    corrected_incident_id: str
    corrected_key: str
    reason: str = ""
    evidence: str = ""
    corrected_at: float = 0.0
    corrected_by: str = ""


@dataclass(frozen=True, slots=True)
class IncidentRepair:
    """A repair requested of an owning domain, referenced from the incident.

    **M9.20 performs no repair.** This records that one was asked for, of whom, under which
    reference — and `observed` records that the owning domain reported back. None of it is evidence
    the incident is resolved; only verification is."""

    id: str
    org_id: str
    incident_id: str
    owner: RepairOwner
    action: str
    request_key: str
    finding_id: str = ""
    reference: str = ""  # the owning domain's own request/command id
    observed: bool = False
    observed_at: float | None = None
    requested_at: float = 0.0
    requested_by: str = ""
    detail: str = ""


# ── read models ─────────────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class VerificationOutcome:
    """What a verification pass found. **Resolution requires this to say `is_clean`.**"""

    incident_id: str
    checked: int = 0
    still_divergent: int = 0
    resolved_findings: int = 0
    unreadable: int = 0
    detail: tuple[str, ...] = ()

    @property
    def is_clean(self) -> bool:
        """Every attached finding is settled and nothing was unreadable.

        `unreadable` blocking resolution is deliberate: a finding that could not be re-read is not
        evidence of agreement, and treating "I could not check" as "it is fine" is how an
        unrepaired incident gets closed."""
        return self.still_divergent == 0 and self.unreadable == 0 and self.checked > 0


@dataclass(frozen=True, slots=True)
class IncidentExplanation:
    """*What happened, and can an auditor reconstruct the chain?* — from stored evidence only."""

    incident_id: str
    correlation_key: str
    anchor_kind: AnchorKind
    anchor_value: str
    status: IncidentStatus
    severity: IncidentSeverity
    root_cause: RootCause
    impact: ImpactClass
    narrative: tuple[str, ...] = ()
    findings: tuple[str, ...] = ()
    relationships: tuple[str, ...] = ()
    owners: tuple[str, ...] = ()
    first_detected_at: float = 0.0
    last_observed_at: float = 0.0
    evidence_digest: str = ""
    canonical_id: str = ""


@dataclass(frozen=True, slots=True)
class IncidentInspection:
    """A structural read: is the incident counted correctly, scoped, connected, explainable?"""

    incident_id: str
    status: IncidentStatus
    severity: IncidentSeverity
    finding_count: int = 0
    links_recorded: int = 0
    events_recorded: int = 0
    is_counted_correctly: bool = True
    is_scoped: bool = True
    is_connected: bool = True
    is_severity_monotonic: bool = True
    unresolved_findings: int = 0
    notes: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class IncidentSearchQuery:
    """A scoped, bounded, **paginated** search. `org_id` is required; there is no unscoped read."""

    org_id: str
    statuses: tuple[IncidentStatus, ...] = ()
    severities: tuple[IncidentSeverity, ...] = ()
    root_causes: tuple[RootCause, ...] = ()
    impacts: tuple[ImpactClass, ...] = ()
    owners: tuple[RepairOwner, ...] = ()
    account_id: str = ""
    correlation_key: str = ""
    anchor_value: str = ""
    limit: int = 50
    offset: int = 0


@dataclass(frozen=True, slots=True)
class IncidentSearchResult:
    incidents: tuple[IntegrityIncident, ...] = ()
    total: int = 0
    limit: int = 50
    offset: int = 0

    @property
    def has_more(self) -> bool:
        return self.offset + len(self.incidents) < self.total


@dataclass(frozen=True, slots=True)
class IncidentPosture:
    """The org's incident picture — **counts, never money**.

    There is no amount on this type and there never will be, for the reason M9.19 gave about its own
    posture: the moment a monetary total exists here somebody reports it beside a figure the Ledger
    owns, and correlation has become a second set of books."""

    org_id: str
    incidents: int = 0
    open_incidents: int = 0
    acknowledged: int = 0
    investigating: int = 0
    repair_in_progress: int = 0
    verifying: int = 0
    resolved: int = 0
    closed: int = 0
    merged: int = 0
    critical: int = 0
    error: int = 0
    warning: int = 0
    by_root_cause: dict[str, int] = field(default_factory=dict)
    by_impact: dict[str, int] = field(default_factory=dict)
    by_owner: dict[str, int] = field(default_factory=dict)

    @property
    def is_clean(self) -> bool:
        return self.critical == 0 and self.error == 0


def worst_severity(severities: tuple[IncidentSeverity, ...]) -> IncidentSeverity:
    """The worst of a set. An index comparison over a declared order, not a sort key."""
    worst = IncidentSeverity.INFO
    for severity in severities:
        if SEVERITY_ORDER.index(severity) > SEVERITY_ORDER.index(worst):
            worst = severity
    return worst


__all__ = [
    "ANCHOR_PRECEDENCE",
    "EDGE_BY_ANCHOR",
    "IMPACT_BY_ROOT_CAUSE",
    "INCIDENT_LIFECYCLE",
    "LIVE_INCIDENT_STATES",
    "ROOT_CAUSE_BY_DIVERGENCE",
    "SEVERITY_FLOOR",
    "SEVERITY_ORDER",
    "TERMINAL_INCIDENT_STATES",
    "AnchorKind",
    "CorrelationAnchor",
    "CorrelationCorrection",
    "EdgeKind",
    "EvidenceEdge",
    "EvidenceGraph",
    "EvidenceNode",
    "ImpactClass",
    "IncidentEvent",
    "IncidentEventKind",
    "IncidentExplanation",
    "IncidentInspection",
    "IncidentMerge",
    "IncidentPosture",
    "IncidentRepair",
    "IncidentSearchQuery",
    "IncidentSearchResult",
    "IncidentSeverity",
    "IncidentStatus",
    "IncidentTrigger",
    "IntegrityIncident",
    "FindingLink",
    "RootCause",
    "VerificationOutcome",
    "derive_correlation_correction_id",
    "derive_correlation_key",
    "derive_incident_evidence_digest",
    "derive_incident_event_id",
    "derive_incident_id",
    "derive_incident_repair_id",
    "derive_link_id",
    "derive_merge_id",
    "worst_severity",
]
