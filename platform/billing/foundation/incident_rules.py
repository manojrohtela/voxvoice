"""Incident correlation rules — the anchor resolver, the lifecycle and the guards (M9.20).

Pure. No clock, no database, no provider, no HTTP, no scheduler thread, no transport. Every rule
takes the instant it reasons about as an argument, which is what makes a correlation decided in
March reproducible in April by replaying the same evidence.

## Correlation is extraction, not inference

`CorrelationService.anchor_for` walks `ANCHOR_PRECEDENCE` and returns the **first resolvable**
anchor. Each resolver reads identifiers the *owning* domain already made immutable and returns
`None` when they are absent — it never falls back to something weaker within a level, never guesses,
and never consults an amount, an instant, an account, a document number or a description.

Two findings correlate when their highest resolvable anchors are **equal in both kind and value**.
Different levels do not correlate even when they might be about the same thing, which
under-correlates rather than over-correlates. That asymmetry is deliberate: an incident that should
have been one and is two is a reporting inconvenience, while an incident that should have been two
and is one has merged two unrelated financial problems and will close both when somebody fixes one.

## Severity is monotonic, and that is the point

`SeverityService.aggregate` takes the worst floor imposed by any *attached* finding — resolved or
not — and never returns something lower than the incident already carries. An incident whose
critical finding was repaired while another remains open must not slip off a dashboard, and the only
way to guarantee that is to make the decrease structurally impossible rather than to remember not to
do it.

## Resolution requires fresh evidence

`ResolutionGuardService.guard_verified` refuses `RESOLVE` unless a `VerificationOutcome` says every
attached finding is settled *and nothing was unreadable*. A finding that could not be re-read is not
evidence of agreement — treating "I could not check" as "it is fine" is how an unrepaired incident
gets closed — and a repair *request* is not evidence either, which is why the FSM has no
`REPAIR_IN_PROGRESS → RESOLVED` edge for this guard to defend.

## What no rule here may do

Reconcile anything (M9.19 owns that). Post, mutate or read a ledger balance. Calculate a price, a
rate, a tax, an invoice total, a recognized amount or a financial impact. Move money. Change a
dunning stage. Write to any domain, including M9.19's. Or invent a root cause: where the evidence
does not establish one, the answer is `RootCause.UNKNOWN`.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, replace

from core.fsm import StateMachineDefinition

from .commercial_incidents import (
    ANCHOR_PRECEDENCE,
    EDGE_BY_ANCHOR,
    IMPACT_BY_ROOT_CAUSE,
    INCIDENT_LIFECYCLE,
    ROOT_CAUSE_BY_DIVERGENCE,
    SEVERITY_FLOOR,
    SEVERITY_ORDER,
    TERMINAL_INCIDENT_STATES,
    AnchorKind,
    CorrelationAnchor,
    EdgeKind,
    EvidenceEdge,
    EvidenceGraph,
    EvidenceNode,
    FindingLink,
    ImpactClass,
    IncidentEvent,
    IncidentExplanation,
    IncidentInspection,
    IncidentSearchQuery,
    IncidentSearchResult,
    IncidentSeverity,
    IncidentStatus,
    IncidentTrigger,
    IntegrityIncident,
    RootCause,
    VerificationOutcome,
    derive_correlation_key,
    worst_severity,
)
from .commercial_reconciliation import (
    UNRESOLVED_FINDING_STATES,
    DivergenceKind,
    FactRef,
    IntegrityFinding,
    RelationshipKind,
)
from .diagnostics import BillingDiagnosticCode, BillingDiagnostics, Diagnostic, Severity
from .errors import BillingConflict, InvalidBillingTransition


class IncidentStale(BillingConflict):
    """A worker tried to transition an incident from a version that is no longer current.

    Raised by persistence when the compare-and-set finds no row, and declared **here, in Foundation,
    beside the domain it protects** — because the Application must react to it and may not import
    persistence. Same placement and same reason as `RefundSequenceConflict` (M9.16-R1), `CaseStale`
    (M9.17), `ScheduleStale` (M9.18) and `FindingStale` (M9.19).

    The scenario: worker A reads an incident as `OPEN` version 3, worker B acknowledges it to
    version 4, and worker A resumes and tries to write. The FSM already makes some of those
    transitions illegal; this makes *losing the race* visible rather than silently overwriting."""


class CorrelationRefused(BillingConflict):
    """A correlation or incident action was asked for that this engine must not perform.

    Raised for boundary violations rather than recorded as an incident fact, because an incident is
    an observation about the *books* and these are refusals about the *request*: attaching a finding
    to a settled incident, correlating across organizations, resolving without verification, or
    merging an incident into itself."""


# ── correlation ─────────────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class FindingEvidence:
    """Everything M9.20 is allowed to correlate on, assembled by the Application from **immutable**
    facts the owning domains already published.

    Deliberately not the finding itself: a finding carries an amount and a detection time, and
    passing it straight into the resolver would put both within reach of a future correlation rule.
    This type carries **no amount and no timestamp**, so a rule that wanted one could not compile.

    `document_id` / `document_revision` are traced by the Application through M9.14, M9.16 and M9.18
    facts — the schedule that names its document, the intent that names its obligation. Traced, not
    guessed: when the trace fails the field is empty and the anchor falls to the next level."""

    finding_id: str
    org_id: str
    relationship: RelationshipKind
    divergence: DivergenceKind
    subject_key: str
    source: FactRef | None = None
    counterpart: FactRef | None = None
    document_id: str = ""
    document_revision: str = ""  # the issued snapshot digest — immutable content, not a number
    ledger_source: str = ""  # the PostingSource the ledger entry names
    ledger_source_id: str = ""  # what that entry says caused it


class CorrelationService:
    """**The only place a correlation identity is decided.**

    Table-driven by `ANCHOR_PRECEDENCE`: adding a seventh way to correlate is a resolver plus a row,
    never a branch bolted onto an existing one."""

    def anchor_for(self, evidence: FindingEvidence) -> CorrelationAnchor:
        """The first resolvable anchor, most specific first.

        Each resolver returns `None` rather than something weaker, so precedence is honoured
        strictly and a finding never silently correlates on a broader identity than the one it
        actually carries."""
        for kind in ANCHOR_PRECEDENCE:
            resolved = self._resolve(kind, evidence)
            if resolved is not None:
                return resolved
        return CorrelationAnchor(
            kind=AnchorKind.NONE,
            value="",
            detail="no immutable identity links this finding to any other",
        )

    def _resolve(
        self, kind: AnchorKind, evidence: FindingEvidence
    ) -> CorrelationAnchor | None:
        if kind is AnchorKind.LEDGER_SOURCE:
            return self._ledger_source(evidence)
        if kind is AnchorKind.DOCUMENT:
            return self._document(evidence)
        if kind is AnchorKind.FINANCIAL_FACT:
            return self._financial_fact(evidence)
        return None  # pragma: no cover — ANCHOR_PRECEDENCE holds exactly these three

    @staticmethod
    def _ledger_source(evidence: FindingEvidence) -> CorrelationAnchor | None:
        """**The strongest evidence that exists**: the cause the Ledger itself names.

        A journal entry records `(source, source_id)` — *why this posting happened*. Two findings
        naming the same pair are not merely similar; they are about the same financial fact, on the
        Ledger's own authority rather than on ours."""
        if not (evidence.ledger_source and evidence.ledger_source_id):
            return None
        return CorrelationAnchor(
            kind=AnchorKind.LEDGER_SOURCE,
            value=f"{evidence.ledger_source}:{evidence.ledger_source_id}",
            detail="the cause the ledger entry names",
        )

    @staticmethod
    def _document(evidence: FindingEvidence) -> CorrelationAnchor | None:
        """The immutable document identity, **including its issued revision digest**.

        The revision matters: a document revised after a divergence is a *different* immutable
        artifact, and correlating across revisions would attach a March finding to an incident about
        the April version of the same invoice."""
        if not evidence.document_id:
            return None
        revision = evidence.document_revision or "unrevised"
        return CorrelationAnchor(
            kind=AnchorKind.DOCUMENT,
            value=f"{evidence.document_id}@{revision}",
            detail="the immutable document revision both findings trace to",
        )

    @staticmethod
    def _financial_fact(evidence: FindingEvidence) -> CorrelationAnchor | None:
        """The source fact's own `(kind, identity)` — a payment attempt, a recognition entry, a
        tax assessment. Immutable, minted by the domain that owns it."""
        source = evidence.source
        if source is None or not source.identity or not source.kind:
            return None
        return CorrelationAnchor(
            kind=AnchorKind.FINANCIAL_FACT,
            value=f"{source.kind}:{source.identity}",
            detail="the immutable source fact both findings name",
        )

    def key_for(self, evidence: FindingEvidence) -> tuple[CorrelationAnchor, str]:
        """The anchor and its org-scoped correlation key.

        An unresolvable anchor still produces a key — derived from the **finding's own identity** —
        so the incident is a singleton rather than a bucket every unanchored finding falls into.
        Sharing a key on the basis of having no evidence would be the worst correlation of all."""
        anchor = self.anchor_for(evidence)
        value = anchor.value if anchor.is_resolvable else f"finding:{evidence.finding_id}"
        return anchor, derive_correlation_key(evidence.org_id, anchor.kind.value, value)

    @staticmethod
    def edge_for(anchor: CorrelationAnchor) -> EdgeKind:
        """Which edge an anchor justifies. **An edge cannot exist without the anchor that proved
        it** — there is no path here that builds one from anything weaker."""
        return EDGE_BY_ANCHOR.get(anchor.kind, EdgeKind.SAME_FINANCIAL_CAUSE)

    def correlates(self, first: FindingEvidence, second: FindingEvidence) -> bool:
        """Do two findings belong to the same incident?

        Equal org, equal anchor kind, equal anchor value — and an unresolvable anchor correlates
        with nothing, including another unresolvable one."""
        if first.org_id != second.org_id:
            return False  # cross-org correlation is impossible by construction, not by policy
        a, b = self.anchor_for(first), self.anchor_for(second)
        if not (a.is_resolvable and b.is_resolvable):
            return False
        return a.kind is b.kind and a.value == b.value


class RootCauseService:
    """Classification by lookup over recorded evidence. **Never a guess.**"""

    @staticmethod
    def classify(divergences: Sequence[DivergenceKind]) -> RootCause:
        """The cause of an incident from the divergences attached to it.

        When the attached findings disagree about the cause, the answer is `UNKNOWN` rather than
        the first, the commonest or the worst. An incident whose findings point at two different
        causes has not been explained, and saying so is the honest output."""
        causes = {ROOT_CAUSE_BY_DIVERGENCE.get(d, RootCause.UNKNOWN) for d in divergences}
        causes.discard(RootCause.UNKNOWN)
        if len(causes) == 1:
            return next(iter(causes))
        return RootCause.UNKNOWN

    @staticmethod
    def impact_of(cause: RootCause) -> ImpactClass:
        """**Classified, never calculated.** There is no subtraction anywhere on this path."""
        return IMPACT_BY_ROOT_CAUSE.get(cause, ImpactClass.POTENTIAL_FINANCIAL_IMPACT)


class SeverityService:
    """Incident severity aggregation. **Monotonic by construction.**"""

    @staticmethod
    def aggregate(
        current: IncidentSeverity, links: Sequence[FindingLink]
    ) -> IncidentSeverity:
        """The worst floor any *attached* finding imposes, and never below what is already there.

        Two rules, both load-bearing:

        * **resolved findings still count.** A critical divergence that has been repaired still
          means a critical thing happened, and the incident records that it happened;
        * **the result never decreases.** `max(current, computed)` rather than `computed`, so an
          incident cannot slip off a dashboard because one of its three findings was fixed. This is
          the mandate's rule — *an incident must not become less severe merely because one finding
          is resolved while another remains unresolved* — made impossible rather than remembered."""
        floors = tuple(
            SEVERITY_FLOOR[link.severity] for link in links if link.is_attached
        )
        computed = worst_severity(floors)
        return worst_severity((current, computed))


# ── lifecycle ───────────────────────────────────────────────────────────────────


class IncidentLifecycleService:
    """Applies incident triggers through the shared FSM, and bumps the concurrency token.

    The machine's **refusals are the specification**: `RESOLVED`, `CLOSED` and `MERGED` have no
    outgoing edge, so `allowed()` returns an empty tuple for all three and every attempt to move
    them raises."""

    def __init__(self, definition: StateMachineDefinition = INCIDENT_LIFECYCLE) -> None:
        self._definition = definition

    def can(self, current: IncidentStatus, trigger: IncidentTrigger) -> bool:
        return self._definition.get(current, trigger) is not None

    def next_state(
        self, current: IncidentStatus, trigger: IncidentTrigger
    ) -> IncidentStatus:
        edge = self._definition.get(current, trigger)
        if edge is None:
            legal = sorted(t.value for t in self._definition.triggers_from(current))
            raise InvalidBillingTransition(
                f"an integrity incident cannot {trigger.value} from {current.value}; "
                f"legal triggers: {legal or ['(none — this state is terminal)']}"
            )
        return edge.target

    def allowed(self, current: IncidentStatus) -> tuple[IncidentTrigger, ...]:
        return tuple(sorted(self._definition.triggers_from(current), key=lambda t: t.value))

    def apply(
        self,
        incident: IntegrityIncident,
        trigger: IncidentTrigger,
        *,
        at: float,
        actor_id: str = "",
        reason: str = "",
        canonical_id: str = "",
    ) -> IntegrityIncident:
        target = self.next_state(incident.status, trigger)
        changes: dict = {
            "status": target,
            "version": incident.version + 1,
            "last_observed_at": at,
        }
        if target in TERMINAL_INCIDENT_STATES:
            changes["closed_at"] = at
            changes["closed_reason"] = reason or trigger.value
        if target is IncidentStatus.MERGED:
            changes["canonical_id"] = canonical_id
        # `severity` is deliberately absent from every branch. A transition records what happened
        # to the investigation; it never changes how bad the thing that happened was.
        return replace(incident, **changes)


# ── guards ──────────────────────────────────────────────────────────────────────


class IncidentGuardService:
    """The refusals. Each states a boundary the mandate draws, in the layer that can enforce it."""

    @staticmethod
    def guard_scoped(org_id: str) -> None:
        if not org_id:
            raise CorrelationRefused(
                "an integrity incident must be org-scoped; there is no cross-tenant correlation"
            )

    @staticmethod
    def guard_same_org(incident: IntegrityIncident, evidence: FindingEvidence) -> None:
        """**Cross-org correlation, refused at the point of attachment.**

        The correlation key is already org-first, so two organizations cannot compute the same key
        from the same invoice number. This catches the other direction: a caller that assembled
        evidence by hand and reached for an incident belonging to somebody else."""
        if incident.org_id != evidence.org_id:
            raise CorrelationRefused(
                f"finding {evidence.finding_id!r} belongs to org {evidence.org_id!r} and cannot be "
                f"correlated into an incident owned by {incident.org_id!r}"
            )

    @staticmethod
    def guard_attachable(incident: IntegrityIncident) -> None:
        """A settled incident takes no new findings.

        If a genuinely new divergence appears after resolution it belongs to a **new** incident —
        the next run observes it, correlation opens one, and the resolved incident stays exactly as
        it was. Reopening would rewrite a conclusion somebody signed."""
        if incident.is_terminal:
            raise CorrelationRefused(
                f"integrity incident {incident.id!r} is {incident.status.value}; a settled "
                "incident never takes new findings — a recurrence opens a new incident"
            )

    @staticmethod
    def guard_transitionable(incident: IntegrityIncident) -> None:
        if incident.is_terminal:
            raise BillingConflict(
                f"integrity incident {incident.id!r} is {incident.status.value}; a settled "
                "incident is never reopened, and its history is never rewritten"
            )

    @staticmethod
    def guard_mergeable(canonical: IntegrityIncident, merged: IntegrityIncident) -> None:
        """A merge is a new fact about two incidents that must actually be two."""
        if canonical.id == merged.id:
            raise CorrelationRefused("an incident cannot be merged into itself")
        if canonical.org_id != merged.org_id:
            raise CorrelationRefused("incidents from different organizations cannot be merged")
        if merged.is_terminal:
            raise CorrelationRefused(
                f"incident {merged.id!r} is {merged.status.value} and cannot be merged; merging a "
                "settled incident would rewrite a conclusion"
            )
        if canonical.is_terminal:
            raise CorrelationRefused(
                f"incident {canonical.id!r} is {canonical.status.value} and cannot become canonical"
            )


class ResolutionGuardService:
    """**Resolution requires fresh evidence.** ADR-0023 PY5, at incident altitude."""

    @staticmethod
    def guard_verified(
        incident: IntegrityIncident, outcome: VerificationOutcome
    ) -> None:
        """Refuse `RESOLVE` unless verification actually showed the books agree.

        Three refusals, each a way "it's fine now" is usually wrong:

        * **nothing was checked** — an incident with no attached findings has not been
          verified, it has been skipped;
        * **something is still divergent** — the obvious one, and the reason this guard exists;
        * **something was unreadable** — the subtle one. A finding that could not be re-read is not
          evidence of agreement, and treating "I could not check" as "it is fine" is how an
          unrepaired incident gets closed. Partial resolution must never masquerade as complete."""
        if outcome.incident_id != incident.id:  # pragma: no cover — the caller passes its own
            raise CorrelationRefused("verification evidence belongs to a different incident")
        if outcome.checked == 0:
            raise CorrelationRefused(
                f"incident {incident.id!r} has nothing to verify; an incident with no attached "
                "findings has not been checked, it has been skipped"
            )
        if outcome.still_divergent:
            raise CorrelationRefused(
                f"incident {incident.id!r} cannot be resolved: {outcome.still_divergent} of "
                f"{outcome.checked} finding(s) still report a divergence"
            )
        if outcome.unreadable:
            raise CorrelationRefused(
                f"incident {incident.id!r} cannot be resolved: {outcome.unreadable} finding(s) "
                "could not be re-read, and an unreadable finding is not evidence of agreement"
            )

    @staticmethod
    def verify(
        incident: IntegrityIncident,
        links: Sequence[FindingLink],
        findings: dict[str, IntegrityFinding],
    ) -> VerificationOutcome:
        """Re-read every attached finding and report what the books say **now**.

        Reads the *current* finding rows rather than the copies stored at attachment, because the
        stored copy is what the divergence looked like when it was found — comparing against it
        would make every verification trivially succeed."""
        checked = divergent = resolved = unreadable = 0
        detail: list[str] = []
        for link in links:
            if not link.is_attached:
                continue
            checked += 1
            finding = findings.get(link.finding_id)
            if finding is None:
                unreadable += 1
                detail.append(f"{link.finding_id}: could not be re-read")
                continue
            if finding.state in UNRESOLVED_FINDING_STATES:
                divergent += 1
                detail.append(f"{link.finding_id}: still {finding.state.value}")
            else:
                resolved += 1
        return VerificationOutcome(
            incident_id=incident.id,
            checked=checked,
            still_divergent=divergent,
            resolved_findings=resolved,
            unreadable=unreadable,
            detail=tuple(detail),
        )


# ── graph, explanation, inspection, search ──────────────────────────────────────


class EvidenceGraphService:
    """Builds the incident's evidence graph. **Every edge carries the anchor that proved it.**"""

    def build(
        self,
        incident: IntegrityIncident,
        links: Sequence[FindingLink],
        repairs: Sequence[object] = (),
        resolutions: Sequence[str] = (),
    ) -> EvidenceGraph:
        nodes: list[EvidenceNode] = [
            EvidenceNode(
                id=incident.id,
                kind="incident",
                label=incident.correlation_key,
                detail=f"{incident.anchor_kind.value}={incident.anchor_value}",
            )
        ]
        edges: list[EvidenceEdge] = []
        for link in links:
            nodes.append(
                EvidenceNode(
                    id=link.finding_id,
                    kind="finding",
                    label=f"{link.relationship.value}/{link.divergence.value}",
                    detail=link.subject_key,
                )
            )
            edges.append(
                EvidenceEdge(
                    source_id=incident.id,
                    target_id=link.finding_id,
                    kind=link.edge,
                    anchor=link.anchor_kind,
                    evidence=link.anchor_value,
                )
            )
        for repair in repairs:
            repair_id = getattr(repair, "id", "")
            if not repair_id:  # pragma: no cover — every stored repair has an id
                continue
            nodes.append(
                EvidenceNode(
                    id=repair_id,
                    kind="repair_request",
                    label=str(getattr(repair, "action", "")),
                    detail=str(getattr(getattr(repair, "owner", None), "value", "")),
                )
            )
            edges.append(
                EvidenceEdge(
                    source_id=incident.id,
                    target_id=repair_id,
                    kind=EdgeKind.RESOLVES,
                    anchor=incident.anchor_kind,
                    evidence=incident.anchor_value,
                )
            )
        if incident.canonical_id:
            nodes.append(
                EvidenceNode(
                    id=incident.canonical_id, kind="incident", label="canonical"
                )
            )
            edges.append(
                EvidenceEdge(
                    source_id=incident.canonical_id,
                    target_id=incident.id,
                    kind=EdgeKind.SUPERSEDES,
                    anchor=incident.anchor_kind,
                    evidence=incident.correlation_key,
                )
            )
        for resolution in resolutions:
            nodes.append(EvidenceNode(id=resolution, kind="resolution", label="evidence"))
            edges.append(
                EvidenceEdge(
                    source_id=incident.id,
                    target_id=resolution,
                    kind=EdgeKind.RESOLVES,
                    anchor=incident.anchor_kind,
                    evidence=incident.evidence_digest,
                )
            )
        return EvidenceGraph(incident_id=incident.id, nodes=tuple(nodes), edges=tuple(edges))


class IncidentExplainer:
    """*What happened?* — answered entirely **from stored evidence**.

    Nothing here re-reads a domain, and that is the point: by the time an auditor opens a
    six-month-old incident the payment has settled, the schedule has completed and the period has
    closed. An explanation that had to ask them again would answer a different question."""

    def explain(
        self,
        incident: IntegrityIncident,
        links: Sequence[FindingLink],
        events: Sequence[IncidentEvent],
    ) -> IncidentExplanation:
        narrative = [
            f"correlated on: {incident.anchor_kind.value} = {incident.anchor_value or '(none)'}",
            f"correlation key: {incident.correlation_key}",
            f"findings attached: {len([link for link in links if link.is_attached])}",
            f"root cause: {incident.root_cause.value}",
            f"impact: {incident.impact.value}",
            f"severity: {incident.severity.value}",
        ]
        for event in events:
            line = f"{event.kind.value}"
            if event.actor_id:
                line += f" by {event.actor_id}"
            if event.summary:
                line += f" — {event.summary}"
            narrative.append(line)
        narrative.append(f"status: {incident.status.value}")
        if incident.canonical_id:
            narrative.append(f"superseded by incident {incident.canonical_id}")
        return IncidentExplanation(
            incident_id=incident.id,
            correlation_key=incident.correlation_key,
            anchor_kind=incident.anchor_kind,
            anchor_value=incident.anchor_value,
            status=incident.status,
            severity=incident.severity,
            root_cause=incident.root_cause,
            impact=incident.impact,
            narrative=tuple(narrative),
            findings=tuple(link.finding_id for link in links if link.is_attached),
            relationships=tuple(sorted({r.value for r in incident.relationships})),
            owners=tuple(sorted({o.value for o in incident.owners})),
            first_detected_at=incident.first_detected_at,
            last_observed_at=incident.last_observed_at,
            evidence_digest=incident.evidence_digest,
            canonical_id=incident.canonical_id,
        )


class IncidentInspector:
    """A structural read: counted correctly, scoped, connected, severity monotonic."""

    def inspect(
        self,
        incident: IntegrityIncident,
        links: Sequence[FindingLink],
        events: Sequence[IncidentEvent],
        graph: EvidenceGraph | None = None,
    ) -> IncidentInspection:
        notes: list[str] = []
        attached = [link for link in links if link.is_attached]
        counted = incident.finding_count == len(attached)
        if not counted:
            notes.append(
                f"the incident counts {incident.finding_count} finding(s) and "
                f"{len(attached)} link(s) are attached"
            )
        scoped = bool(incident.org_id) and all(link.org_id == incident.org_id for link in links)
        if not scoped:
            notes.append("a link is attributed to a different org than the incident")
        connected = graph.is_connected if graph is not None else True
        if not connected:
            notes.append("a node in the evidence graph has no edge proving why it belongs")
        floors = tuple(SEVERITY_FLOOR[link.severity] for link in attached)
        monotonic = (
            SEVERITY_ORDER.index(incident.severity) >= SEVERITY_ORDER.index(worst_severity(floors))
            if floors
            else True
        )
        if not monotonic:
            notes.append(
                f"severity {incident.severity.value} is below the floor its findings impose"
            )
        unresolved = incident.unresolved_findings
        return IncidentInspection(
            incident_id=incident.id,
            status=incident.status,
            severity=incident.severity,
            finding_count=incident.finding_count,
            links_recorded=len(links),
            events_recorded=len(events),
            is_counted_correctly=counted,
            is_scoped=scoped,
            is_connected=connected,
            is_severity_monotonic=monotonic,
            unresolved_findings=unresolved,
            notes=tuple(notes),
        )


class IncidentSearchService:
    """Filter, order and **paginate**. Scoped by construction; the caller supplies indexed rows."""

    def search(
        self,
        query: IncidentSearchQuery,
        candidates: Sequence[IntegrityIncident],
        total: int | None = None,
    ) -> IncidentSearchResult:
        """`total` is the database's count when the caller has one.

        Without it the total is derived from the page, which is honest but only correct for the
        first page — so every production read supplies it and a test asserts the query layer
        does."""
        found = [i for i in candidates if i.org_id == query.org_id]
        if query.statuses:
            found = [i for i in found if i.status in query.statuses]
        if query.severities:
            found = [i for i in found if i.severity in query.severities]
        if query.root_causes:
            found = [i for i in found if i.root_cause in query.root_causes]
        if query.impacts:
            found = [i for i in found if i.impact in query.impacts]
        if query.owners:
            found = [i for i in found if set(i.owners) & set(query.owners)]
        if query.account_id:
            found = [i for i in found if i.account_id == query.account_id]
        if query.correlation_key:
            found = [i for i in found if i.correlation_key == query.correlation_key]
        if query.anchor_value:
            needle = query.anchor_value.lower()
            found = [i for i in found if needle in i.anchor_value.lower()]
        limit = max(1, query.limit)
        offset = max(0, query.offset)
        page = found[offset : offset + limit]
        return IncidentSearchResult(
            incidents=tuple(page),
            total=total if total is not None else len(found),
            limit=limit,
            offset=offset,
        )


class IncidentPostureService:
    """Counts, derived on every read. **There is no stored posture and no monetary total.**"""

    def posture(self, org_id: str, incidents: Sequence[IntegrityIncident]):
        from .commercial_incidents import IncidentPosture

        by_status: dict[IncidentStatus, int] = {}
        by_severity: dict[IncidentSeverity, int] = {}
        by_cause: dict[str, int] = {}
        by_impact: dict[str, int] = {}
        by_owner: dict[str, int] = {}
        for incident in incidents:
            by_status[incident.status] = by_status.get(incident.status, 0) + 1
            if incident.is_terminal:
                continue
            by_severity[incident.severity] = by_severity.get(incident.severity, 0) + 1
            by_cause[incident.root_cause.value] = by_cause.get(incident.root_cause.value, 0) + 1
            by_impact[incident.impact.value] = by_impact.get(incident.impact.value, 0) + 1
            for owner in incident.owners:
                by_owner[owner.value] = by_owner.get(owner.value, 0) + 1
        return IncidentPosture(
            org_id=org_id,
            incidents=len(incidents),
            open_incidents=by_status.get(IncidentStatus.OPEN, 0),
            acknowledged=by_status.get(IncidentStatus.ACKNOWLEDGED, 0),
            investigating=by_status.get(IncidentStatus.INVESTIGATING, 0),
            repair_in_progress=by_status.get(IncidentStatus.REPAIR_IN_PROGRESS, 0),
            verifying=by_status.get(IncidentStatus.VERIFYING, 0),
            resolved=by_status.get(IncidentStatus.RESOLVED, 0),
            closed=by_status.get(IncidentStatus.CLOSED, 0),
            merged=by_status.get(IncidentStatus.MERGED, 0),
            critical=by_severity.get(IncidentSeverity.CRITICAL, 0),
            error=by_severity.get(IncidentSeverity.ERROR, 0),
            warning=by_severity.get(IncidentSeverity.WARNING, 0),
            by_root_cause=by_cause,
            by_impact=by_impact,
            by_owner=by_owner,
        )


class IncidentValidator:
    """Structural validation. Errors fail fast at the factory (§A2.6)."""

    def validate_evidence(self, evidence: FindingEvidence) -> BillingDiagnostics:
        found: list[Diagnostic] = []
        if not evidence.org_id:
            found.append(
                Diagnostic(
                    code=BillingDiagnosticCode.SNAPSHOT_INTEGRITY,
                    severity=Severity.ERROR,
                    message="correlation evidence must name an org",
                    path="evidence.org_id",
                )
            )
        if not evidence.finding_id:
            found.append(
                Diagnostic(
                    code=BillingDiagnosticCode.SNAPSHOT_INTEGRITY,
                    severity=Severity.ERROR,
                    message="correlation evidence must name the finding it came from",
                    path="evidence.finding_id",
                )
            )
        return BillingDiagnostics(tuple(found))

    def validate_tables(self) -> BillingDiagnostics:
        """Every divergence classifies, every cause classifies impact, every anchor has an edge.

        Run as a validation rather than only as a test, so a future divergence class added without
        a root cause fails in the factory rather than silently becoming `UNKNOWN`."""
        found: list[Diagnostic] = []
        for divergence in DivergenceKind:
            if divergence not in ROOT_CAUSE_BY_DIVERGENCE:
                found.append(
                    Diagnostic(
                        code=BillingDiagnosticCode.SNAPSHOT_INTEGRITY,
                        severity=Severity.ERROR,
                        message=f"divergence {divergence.value!r} has no root-cause classification",
                        path=f"root_cause.{divergence.value}",
                    )
                )
        for cause in RootCause:
            if cause not in IMPACT_BY_ROOT_CAUSE:
                found.append(
                    Diagnostic(
                        code=BillingDiagnosticCode.SNAPSHOT_INTEGRITY,
                        severity=Severity.ERROR,
                        message=f"root cause {cause.value!r} has no impact classification",
                        path=f"impact.{cause.value}",
                    )
                )
        for anchor in ANCHOR_PRECEDENCE:
            if anchor not in EDGE_BY_ANCHOR:
                found.append(
                    Diagnostic(
                        code=BillingDiagnosticCode.SNAPSHOT_INTEGRITY,
                        severity=Severity.ERROR,
                        message=f"anchor {anchor.value!r} justifies no evidence edge",
                        path=f"edge.{anchor.value}",
                    )
                )
        return BillingDiagnostics(tuple(found))


__all__ = [
    "CorrelationRefused",
    "CorrelationService",
    "EvidenceGraphService",
    "FindingEvidence",
    "IncidentExplainer",
    "IncidentGuardService",
    "IncidentInspector",
    "IncidentLifecycleService",
    "IncidentPostureService",
    "IncidentSearchService",
    "IncidentStale",
    "IncidentValidator",
    "ResolutionGuardService",
    "RootCauseService",
    "SeverityService",
]
