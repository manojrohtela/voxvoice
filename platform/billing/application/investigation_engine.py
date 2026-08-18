"""Financial Investigation application layer (M9.21) — commands, queries, use cases.

Additive to M9.3/M9.6–M9.20: the buses, Unit of Work, authorization orchestrator, application-event
vocabulary and dispatch discipline are reused exactly as they are. This module adds 13 commands, 14
queries, their use cases and the view DTOs — and nothing else.

## What crosses this layer's boundaries, and in which direction

    Integrity incident (M9.20) ──read──▶ Investigation ──▶ emits billing.investigation.*
    Reconciliation findings (M9.19) ─read──▶     │
    Ledger / Invoice / Payment / Revenue ─read──▶│  (evidence capture only)

**Every arrow is a read.** There is no outward command at all — not even M9.20's repair request,
which M9.21 *references* rather than issues. An investigation records what was asked of an owning
domain and what came back; the asking itself is somebody else's boundary, and the mandate says so
in four separate sections.

An architecture test asserts that no ledger, invoice, tax, payment, recognition, collection,
rating, metering, entitlement, subscription or account repository is reachable from this module,
**and that no write method on M9.20's incident repository is called from here either**. The
incident is read to prove it exists and to snapshot what it said; it is never advanced, classified,
resolved, merged or corrected from this plane.

## Evidence capture is a read plus a copy, never a recomputation

`_EvidenceCollector` is the only place M9.21 touches another domain. It resolves a locator to the
owning domain's own immutable fact and renders it into a canonical body — **projecting fields the
owner already decided, never computing one**. A figure that appears in an evidence body arrived as
an integer the Ledger, the Invoice or the Payment domain wrote; nothing here adds, subtracts,
converts a currency or applies a rate.

When a source cannot be read the collector does not substitute a live object and call it history.
It records an `UNAVAILABLE` capture naming what was sought and why it failed, which is what keeps
"could not retrieve" from quietly becoming "verified".

## Closure is computed, and refusal comes with the list

`AssessClosure` returns the same `ClosureAssessment` the `CloseInvestigation` command consults, so
an operator sees exactly what is outstanding before pressing anything rather than discovering it
through an error. Both call one Foundation service; there is no second copy of the criteria here.

## No scheduler

`OpenInvestigation`, `CaptureEvidence` and `ExportAuditPackage` are invoked from outside. Nothing
here loops, sleeps, polls or spawns; whoever decides *when* to investigate lives outside this
plane, exactly as for M9.20's `/integrity/incidents` and M9.19's `/integrity/findings`.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field, replace

from billing.foundation import (
    AuditPackage,
    BillingError,
    CaptureMode,
    ClosureAssessment,
    ConclusionClassification,
    CustodyChain,
    EvidenceAvailability,
    EvidenceCapture,
    EvidenceChainEdge,
    EvidenceChainNode,
    EvidenceDomain,
    EvidenceGap,
    EvidenceReference,
    EvidenceSourceType,
    HypothesisStatus,
    IntegrityIncident,
    Investigation,
    InvestigationConclusion,
    InvestigationEvent,
    InvestigationEventKind,
    InvestigationEvidenceChain,
    InvestigationObservation,
    InvestigationPosture,
    InvestigationPriority,
    InvestigationQuestion,
    InvestigationSearchQuery,
    InvestigationStatus,
    InvestigationTrigger,
    ObservationKind,
    QuestionKind,
    QuestionStatus,
    RootCause,
    canonical_instant,
)
from billing.foundation import BillingConflict as _Conflict
from billing.foundation import BillingNotFound as _NotFound

from .authorization import AuthorizationOrchestrator
from .authorization import BillingPermission as P
from .commands import _Command
from .events import PREFIX_APP_EVENT, ApplicationEvent, ApplicationEventType
from .queries import _Query
from .unit_of_work import UnitOfWorkFactory

#: How many times a worker re-reads and re-decides after losing a compare-and-set. Bounded: each
#: round either wins or discovers the investigation moved somewhere it must not touch.
_CAS_ATTEMPTS = 5


# ── commands ────────────────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class OpenInvestigation(_Command):
    """Open a case against an **existing** M9.20 incident.

    Idempotent by derived identity: two investigators opening a case on one incident get one
    investigation, not two that could reach different conclusions about the same divergence."""

    incident_id: str = ""
    summary: str = ""
    priority: InvestigationPriority = InvestigationPriority.NORMAL


@dataclass(frozen=True, slots=True)
class TriageInvestigation(_Command):
    investigation_id: str = ""
    priority: InvestigationPriority = InvestigationPriority.NORMAL
    note: str = ""


@dataclass(frozen=True, slots=True)
class StartInvestigation(_Command):
    investigation_id: str = ""
    note: str = ""


@dataclass(frozen=True, slots=True)
class CaptureEvidence(_Command):
    """Capture a source as a **snapshot** or record a **live reference** to it.

    The two are different commands' worth of meaning behind one verb, and the `mode` argument is
    the whole difference: a snapshot asserts what a source said and can back a conclusion, a live
    reference points at it and cannot."""

    investigation_id: str = ""
    domain: EvidenceDomain = EvidenceDomain.INTEGRITY
    source_type: EvidenceSourceType = EvidenceSourceType.INTEGRITY_INCIDENT
    source_id: str = ""
    mode: CaptureMode = CaptureMode.EVIDENCE_SNAPSHOT
    note: str = ""


@dataclass(frozen=True, slots=True)
class CaptureIncidentEvidence(_Command):
    """Capture the whole incident chain in one pass: the incident, every attached finding, and
    every repair the incident recorded. **The command an investigator actually starts with.**"""

    investigation_id: str = ""
    include_findings: bool = True
    include_repairs: bool = True


@dataclass(frozen=True, slots=True)
class MarkEvidenceUnavailable(_Command):
    """Record that a source could not be retrieved. **A new link, never an edit.**"""

    investigation_id: str = ""
    domain: EvidenceDomain = EvidenceDomain.INTEGRITY
    source_type: EvidenceSourceType = EvidenceSourceType.INTEGRITY_INCIDENT
    source_id: str = ""
    reason: str = ""


@dataclass(frozen=True, slots=True)
class RedactEvidence(_Command):
    """Supersede a captured representation with a narrower safe one, keeping the original identity,
    digest, author and timestamp in the chain."""

    investigation_id: str = ""
    evidence_id: str = ""
    reason: str = ""
    keep_fields: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class RecordQuestion(_Command):
    investigation_id: str = ""
    kind: QuestionKind = QuestionKind.OTHER
    text: str = ""
    required: bool = False


@dataclass(frozen=True, slots=True)
class AnswerQuestion(_Command):
    """Answer, or record that the evidence cannot settle it. **Both are outcomes.**"""

    investigation_id: str = ""
    question_id: str = ""
    answer: str = ""
    evidence_ids: tuple[str, ...] = ()
    unanswerable: bool = False


@dataclass(frozen=True, slots=True)
class RecordObservation(_Command):
    """Record an observed fact or a hypothesis. **Separately identified, so one never becomes the
    other by resubmission.**"""

    investigation_id: str = ""
    kind: ObservationKind = ObservationKind.OBSERVED_FACT
    statement: str = ""
    evidence_ids: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class SettleHypothesis(_Command):
    """Support or reject a hypothesis, naming the evidence that settled it."""

    investigation_id: str = ""
    observation_id: str = ""
    supported: bool = False
    evidence_ids: tuple[str, ...] = ()
    reason: str = ""


@dataclass(frozen=True, slots=True)
class RecordConclusion(_Command):
    """State what the investigation found.

    `CONFIRMED` and `CONTRADICTS_INCIDENT_CLASSIFICATION` require an available snapshot behind
    them; `INCONCLUSIVE` requires none and is always available, so no investigator is ever pushed
    into naming a root cause to close a case."""

    investigation_id: str = ""
    classification: ConclusionClassification = ConclusionClassification.INCONCLUSIVE
    statement: str = ""
    evidence_ids: tuple[str, ...] = ()
    evidenced_root_cause: RootCause | None = None
    supersedes: str = ""


@dataclass(frozen=True, slots=True)
class AwaitEvidence(_Command):
    """Park a case that cannot proceed because evidence is not retrievable. **Visibly distinct**
    from one nobody is working on."""

    investigation_id: str = ""
    reason: str = ""


@dataclass(frozen=True, slots=True)
class ResumeInvestigation(_Command):
    investigation_id: str = ""
    note: str = ""


@dataclass(frozen=True, slots=True)
class CloseInvestigation(_Command):
    """Close the investigation. **Not the incident** — M9.20 owns resolution, and a case may
    legitimately close `INCONCLUSIVE` while its incident is still open."""

    investigation_id: str = ""
    reason: str = ""


@dataclass(frozen=True, slots=True)
class ExportAuditPackage(_Command):
    """Produce an immutable, content-addressed package. Exporting unchanged state twice yields the
    same package and writes nothing."""

    investigation_id: str = ""


ALL_INVESTIGATION_COMMANDS: tuple[type, ...] = (
    OpenInvestigation,
    TriageInvestigation,
    StartInvestigation,
    CaptureEvidence,
    CaptureIncidentEvidence,
    MarkEvidenceUnavailable,
    RedactEvidence,
    RecordQuestion,
    AnswerQuestion,
    RecordObservation,
    SettleHypothesis,
    RecordConclusion,
    AwaitEvidence,
    ResumeInvestigation,
    CloseInvestigation,
    ExportAuditPackage,
)


# ── queries ─────────────────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class GetInvestigation(_Query):
    investigation_id: str = ""


@dataclass(frozen=True, slots=True)
class GetInvestigationForIncident(_Query):
    incident_id: str = ""


@dataclass(frozen=True, slots=True)
class ListInvestigations(_Query):
    status: InvestigationStatus | None = None
    priority: InvestigationPriority | None = None
    incident_id: str = ""
    blocked_only: bool = False
    offset: int = 0
    limit: int = 50


@dataclass(frozen=True, slots=True)
class SearchInvestigations(_Query):
    incident_id: str = ""
    status: InvestigationStatus | None = None
    priority: InvestigationPriority | None = None
    opened_by: str = ""
    opened_from: float | None = None
    opened_to: float | None = None
    blocked_only: bool = False
    offset: int = 0
    limit: int = 50


@dataclass(frozen=True, slots=True)
class GetInvestigationTimeline(_Query):
    investigation_id: str = ""
    offset: int = 0
    limit: int = 100


@dataclass(frozen=True, slots=True)
class GetInvestigationEvidence(_Query):
    investigation_id: str = ""
    offset: int = 0
    limit: int = 100


@dataclass(frozen=True, slots=True)
class GetCustodyChains(_Query):
    """Every capture of every source, oldest first. **What changed, and when.**"""

    investigation_id: str = ""


@dataclass(frozen=True, slots=True)
class GetEvidenceChain(_Query):
    """The incident → finding → evidence → repair → verification chain, assembled from
    references. Unreadable nodes are present and marked, never dropped."""

    investigation_id: str = ""


@dataclass(frozen=True, slots=True)
class GetInvestigationQuestions(_Query):
    investigation_id: str = ""


@dataclass(frozen=True, slots=True)
class GetInvestigationObservations(_Query):
    investigation_id: str = ""


@dataclass(frozen=True, slots=True)
class GetInvestigationConclusions(_Query):
    investigation_id: str = ""


@dataclass(frozen=True, slots=True)
class AssessClosure(_Query):
    """What is outstanding, **before** anybody presses close."""

    investigation_id: str = ""


@dataclass(frozen=True, slots=True)
class GetAuditPackage(_Query):
    investigation_id: str = ""
    package_id: str = ""  # empty → the latest


@dataclass(frozen=True, slots=True)
class GetInvestigationPosture(_Query):
    """Fleet-wide counts for an operator queue. Counts only — never a figure."""


ALL_INVESTIGATION_QUERIES: tuple[type, ...] = (
    GetInvestigation,
    GetInvestigationForIncident,
    ListInvestigations,
    SearchInvestigations,
    GetInvestigationTimeline,
    GetInvestigationEvidence,
    GetCustodyChains,
    GetEvidenceChain,
    GetInvestigationQuestions,
    GetInvestigationObservations,
    GetInvestigationConclusions,
    AssessClosure,
    GetAuditPackage,
    GetInvestigationPosture,
)


# ── views ───────────────────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class InvestigationView:
    id: str
    org_id: str
    incident_id: str
    status: str
    priority: str
    incident_severity: str
    summary: str
    opened_at: float
    opened_by: str
    last_activity_at: float
    evidence_count: int
    open_question_count: int
    unavailable_evidence_count: int
    conclusion_id: str
    closed_at: float | None
    closed_by: str
    closed_reason: str
    version: int
    allowed_triggers: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class EvidenceView:
    id: str
    locator: str
    domain: str
    source_type: str
    source_id: str
    revision: str
    mode: str
    availability: str
    content_digest: str
    supports_conclusion: bool
    captured_at: float
    captured_by: str
    previous_evidence_id: str
    unavailable_reason: str
    redaction_reason: str
    sequence: int
    body: dict[str, object] | None = None


@dataclass(frozen=True, slots=True)
class EvidencePageView:
    evidence: tuple[EvidenceView, ...]
    total: int
    offset: int
    limit: int


@dataclass(frozen=True, slots=True)
class CustodyLinkView:
    evidence_id: str
    locator: str
    mode: str
    availability: str
    content_digest: str
    captured_at: float
    captured_by: str
    previous_evidence_id: str
    reason: str


@dataclass(frozen=True, slots=True)
class CustodyChainView:
    locator: str
    changed: bool
    links: tuple[CustodyLinkView, ...]


@dataclass(frozen=True, slots=True)
class QuestionView:
    id: str
    kind: str
    text: str
    status: str
    required: bool
    answer: str
    evidence_ids: tuple[str, ...]
    asked_at: float
    asked_by: str
    answered_at: float | None
    answered_by: str


@dataclass(frozen=True, slots=True)
class ObservationView:
    id: str
    kind: str
    statement: str
    statement_digest: str
    evidence_ids: tuple[str, ...]
    hypothesis_status: str
    recorded_at: float
    recorded_by: str
    settled_at: float | None
    settled_by: str
    settling_evidence_ids: tuple[str, ...]
    settled_reason: str
    sequence: int


@dataclass(frozen=True, slots=True)
class ConclusionView:
    id: str
    classification: str
    statement: str
    evidence_ids: tuple[str, ...]
    incident_root_cause: str
    evidenced_root_cause: str | None
    supersedes: str
    recorded_at: float
    recorded_by: str
    version: int


@dataclass(frozen=True, slots=True)
class TimelineEntryView:
    sequence: int
    kind: str
    at: float
    actor_id: str
    subject: str
    summary: str
    evidence_ids: tuple[str, ...]
    detail: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class TimelinePageView:
    entries: tuple[TimelineEntryView, ...]
    total: int
    offset: int
    limit: int


@dataclass(frozen=True, slots=True)
class EvidenceGapView:
    locator: str
    reason: str
    availability: str
    last_known_available_at: float
    blocks_conclusion: bool
    evidence_id: str


@dataclass(frozen=True, slots=True)
class ClosureAssessmentView:
    investigation_id: str
    may_close: bool
    unresolved_required_questions: tuple[str, ...]
    open_hypotheses: tuple[str, ...]
    blocking_gaps: tuple[EvidenceGapView, ...]
    conclusion_id: str
    conclusion_classification: str | None
    reasons: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class AuditPackageView:
    id: str
    org_id: str
    investigation_id: str
    incident_id: str
    manifest_digest: str
    completeness: str
    evidence_count: int
    observation_count: int
    question_count: int
    conclusion_count: int
    timeline_length: int
    created_at: float
    created_by: str
    gaps: tuple[EvidenceGapView, ...]
    manifest: dict[str, object] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class EvidenceChainNodeView:
    kind: str
    identity: str
    label: str
    available: bool


@dataclass(frozen=True, slots=True)
class EvidenceChainEdgeView:
    source: str
    target: str
    relation: str


@dataclass(frozen=True, slots=True)
class EvidenceChainView:
    investigation_id: str
    incident_id: str
    nodes: tuple[EvidenceChainNodeView, ...]
    edges: tuple[EvidenceChainEdgeView, ...]
    unavailable: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class InvestigationPageView:
    investigations: tuple[InvestigationView, ...]
    total: int
    offset: int
    limit: int
    has_more: bool


@dataclass(frozen=True, slots=True)
class InvestigationPostureView:
    org_id: str
    total: int
    by_status: dict[str, int]
    by_priority: dict[str, int]
    blocked: int
    concluded_awaiting_closure: int
    unavailable_evidence: int
    packages_exported: int
    incomplete_packages: int


# ── view mapping ────────────────────────────────────────────────────────────────


def investigation_view(
    investigation: Investigation, allowed: tuple[InvestigationTrigger, ...]
) -> InvestigationView:
    return InvestigationView(
        id=investigation.id,
        org_id=investigation.org_id,
        incident_id=investigation.incident_id,
        status=investigation.status.value,
        priority=investigation.priority.value,
        incident_severity=investigation.incident_severity.value,
        summary=investigation.summary,
        opened_at=investigation.opened_at,
        opened_by=investigation.opened_by,
        last_activity_at=investigation.last_activity_at,
        evidence_count=investigation.evidence_count,
        open_question_count=investigation.open_question_count,
        unavailable_evidence_count=investigation.unavailable_evidence_count,
        conclusion_id=investigation.conclusion_id,
        closed_at=investigation.closed_at,
        closed_by=investigation.closed_by,
        closed_reason=investigation.closed_reason,
        version=investigation.version,
        allowed_triggers=tuple(t.value for t in allowed),
    )


def evidence_view(capture: EvidenceCapture) -> EvidenceView:
    return EvidenceView(
        id=capture.id,
        locator=capture.locator,
        domain=capture.reference.domain.value,
        source_type=capture.reference.source_type.value,
        source_id=capture.reference.source_id,
        revision=capture.reference.revision,
        mode=capture.mode.value,
        availability=capture.availability.value,
        content_digest=capture.content_digest,
        supports_conclusion=capture.supports_conclusion,
        captured_at=capture.captured_at,
        captured_by=capture.captured_by,
        previous_evidence_id=capture.previous_evidence_id,
        unavailable_reason=capture.unavailable_reason,
        redaction_reason=capture.redaction_reason,
        sequence=capture.sequence,
        body=capture.body,
    )


def question_view(question: InvestigationQuestion) -> QuestionView:
    return QuestionView(
        id=question.id,
        kind=question.kind.value,
        text=question.text,
        status=question.status.value,
        required=question.required,
        answer=question.answer,
        evidence_ids=question.evidence_ids,
        asked_at=question.asked_at,
        asked_by=question.asked_by,
        answered_at=question.answered_at,
        answered_by=question.answered_by,
    )


def observation_view(observation: InvestigationObservation) -> ObservationView:
    return ObservationView(
        id=observation.id,
        kind=observation.kind.value,
        statement=observation.statement,
        statement_digest=observation.statement_digest,
        evidence_ids=observation.evidence_ids,
        hypothesis_status=observation.hypothesis_status.value,
        recorded_at=observation.recorded_at,
        recorded_by=observation.recorded_by,
        settled_at=observation.settled_at,
        settled_by=observation.settled_by,
        settling_evidence_ids=observation.settling_evidence_ids,
        settled_reason=observation.settled_reason,
        sequence=observation.sequence,
    )


def conclusion_view(conclusion: InvestigationConclusion) -> ConclusionView:
    return ConclusionView(
        id=conclusion.id,
        classification=conclusion.classification.value,
        statement=conclusion.statement,
        evidence_ids=conclusion.evidence_ids,
        incident_root_cause=conclusion.incident_root_cause.value,
        evidenced_root_cause=(
            None if conclusion.evidenced_root_cause is None
            else conclusion.evidenced_root_cause.value
        ),
        supersedes=conclusion.supersedes,
        recorded_at=conclusion.recorded_at,
        recorded_by=conclusion.recorded_by,
        version=conclusion.version,
    )


def timeline_view(event: InvestigationEvent) -> TimelineEntryView:
    return TimelineEntryView(
        sequence=event.sequence,
        kind=event.kind.value,
        at=event.at,
        actor_id=event.actor_id,
        subject=event.subject,
        summary=event.summary,
        evidence_ids=event.evidence_ids,
        detail=dict(event.detail),
    )


def gap_view(gap: EvidenceGap) -> EvidenceGapView:
    return EvidenceGapView(
        locator=gap.locator,
        reason=gap.reason,
        availability=gap.availability.value,
        last_known_available_at=gap.last_known_available_at,
        blocks_conclusion=gap.blocks_conclusion,
        evidence_id=gap.evidence_id,
    )


def chain_view(chain: CustodyChain) -> CustodyChainView:
    return CustodyChainView(
        locator=chain.locator,
        changed=chain.changed,
        links=tuple(
            CustodyLinkView(
                evidence_id=link.evidence_id,
                locator=link.locator,
                mode=link.mode.value,
                availability=link.availability.value,
                content_digest=link.content_digest,
                captured_at=link.captured_at,
                captured_by=link.captured_by,
                previous_evidence_id=link.previous_evidence_id,
                reason=link.reason,
            )
            for link in chain.links
        ),
    )


def closure_view(assessment: ClosureAssessment) -> ClosureAssessmentView:
    return ClosureAssessmentView(
        investigation_id=assessment.investigation_id,
        may_close=assessment.may_close,
        unresolved_required_questions=tuple(
            k.value for k in assessment.unresolved_required_questions
        ),
        open_hypotheses=assessment.open_hypotheses,
        blocking_gaps=tuple(gap_view(g) for g in assessment.blocking_gaps),
        conclusion_id=assessment.conclusion_id,
        conclusion_classification=(
            None if assessment.conclusion_classification is None
            else assessment.conclusion_classification.value
        ),
        reasons=assessment.reasons,
    )


def package_view(package: AuditPackage, *, include_manifest: bool = True) -> AuditPackageView:
    return AuditPackageView(
        id=package.id,
        org_id=package.org_id,
        investigation_id=package.investigation_id,
        incident_id=package.incident_id,
        manifest_digest=package.manifest_digest,
        completeness=package.completeness.value,
        evidence_count=package.evidence_count,
        observation_count=package.observation_count,
        question_count=package.question_count,
        conclusion_count=package.conclusion_count,
        timeline_length=package.timeline_length,
        created_at=package.created_at,
        created_by=package.created_by,
        gaps=tuple(gap_view(g) for g in package.gaps),
        manifest=dict(package.manifest) if include_manifest else {},
    )


def evidence_chain_view(chain: InvestigationEvidenceChain) -> EvidenceChainView:
    return EvidenceChainView(
        investigation_id=chain.investigation_id,
        incident_id=chain.incident_id,
        nodes=tuple(
            EvidenceChainNodeView(
                kind=n.kind, identity=n.identity, label=n.label, available=n.available
            )
            for n in chain.nodes
        ),
        edges=tuple(
            EvidenceChainEdgeView(source=e.source, target=e.target, relation=e.relation)
            for e in chain.edges
        ),
        unavailable=tuple(n.identity for n in chain.unavailable),
    )


def posture_view(posture: InvestigationPosture) -> InvestigationPostureView:
    return InvestigationPostureView(
        org_id=posture.org_id,
        total=posture.total,
        by_status=dict(posture.by_status),
        by_priority=dict(posture.by_priority),
        blocked=posture.blocked,
        concluded_awaiting_closure=posture.concluded_awaiting_closure,
        unavailable_evidence=posture.unavailable_evidence,
        packages_exported=posture.packages_exported,
        incomplete_packages=posture.incomplete_packages,
    )


# ── seams ───────────────────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class InvestigationRepositories:
    """The seven investigation seams, injected. Interfaces only — implementations in persistence.

    Seven, and only seven. **Nothing here reaches an incident, reconciliation, ledger, invoice,
    tax, payment, recognition, collection or subscription table for a write.** Those domains are
    read through the context, and every one of those reads is a read."""

    investigations: object
    evidence: object
    questions: object
    observations: object
    conclusions: object
    events: object
    packages: object


@dataclass(frozen=True, slots=True)
class InvestigationServices:
    """The Foundation services this layer coordinates. Composed once, held by reference."""

    lifecycle: object
    guards: object
    evidence_guards: object
    observations: object
    conclusions: object
    custody: object
    comparison: object
    closure: object
    manifest: object
    completeness: object
    canonical: object
    packages: object
    validator: object


class _EvidenceCollector:
    """Resolves a locator to the owning domain's own immutable fact and renders a canonical body.

    **The only place M9.21 touches another domain, and it only ever reads.** Every field in a body
    below is projected verbatim from something an owner already decided. Nothing is summed,
    subtracted, converted or re-derived — an amount that appears here is the integer minor-unit
    value the Ledger, the Invoice or the Payment domain wrote, carried as a plain integer because
    `Money` has no business being reconstructed outside the domain that owns it.

    **The forbidden fields never appear.** No provider payload, no raw response, no token, no
    card number, no CVV, no credential, no API key — M9.16's token-reference-only boundary is
    preserved by projecting the safe identifiers the owning domain publishes rather than copying
    whatever the object happens to hold. A test scans every body-producing method to prove it.
    """

    def __init__(self, context) -> None:
        self._ctx = context

    # ── the readers ─────────────────────────────────────────────────────────────

    def incident(self, org_id: str, incident_id: str) -> IntegrityIncident | None:
        repos = getattr(self._ctx, "incident_engine", None)
        incidents = getattr(repos, "incidents", None)
        if incidents is None:
            return None
        return incidents.get(org_id, incident_id)

    def links_for(self, org_id: str, incident_id: str) -> tuple:
        repos = getattr(self._ctx, "incident_engine", None)
        links = getattr(repos, "links", None)
        if links is None:
            return ()
        return links.for_incident(org_id, incident_id)

    def repairs_for(self, org_id: str, incident_id: str) -> tuple:
        repos = getattr(self._ctx, "incident_engine", None)
        repairs = getattr(repos, "repairs", None)
        if repairs is None:
            return ()
        return repairs.for_incident(org_id, incident_id)

    def finding(self, org_id: str, finding_id: str):
        repos = getattr(self._ctx, "reconciliation_engine", None)
        findings = getattr(repos, "findings", None)
        if findings is None:
            return None
        return findings.get(org_id, finding_id)

    # ── the bodies ──────────────────────────────────────────────────────────────

    def body_for(
        self, org_id: str, domain: EvidenceDomain, source_type: EvidenceSourceType, source_id: str
    ) -> tuple[dict[str, object] | None, str]:
        """`(body, revision)`, or `(None, "")` when the source could not be read.

        `None` is not an error to swallow — the caller records an `UNAVAILABLE` capture, because a
        source that could not be read must stay visible as one."""
        reader = _BODY_READERS.get((domain, source_type))
        if reader is None:
            return None, ""
        try:
            return reader(self, org_id, source_id)
        except BillingError:
            # A refusal from the owning domain is a legitimate "cannot read", not a crash. Anything
            # that is not a BillingError propagates — the M9.16-R1 P-6 correction, applied here.
            return None, ""

    def _incident_body(self, org_id: str, source_id: str):
        incident = self.incident(org_id, source_id)
        if incident is None:
            return None, ""
        return {
            "id": incident.id,
            "correlation_key": incident.correlation_key,
            "anchor_kind": incident.anchor_kind.value,
            "anchor_value": incident.anchor_value,
            "status": incident.status.value,
            "severity": incident.severity.value,
            "root_cause": incident.root_cause.value,
            "impact": incident.impact.value,
            "finding_count": incident.finding_count,
            "unresolved_findings": incident.unresolved_findings,
            "relationships": [r.value for r in incident.relationships],
            "owners": [o.value for o in incident.owners],
            "account_id": incident.account_id,
            "evidence_digest": incident.evidence_digest,
            "first_detected_at": canonical_instant(incident.first_detected_at),
            "last_observed_at": canonical_instant(incident.last_observed_at),
            "canonical_id": incident.canonical_id,
        }, str(incident.version)

    def _finding_body(self, org_id: str, source_id: str):
        finding = self.finding(org_id, source_id)
        if finding is None:
            return None, ""
        return {
            "id": finding.id,
            "run_id": finding.run_id,
            "relationship": finding.relationship.value,
            "subject_key": finding.subject_key,
            "divergence": finding.divergence.value,
            "severity": finding.severity.value,
            "owner": finding.owner.value,
            "state": finding.state.value,
            "verdict": finding.verdict.value,
            "expected": finding.expected,
            "observed": finding.observed,
            "detail": finding.detail,
            "account_id": finding.account_id,
            "detected_at": canonical_instant(finding.detected_at),
            "source": _fact_ref_body(finding.source),
            "counterpart": _fact_ref_body(finding.counterpart),
            "repair_request_id": finding.repair_request_id,
            "resolution_id": finding.resolution_id,
        }, str(finding.version)

    def _repair_body(self, org_id: str, source_id: str):
        repos = getattr(self._ctx, "incident_engine", None)
        repairs = getattr(repos, "repairs", None)
        if repairs is None:
            return None, ""
        repair = repairs.get(org_id, source_id)
        if repair is None:
            return None, ""
        return {
            "id": repair.id,
            "incident_id": repair.incident_id,
            "finding_id": repair.finding_id,
            "owner": repair.owner.value,
            "action": repair.action,
            "request_key": repair.request_key,
            "requested_at": canonical_instant(repair.requested_at),
            "requested_by": repair.requested_by,
            "observed": repair.observed,
            "outcome": repair.outcome,
        }, ""


def _fact_ref_body(ref) -> dict[str, object] | None:
    """One side of an M9.19 comparison, projected. The amount is carried as its **integer minor
    units** — the value the owning domain wrote — because reconstructing `Money` here would be this
    milestone forming an opinion about a figure it does not own."""
    if ref is None:
        return None
    return {
        "identity": ref.identity,
        "kind": ref.kind,
        "amount_minor": None if ref.amount is None else ref.amount.minor,
        "currency": None if ref.amount is None else str(ref.amount.currency),
        "account_id": ref.account_id,
        "period_key": ref.period_key,
        "source": ref.source,
        "source_id": ref.source_id,
        "state": ref.state,
        "digest": ref.digest,
        "at": canonical_instant(ref.at),
        "detail": ref.detail,
    }


#: Which reader renders which source. A table, so "every evidence reference is machine-resolvable"
#: is checkable by iteration; anything absent resolves to `UNAVAILABLE` rather than to a guess.
_BODY_READERS: dict[tuple[EvidenceDomain, EvidenceSourceType], Callable] = {
    (EvidenceDomain.INTEGRITY, EvidenceSourceType.INTEGRITY_INCIDENT): (
        _EvidenceCollector._incident_body
    ),
    (EvidenceDomain.RECONCILIATION, EvidenceSourceType.RECONCILIATION_FINDING): (
        _EvidenceCollector._finding_body
    ),
    (EvidenceDomain.INTEGRITY, EvidenceSourceType.REPAIR_REQUEST): (
        _EvidenceCollector._repair_body
    ),
}


class _InvestigationSupport:
    """Shared lookups for the command and query sides."""

    def __init__(self, *, context, repos: InvestigationRepositories, services) -> None:
        self._ctx = context
        self._repos = repos
        self._svc = services
        self._collector = _EvidenceCollector(context)

    def investigation_or_404(self, org_id: str, investigation_id: str) -> Investigation:
        found = self._repos.investigations.get(org_id, investigation_id)
        if found is None:
            raise _NotFound(f"investigation {investigation_id!r} not found")
        return found

    def _view(self, investigation: Investigation) -> InvestigationView:
        return investigation_view(
            investigation, self._svc.lifecycle.allowed(investigation.status)
        )

    def _captures(self, org_id: str, investigation_id: str) -> tuple[EvidenceCapture, ...]:
        return self._repos.evidence.all_for_investigation(org_id, investigation_id)

    def _supporting(
        self, org_id: str, investigation_id: str, evidence_ids: tuple[str, ...]
    ) -> tuple[EvidenceCapture, ...]:
        """The cited captures, re-read. **Never trusted as supplied by the caller.**

        An id that names nothing is silently dropped rather than raising, and the guard downstream
        then refuses for the honest reason: the statement is not evidence-backed."""
        out = []
        for evidence_id in evidence_ids:
            found = self._repos.evidence.get(org_id, evidence_id)
            if found is not None and found.investigation_id == investigation_id:
                out.append(found)
        return tuple(out)

    def _effective_conclusion(
        self, org_id: str, investigation_id: str
    ) -> InvestigationConclusion | None:
        """The latest conclusion. Superseded ones stay readable; this is the one in force."""
        found = self._repos.conclusions.for_investigation(org_id, investigation_id)
        if not found:
            return None
        return sorted(found, key=lambda c: (c.version, c.recorded_at, c.id))[-1]

    def _assess(self, org_id: str, investigation: Investigation) -> ClosureAssessment:
        return self._svc.closure.assess(
            investigation,
            questions=self._repos.questions.for_investigation(org_id, investigation.id),
            observations=self._repos.observations.for_investigation(org_id, investigation.id),
            captures=self._captures(org_id, investigation.id),
            conclusion=self._effective_conclusion(org_id, investigation.id),
            custody=self._svc.custody,
        )


class InvestigationUseCases(_InvestigationSupport):
    """Investigation orchestration. **Records what was found; changes nothing financial.**"""

    def __init__(
        self,
        *,
        context,
        repos: InvestigationRepositories,
        factory,
        services: InvestigationServices,
        authz: AuthorizationOrchestrator,
        uow_factory: UnitOfWorkFactory,
        ids,
        clock,
    ) -> None:
        super().__init__(context=context, repos=repos, services=services)
        self._factory = factory
        self._authz = authz
        self._uow = uow_factory
        self._ids = ids
        self._clock = clock

    # ── opening ─────────────────────────────────────────────────────────────────

    def open(self, command: OpenInvestigation) -> InvestigationView:
        """**The command this milestone exists for.**

        Refuses without a readable incident: an investigation with no incident is a container for a
        narrative that no observation supports."""
        self._authz.require(command.actor_id, command.org_id, P.BILLING_INVESTIGATION_OPEN)
        self._svc.guards.guard_scoped(command.org_id)
        at = self._clock.now()
        with self._uow.new() as uow:
            incident = self._collector.incident(command.org_id, command.incident_id)
            if incident is None:
                raise _NotFound(
                    f"integrity incident {command.incident_id!r} not found; an investigation is "
                    "always an investigation *of* an incident M9.20 already declared"
                )
            self._svc.guards.guard_incident_investigable(incident, command.org_id)
            built = self._factory.investigation(
                incident,
                org_id=command.org_id,
                summary=command.summary,
                priority=command.priority,
                opened_by=command.actor_id,
                at=at,
            )
            stored, created = self._repos.investigations.create(built)
            if not created:
                return self._view(stored)
            self._append_event(
                stored,
                InvestigationEventKind.INVESTIGATION_OPENED,
                at=at,
                actor_id=command.actor_id,
                subject=incident.id,
                summary=command.summary or f"investigation opened on incident {incident.id}",
                detail={
                    "incident_severity": incident.severity.value,
                    "incident_root_cause": incident.root_cause.value,
                },
            )
            self._factory.emit_opened(stored)
            uow.stage(self._event(command, stored.id, incident_id=incident.id))
            return self._view(stored)

    # ── lifecycle ───────────────────────────────────────────────────────────────

    def triage(self, command: TriageInvestigation) -> InvestigationView:
        return self._transition(
            command,
            command.investigation_id,
            InvestigationTrigger.TRIAGE,
            InvestigationEventKind.INVESTIGATION_TRIAGED,
            note=command.note,
            permission=P.BILLING_INVESTIGATION_OPEN,
            priority=command.priority,
        )

    def start(self, command: StartInvestigation) -> InvestigationView:
        return self._transition(
            command,
            command.investigation_id,
            InvestigationTrigger.START,
            InvestigationEventKind.INVESTIGATION_STARTED,
            note=command.note,
            permission=P.BILLING_INVESTIGATION_CONDUCT,
        )

    def await_evidence(self, command: AwaitEvidence) -> InvestigationView:
        return self._transition(
            command,
            command.investigation_id,
            InvestigationTrigger.AWAIT_EVIDENCE,
            InvestigationEventKind.AWAITING_EVIDENCE,
            note=command.reason,
            permission=P.BILLING_INVESTIGATION_CONDUCT,
        )

    def resume(self, command: ResumeInvestigation) -> InvestigationView:
        return self._transition(
            command,
            command.investigation_id,
            InvestigationTrigger.RESUME,
            InvestigationEventKind.INVESTIGATION_RESUMED,
            note=command.note,
            permission=P.BILLING_INVESTIGATION_CONDUCT,
        )

    def close(self, command: CloseInvestigation) -> InvestigationView:
        """**Closure criteria are computed, not asserted.**

        `ClosureService` is consulted on freshly-read rows inside the transaction, so a question
        answered by somebody else a moment ago counts and one left open blocks. Closing the
        investigation does not resolve the incident — M9.20 owns that, and this command touches no
        incident row."""
        self._authz.require(command.actor_id, command.org_id, P.BILLING_INVESTIGATION_CLOSE)
        at = self._clock.now()
        with self._uow.new() as uow:
            investigation = self.investigation_or_404(command.org_id, command.investigation_id)
            self._svc.guards.guard_active(investigation)
            assessment = self._assess(command.org_id, investigation)
            self._svc.closure.guard_closable(assessment)
            moved = self._svc.lifecycle.apply(
                investigation,
                InvestigationTrigger.CLOSE,
                at=at,
                actor_id=command.actor_id,
                reason=command.reason,
                conclusion_id=assessment.conclusion_id,
            )
            stored = self._repos.investigations.advance(
                moved, expected_version=investigation.version
            )
            self._append_event(
                stored,
                InvestigationEventKind.INVESTIGATION_CLOSED,
                at=at,
                actor_id=command.actor_id,
                subject=assessment.conclusion_id,
                summary=command.reason or "investigation closed",
                detail={
                    "conclusion": assessment.conclusion_classification.value
                    if assessment.conclusion_classification
                    else "",
                },
            )
            self._factory.emit_closed(stored)
            uow.stage(self._event(command, stored.id, status=stored.status.value))
            return self._view(stored)

    def _transition(
        self,
        command,
        investigation_id: str,
        trigger: InvestigationTrigger,
        event_kind: InvestigationEventKind,
        *,
        note: str,
        permission,
        priority: InvestigationPriority | None = None,
    ) -> InvestigationView:
        self._authz.require(command.actor_id, command.org_id, permission)
        at = self._clock.now()
        with self._uow.new() as uow:
            investigation = self.investigation_or_404(command.org_id, investigation_id)
            self._svc.guards.guard_active(investigation)
            moved = self._svc.lifecycle.apply(
                investigation,
                trigger,
                at=at,
                actor_id=command.actor_id,
                reason=note,
                priority=priority,
            )
            stored = self._repos.investigations.advance(
                moved, expected_version=investigation.version
            )
            self._append_event(
                stored, event_kind, at=at, actor_id=command.actor_id, summary=note
            )
            uow.stage(self._event(command, stored.id, status=stored.status.value))
            return self._view(stored)

    # ── evidence ────────────────────────────────────────────────────────────────

    def capture(self, command: CaptureEvidence) -> EvidenceView:
        """Capture one source. A snapshot reads and renders it; a live reference does not."""
        self._authz.require(command.actor_id, command.org_id, P.BILLING_INVESTIGATION_EVIDENCE)
        at = self._clock.now()
        with self._uow.new() as uow:
            investigation = self.investigation_or_404(command.org_id, command.investigation_id)
            self._svc.evidence_guards.guard_capturable(investigation)
            capture = self._capture_one(
                investigation,
                EvidenceReference(
                    domain=command.domain,
                    source_type=command.source_type,
                    source_id=command.source_id,
                ),
                mode=command.mode,
                actor_id=command.actor_id,
                at=at,
            )
            self._recount(investigation)
            uow.stage(self._event(command, capture.id, locator=capture.locator))
            return evidence_view(capture)

    def capture_incident(self, command: CaptureIncidentEvidence) -> tuple[EvidenceView, ...]:
        """Capture the incident, its findings and its repairs in one pass.

        A source that cannot be read produces an `UNAVAILABLE` capture rather than being skipped:
        the difference between "we looked and it was gone" and "we never looked" is exactly what an
        examiner asks about."""
        self._authz.require(command.actor_id, command.org_id, P.BILLING_INVESTIGATION_EVIDENCE)
        at = self._clock.now()
        with self._uow.new() as uow:
            investigation = self.investigation_or_404(command.org_id, command.investigation_id)
            self._svc.evidence_guards.guard_capturable(investigation)
            refs: list[EvidenceReference] = [
                EvidenceReference(
                    domain=EvidenceDomain.INTEGRITY,
                    source_type=EvidenceSourceType.INTEGRITY_INCIDENT,
                    source_id=investigation.incident_id,
                )
            ]
            if command.include_findings:
                for link in self._collector.links_for(command.org_id, investigation.incident_id):
                    refs.append(
                        EvidenceReference(
                            domain=EvidenceDomain.RECONCILIATION,
                            source_type=EvidenceSourceType.RECONCILIATION_FINDING,
                            source_id=link.finding_id,
                        )
                    )
            if command.include_repairs:
                for repair in self._collector.repairs_for(
                    command.org_id, investigation.incident_id
                ):
                    refs.append(
                        EvidenceReference(
                            domain=EvidenceDomain.INTEGRITY,
                            source_type=EvidenceSourceType.REPAIR_REQUEST,
                            source_id=repair.id,
                        )
                    )
            out = tuple(
                self._capture_one(
                    investigation,
                    ref,
                    mode=CaptureMode.EVIDENCE_SNAPSHOT,
                    actor_id=command.actor_id,
                    at=at,
                )
                for ref in refs
            )
            self._recount(investigation)
            uow.stage(self._event(command, investigation.id, captured=str(len(out))))
            return tuple(evidence_view(c) for c in out)

    def _capture_one(
        self,
        investigation: Investigation,
        reference: EvidenceReference,
        *,
        mode: CaptureMode,
        actor_id: str,
        at: float,
    ) -> EvidenceCapture:
        previous = self._repos.evidence.latest_for_source(
            investigation.org_id, investigation.id, reference.locator.split("@")[0]
        )
        sequence = self._repos.evidence.next_sequence(investigation.org_id, investigation.id)
        if mode is CaptureMode.LIVE_REFERENCE:
            built = self._factory.live_reference(
                investigation, reference, captured_by=actor_id, at=at, sequence=sequence
            )
            stored, _created = self._repos.evidence.append(built)
            self._append_event(
                investigation,
                InvestigationEventKind.EVIDENCE_CAPTURED,
                at=at,
                actor_id=actor_id,
                subject=stored.id,
                summary=f"live reference to {stored.locator}",
                evidence_ids=(stored.id,),
            )
            return stored

        body, revision = self._collector.body_for(
            investigation.org_id,
            reference.domain,
            reference.source_type,
            reference.source_id,
        )
        if body is None:
            built = self._factory.unavailable(
                investigation,
                reference,
                reason=f"{reference.locator} could not be read from the owning domain",
                captured_by=actor_id,
                at=at,
                previous=previous,
                sequence=sequence,
            )
            stored, created = self._repos.evidence.append(built)
            if created:
                self._append_event(
                    investigation,
                    InvestigationEventKind.EVIDENCE_MARKED_UNAVAILABLE,
                    at=at,
                    actor_id=actor_id,
                    subject=stored.id,
                    summary=stored.unavailable_reason,
                    evidence_ids=(stored.id,),
                )
                self._factory.emit_evidence_unavailable(stored)
            return stored

        resolved = replace(reference, revision=revision)
        built = self._factory.snapshot(
            investigation,
            resolved,
            body,
            captured_by=actor_id,
            at=at,
            previous=previous,
            sequence=sequence,
        )
        stored, created = self._repos.evidence.append(built)
        if created:
            superseded = previous is not None and previous.content_digest != stored.content_digest
            self._append_event(
                investigation,
                InvestigationEventKind.EVIDENCE_SUPERSEDED
                if superseded
                else InvestigationEventKind.EVIDENCE_CAPTURED,
                at=at,
                actor_id=actor_id,
                subject=stored.id,
                summary=(
                    f"{stored.locator} changed since the previous capture"
                    if superseded
                    else f"snapshot of {stored.locator}"
                ),
                evidence_ids=(stored.id,),
            )
        return stored

    def mark_unavailable(self, command: MarkEvidenceUnavailable) -> EvidenceView:
        self._authz.require(command.actor_id, command.org_id, P.BILLING_INVESTIGATION_EVIDENCE)
        at = self._clock.now()
        with self._uow.new() as uow:
            investigation = self.investigation_or_404(command.org_id, command.investigation_id)
            self._svc.evidence_guards.guard_capturable(investigation)
            reference = EvidenceReference(
                domain=command.domain,
                source_type=command.source_type,
                source_id=command.source_id,
            )
            previous = self._repos.evidence.latest_for_source(
                command.org_id, investigation.id, reference.locator.split("@")[0]
            )
            built = self._factory.unavailable(
                investigation,
                reference,
                reason=command.reason or "evidence could not be retrieved",
                captured_by=command.actor_id,
                at=at,
                previous=previous,
                sequence=self._repos.evidence.next_sequence(command.org_id, investigation.id),
            )
            stored, created = self._repos.evidence.append(built)
            if created:
                self._append_event(
                    investigation,
                    InvestigationEventKind.EVIDENCE_MARKED_UNAVAILABLE,
                    at=at,
                    actor_id=command.actor_id,
                    subject=stored.id,
                    summary=stored.unavailable_reason,
                    evidence_ids=(stored.id,),
                )
                self._factory.emit_evidence_unavailable(stored)
            self._recount(investigation)
            uow.stage(self._event(command, stored.id, locator=stored.locator))
            return evidence_view(stored)

    def redact(self, command: RedactEvidence) -> EvidenceView:
        """Supersede a representation with a narrower one. **The original identity survives.**"""
        self._authz.require(command.actor_id, command.org_id, P.BILLING_INVESTIGATION_CONCLUDE)
        at = self._clock.now()
        with self._uow.new() as uow:
            investigation = self.investigation_or_404(command.org_id, command.investigation_id)
            self._svc.evidence_guards.guard_capturable(investigation)
            original = self._repos.evidence.get(command.org_id, command.evidence_id)
            if original is None or original.investigation_id != investigation.id:
                raise _NotFound(f"evidence {command.evidence_id!r} not found")
            if original.body is None:
                raise _Conflict(
                    f"evidence {original.id!r} is a live reference and holds no representation to "
                    "redact; there is nothing here that could carry over-broad detail"
                )
            keep = command.keep_fields or ("id",)
            replacement = {k: v for k, v in original.body.items() if k in keep}
            built = self._factory.redaction(
                investigation,
                original.reference,
                replacement,
                reason=command.reason or "representation narrowed",
                captured_by=command.actor_id,
                at=at,
                previous=original,
                sequence=self._repos.evidence.next_sequence(command.org_id, investigation.id),
            )
            stored, created = self._repos.evidence.append(built)
            if created:
                self._append_event(
                    investigation,
                    InvestigationEventKind.EVIDENCE_REDACTED,
                    at=at,
                    actor_id=command.actor_id,
                    subject=stored.id,
                    summary=stored.redaction_reason,
                    evidence_ids=(original.id, stored.id),
                    detail={"redacted_evidence_id": original.id},
                )
            self._recount(investigation)
            uow.stage(self._event(command, stored.id, redacted=original.id))
            return evidence_view(stored)

    # ── questions ───────────────────────────────────────────────────────────────

    def record_question(self, command: RecordQuestion) -> QuestionView:
        self._authz.require(command.actor_id, command.org_id, P.BILLING_INVESTIGATION_CONDUCT)
        at = self._clock.now()
        with self._uow.new() as uow:
            investigation = self.investigation_or_404(command.org_id, command.investigation_id)
            self._svc.guards.guard_recordable(investigation)
            built = self._factory.question(
                investigation,
                kind=command.kind,
                text=command.text,
                required=command.required,
                asked_by=command.actor_id,
                at=at,
            )
            stored, created = self._repos.questions.append(built)
            if created:
                self._append_event(
                    investigation,
                    InvestigationEventKind.QUESTION_RECORDED,
                    at=at,
                    actor_id=command.actor_id,
                    subject=stored.id,
                    summary=stored.text,
                )
                self._recount(investigation)
            uow.stage(self._event(command, stored.id, kind=stored.kind.value))
            return question_view(stored)

    def answer_question(self, command: AnswerQuestion) -> QuestionView:
        """Answer it, or record that the evidence cannot settle it. **Both are outcomes.**"""
        self._authz.require(command.actor_id, command.org_id, P.BILLING_INVESTIGATION_CONDUCT)
        at = self._clock.now()
        with self._uow.new() as uow:
            investigation = self.investigation_or_404(command.org_id, command.investigation_id)
            self._svc.guards.guard_recordable(investigation)
            question = self._repos.questions.get(command.org_id, command.question_id)
            if question is None or question.investigation_id != investigation.id:
                raise _NotFound(f"question {command.question_id!r} not found")
            if question.is_resolved:
                raise _Conflict(
                    f"question {question.id!r} is already {question.status.value}; a settled "
                    "question is not re-answered, because the record would then show one outcome "
                    "and hide another"
                )
            status = (
                QuestionStatus.UNANSWERABLE if command.unanswerable else QuestionStatus.ANSWERED
            )
            resolved = replace(
                question,
                status=status,
                answer=command.answer,
                evidence_ids=command.evidence_ids,
                answered_at=at,
                answered_by=command.actor_id,
                version=question.version + 1,
            )
            stored = self._repos.questions.resolve(
                resolved, expected_version=question.version
            )
            self._append_event(
                investigation,
                InvestigationEventKind.QUESTION_UNANSWERABLE
                if command.unanswerable
                else InvestigationEventKind.QUESTION_ANSWERED,
                at=at,
                actor_id=command.actor_id,
                subject=stored.id,
                summary=command.answer,
                evidence_ids=command.evidence_ids,
            )
            self._recount(investigation)
            uow.stage(self._event(command, stored.id, status=stored.status.value))
            return question_view(stored)

    # ── observations ────────────────────────────────────────────────────────────

    def record_observation(self, command: RecordObservation) -> ObservationView:
        """An observed fact must be evidence-backed; a hypothesis need not be.

        The cited evidence is **re-read here**, never taken on the caller's word: an id naming
        nothing supports nothing."""
        self._authz.require(command.actor_id, command.org_id, P.BILLING_INVESTIGATION_EVIDENCE)
        at = self._clock.now()
        with self._uow.new() as uow:
            investigation = self.investigation_or_404(command.org_id, command.investigation_id)
            self._svc.guards.guard_recordable(investigation)
            supporting = self._supporting(
                command.org_id, investigation.id, command.evidence_ids
            )
            self._svc.observations.guard_fact_is_evidence_backed(command.kind, supporting)
            built = self._factory.observation(
                investigation,
                kind=command.kind,
                statement=command.statement,
                evidence_ids=tuple(c.id for c in supporting),
                recorded_by=command.actor_id,
                at=at,
                sequence=self._repos.observations.next_sequence(command.org_id, investigation.id),
            )
            stored, created = self._repos.observations.append(built)
            if created:
                self._append_event(
                    investigation,
                    InvestigationEventKind.HYPOTHESIS_RECORDED
                    if command.kind is ObservationKind.HYPOTHESIS
                    else InvestigationEventKind.OBSERVATION_RECORDED,
                    at=at,
                    actor_id=command.actor_id,
                    subject=stored.id,
                    summary=stored.statement,
                    evidence_ids=stored.evidence_ids,
                )
            uow.stage(self._event(command, stored.id, kind=stored.kind.value))
            return observation_view(stored)

    def settle_hypothesis(self, command: SettleHypothesis) -> ObservationView:
        self._authz.require(command.actor_id, command.org_id, P.BILLING_INVESTIGATION_EVIDENCE)
        at = self._clock.now()
        with self._uow.new() as uow:
            investigation = self.investigation_or_404(command.org_id, command.investigation_id)
            self._svc.guards.guard_recordable(investigation)
            observation = self._repos.observations.get(command.org_id, command.observation_id)
            if observation is None or observation.investigation_id != investigation.id:
                raise _NotFound(f"observation {command.observation_id!r} not found")
            self._svc.observations.guard_settleable(observation)
            supporting = self._supporting(
                command.org_id, investigation.id, command.evidence_ids
            )
            settled = self._svc.observations.settle(
                observation,
                status=(
                    HypothesisStatus.SUPPORTED if command.supported else HypothesisStatus.REJECTED
                ),
                at=at,
                actor_id=command.actor_id,
                evidence_ids=tuple(c.id for c in supporting),
                reason=command.reason,
            )
            stored = self._repos.observations.settle(
                settled, expected_version=observation.version
            )
            self._append_event(
                investigation,
                InvestigationEventKind.HYPOTHESIS_SUPPORTED
                if command.supported
                else InvestigationEventKind.HYPOTHESIS_REJECTED,
                at=at,
                actor_id=command.actor_id,
                subject=stored.id,
                summary=command.reason,
                evidence_ids=stored.settling_evidence_ids,
            )
            uow.stage(self._event(command, stored.id, status=stored.hypothesis_status.value))
            return observation_view(stored)

    # ── conclusions ─────────────────────────────────────────────────────────────

    def record_conclusion(self, command: RecordConclusion) -> ConclusionView:
        """State the finding, with the evidence behind it — or state that there is none.

        The incident's root cause is read **at this moment** and stored on the conclusion as a
        historical copy. It is never written back: a conclusion that disagrees records a
        discrepancy for the owning M9.20 workflow, and this command touches no incident row."""
        self._authz.require(command.actor_id, command.org_id, P.BILLING_INVESTIGATION_CONCLUDE)
        at = self._clock.now()
        with self._uow.new() as uow:
            investigation = self.investigation_or_404(command.org_id, command.investigation_id)
            self._svc.guards.guard_recordable(investigation)
            supporting = self._supporting(
                command.org_id, investigation.id, command.evidence_ids
            )
            self._svc.conclusions.guard_supported(command.classification, supporting)
            self._svc.conclusions.guard_contradiction_is_specific(
                command.classification, command.evidenced_root_cause
            )
            incident = self._collector.incident(command.org_id, investigation.incident_id)
            incident_cause = incident.root_cause if incident else RootCause.UNKNOWN
            self._svc.conclusions.guard_not_self_contradicting(
                incident_cause, command.evidenced_root_cause
            )
            previous = self._effective_conclusion(command.org_id, investigation.id)
            built = self._factory.conclusion(
                investigation,
                classification=command.classification,
                statement=command.statement,
                evidence_ids=tuple(c.id for c in supporting),
                incident_root_cause=incident_cause,
                evidenced_root_cause=command.evidenced_root_cause,
                supersedes=command.supersedes or (previous.id if previous else ""),
                previous_version=previous.version if previous else 0,
                recorded_by=command.actor_id,
                at=at,
            )
            stored, created = self._repos.conclusions.append(built)
            if created:
                self._append_event(
                    investigation,
                    InvestigationEventKind.CONCLUSION_RECORDED,
                    at=at,
                    actor_id=command.actor_id,
                    subject=stored.id,
                    summary=stored.statement,
                    evidence_ids=stored.evidence_ids,
                    detail={"classification": stored.classification.value},
                )
                if stored.contradicts_incident:
                    evidenced_label = (
                        stored.evidenced_root_cause.value
                        if stored.evidenced_root_cause
                        else ""
                    )
                    self._append_event(
                        investigation,
                        InvestigationEventKind.CLASSIFICATION_DISCREPANCY_RECORDED,
                        at=at,
                        actor_id=command.actor_id,
                        subject=investigation.incident_id,
                        summary=(
                            "the evidence indicates "
                            f"{evidenced_label}, while incident "
                            f"{investigation.incident_id} is classified "
                            f"{incident_cause.value}. Reclassification belongs to the owning M9.20 "
                            "workflow; M9.21 records the disagreement and changes nothing"
                        ),
                        detail={
                            "incident_root_cause": incident_cause.value,
                            "evidenced_root_cause": (
                                stored.evidenced_root_cause.value
                                if stored.evidenced_root_cause
                                else ""
                            ),
                        },
                    )
                self._factory.emit_concluded(stored)
            # The investigation moves to CONCLUDED only from INVESTIGATING; a second conclusion
            # recorded while already CONCLUDED supersedes without a redundant transition.
            if investigation.status is InvestigationStatus.INVESTIGATING:
                moved = self._svc.lifecycle.apply(
                    investigation,
                    InvestigationTrigger.CONCLUDE,
                    at=at,
                    actor_id=command.actor_id,
                    reason=command.statement,
                    conclusion_id=stored.id,
                )
                self._repos.investigations.advance(
                    moved, expected_version=investigation.version
                )
            uow.stage(
                self._event(command, stored.id, classification=stored.classification.value)
            )
            return conclusion_view(stored)

    # ── the audit package ───────────────────────────────────────────────────────

    def export(self, command: ExportAuditPackage) -> AuditPackageView:
        """**Deterministic.** The same investigation state exports to the same package id.

        Completeness is decided on availability: an unreadable incident, an unavailable source or
        an unresolved required question each make the package `INCOMPLETE`, and every gap is named
        rather than dropped."""
        self._authz.require(command.actor_id, command.org_id, P.BILLING_AUDIT_EXPORT)
        at = self._clock.now()
        with self._uow.new() as uow:
            investigation = self.investigation_or_404(command.org_id, command.investigation_id)
            captures = self._captures(command.org_id, investigation.id)
            questions = self._repos.questions.for_investigation(command.org_id, investigation.id)
            observations = self._repos.observations.for_investigation(
                command.org_id, investigation.id
            )
            conclusions = self._repos.conclusions.for_investigation(
                command.org_id, investigation.id
            )
            timeline = self._repos.events.all_for_investigation(command.org_id, investigation.id)
            incident = self._collector.incident(command.org_id, investigation.incident_id)
            manifest = self._svc.manifest.build(
                investigation,
                incident=incident,
                captures=captures,
                questions=questions,
                observations=observations,
                conclusions=conclusions,
                timeline=timeline,
                custody=self._svc.custody,
            )
            gaps = self._svc.custody.gaps(captures)
            completeness = self._svc.completeness.assess(
                gaps=gaps, questions=questions, incident_available=incident is not None
            )
            built = self._factory.package(
                investigation,
                manifest=manifest,
                completeness=completeness,
                gaps=gaps,
                evidence_count=len(captures),
                observation_count=len(observations),
                question_count=len(questions),
                conclusion_count=len(conclusions),
                # The manifest's own view of the trail, so the count and the manifest agree.
                timeline_length=len(self._svc.manifest.manifest_timeline(timeline)),
                created_by=command.actor_id,
                at=at,
            )
            stored, created = self._repos.packages.create(built)
            if not created:
                self._svc.packages.guard_immutable(stored, built.manifest_digest)
                return package_view(stored)
            self._append_event(
                investigation,
                InvestigationEventKind.AUDIT_PACKAGE_CREATED,
                at=at,
                actor_id=command.actor_id,
                subject=stored.id,
                summary=f"audit package {stored.completeness.value}",
                detail={
                    "manifest_digest": stored.manifest_digest,
                    "gaps": str(len(stored.gaps)),
                },
            )
            self._factory.emit_package(stored)
            uow.stage(
                self._event(command, stored.id, completeness=stored.completeness.value)
            )
            return package_view(stored)

    # ── internals ───────────────────────────────────────────────────────────────

    def _append_event(
        self,
        investigation: Investigation,
        kind: InvestigationEventKind,
        *,
        at: float,
        actor_id: str = "",
        subject: str = "",
        summary: str = "",
        evidence_ids: tuple[str, ...] = (),
        detail: dict[str, str] | None = None,
    ) -> InvestigationEvent:
        sequence = self._repos.events.next_sequence(investigation.org_id, investigation.id)
        built = self._factory.event(
            investigation,
            kind=kind,
            sequence=sequence,
            at=at,
            actor_id=actor_id,
            subject=subject,
            summary=summary,
            evidence_ids=evidence_ids,
            detail=detail,
        )
        stored, _created = self._repos.events.append(built)
        return stored

    def _recount(self, investigation: Investigation) -> None:
        """Refresh the denormalized counts. **Counts only, and never money.**

        A compare-and-set that loses is swallowed here on purpose: the counts are a display
        convenience derived from rows that are already durable, and failing an evidence capture
        because a counter moved would lose the evidence to protect a number that can be recomputed.
        Nothing an auditor reads depends on them."""
        org_id = investigation.org_id
        captures = self._captures(org_id, investigation.id)
        questions = self._repos.questions.for_investigation(org_id, investigation.id)
        current = self._repos.investigations.get(org_id, investigation.id)
        if current is None or current.is_terminal:
            return
        unavailable = sum(
            1
            for c in self._svc.custody.current(captures).values()
            if c.availability is EvidenceAvailability.UNAVAILABLE
        )
        updated = replace(
            current,
            evidence_count=len(captures),
            open_question_count=sum(1 for q in questions if not q.is_resolved),
            unavailable_evidence_count=unavailable,
            version=current.version + 1,
        )
        try:
            self._repos.investigations.advance(updated, expected_version=current.version)
        except BillingError:
            return

    def _event(self, command, subject: str, **data: str) -> ApplicationEvent:
        return ApplicationEvent(
            id=self._ids.new(PREFIX_APP_EVENT),
            type=ApplicationEventType.INVESTIGATION_RECORDED,
            ts=self._clock.now(),
            use_case=type(command).__name__,
            outcome="completed",
            org_id=command.org_id,
            actor=command.actor_id,
            subject_id=subject,
            data=dict(data),
        )


class InvestigationQueries(_InvestigationSupport):
    """Reads. Every one org-scoped, bounded, index-backed and — for collections — paginated."""

    def __init__(self, *, context, repos, services, authz: AuthorizationOrchestrator) -> None:
        super().__init__(context=context, repos=repos, services=services)
        self._authz = authz

    def get(self, query: GetInvestigation) -> InvestigationView:
        self._read(query)
        return self._view(self.investigation_or_404(query.org_id, query.investigation_id))

    def for_incident(self, query: GetInvestigationForIncident) -> InvestigationView:
        self._read(query)
        found = self._repos.investigations.by_incident(query.org_id, query.incident_id)
        if found is None:
            raise _NotFound(
                f"no investigation has been opened on incident {query.incident_id!r}"
            )
        return self._view(found)

    def list_investigations(self, query: ListInvestigations) -> InvestigationPageView:
        self._read(query)
        return self._page(
            InvestigationSearchQuery(
                org_id=query.org_id,
                incident_id=query.incident_id,
                status=query.status,
                priority=query.priority,
                blocked_only=query.blocked_only,
                offset=query.offset,
                limit=query.limit,
            )
        )

    def search(self, query: SearchInvestigations) -> InvestigationPageView:
        self._read(query)
        return self._page(
            InvestigationSearchQuery(
                org_id=query.org_id,
                incident_id=query.incident_id,
                status=query.status,
                priority=query.priority,
                opened_by=query.opened_by,
                opened_from=query.opened_from,
                opened_to=query.opened_to,
                blocked_only=query.blocked_only,
                offset=query.offset,
                limit=query.limit,
            )
        )

    def _page(self, spec: InvestigationSearchQuery) -> InvestigationPageView:
        rows, total = self._repos.investigations.page(spec)
        return InvestigationPageView(
            investigations=tuple(self._view(i) for i in rows),
            total=total,
            offset=spec.offset,
            limit=spec.limit,
            has_more=spec.offset + len(rows) < total,
        )

    def timeline(self, query: GetInvestigationTimeline) -> TimelinePageView:
        self._read(query)
        investigation = self.investigation_or_404(query.org_id, query.investigation_id)
        rows, total = self._repos.events.for_investigation(
            query.org_id, investigation.id, offset=query.offset, limit=query.limit
        )
        return TimelinePageView(
            entries=tuple(timeline_view(e) for e in rows),
            total=total,
            offset=query.offset,
            limit=query.limit,
        )

    def evidence(self, query: GetInvestigationEvidence) -> EvidencePageView:
        self._read(query)
        investigation = self.investigation_or_404(query.org_id, query.investigation_id)
        rows, total = self._repos.evidence.for_investigation(
            query.org_id, investigation.id, offset=query.offset, limit=query.limit
        )
        return EvidencePageView(
            evidence=tuple(evidence_view(c) for c in rows),
            total=total,
            offset=query.offset,
            limit=query.limit,
        )

    def custody(self, query: GetCustodyChains) -> tuple[CustodyChainView, ...]:
        self._read(query)
        investigation = self.investigation_or_404(query.org_id, query.investigation_id)
        chains = self._svc.custody.chains(self._captures(query.org_id, investigation.id))
        return tuple(chain_view(c) for c in chains)

    def evidence_chain(self, query: GetEvidenceChain) -> EvidenceChainView:
        """The incident → finding → evidence → repair chain, **assembled from references**.

        Every node names a fact another milestone owns and every edge names why the two connect.
        A node whose source could not be read is present and marked unavailable, never dropped."""
        self._read(query)
        investigation = self.investigation_or_404(query.org_id, query.investigation_id)
        captures = self._captures(query.org_id, investigation.id)
        current = self._svc.custody.current(captures)
        incident = self._collector.incident(query.org_id, investigation.incident_id)

        nodes: list[EvidenceChainNode] = [
            EvidenceChainNode(
                kind="incident",
                identity=investigation.incident_id,
                label=(
                    f"{incident.status.value} · {incident.severity.value} · "
                    f"{incident.root_cause.value}"
                    if incident
                    else "unreadable"
                ),
                available=incident is not None,
            )
        ]
        edges: list[EvidenceChainEdge] = []
        relationships = incident.relationships if incident else ()

        for link in self._collector.links_for(query.org_id, investigation.incident_id):
            nodes.append(
                EvidenceChainNode(
                    kind="finding",
                    identity=link.finding_id,
                    label=f"{link.relationship.value} · {link.divergence.value}",
                    available=link.is_attached,
                )
            )
            edges.append(
                EvidenceChainEdge(
                    source=investigation.incident_id,
                    target=link.finding_id,
                    relation="correlated_by_" + link.anchor_kind.value,
                )
            )

        for locator, capture in sorted(current.items()):
            nodes.append(
                EvidenceChainNode(
                    kind="evidence",
                    identity=capture.id,
                    label=f"{locator} · {capture.mode.value} · {capture.availability.value}",
                    available=capture.supports_conclusion,
                )
            )
            edges.append(
                EvidenceChainEdge(
                    source=capture.reference.source_id,
                    target=capture.id,
                    relation="captured_as_" + capture.mode.value,
                )
            )

        for repair in self._collector.repairs_for(query.org_id, investigation.incident_id):
            nodes.append(
                EvidenceChainNode(
                    kind="repair",
                    identity=repair.id,
                    label=f"{repair.owner.value} · {repair.action}"
                    + (f" · {repair.outcome}" if repair.outcome else ""),
                    available=True,
                )
            )
            edges.append(
                EvidenceChainEdge(
                    source=repair.finding_id or investigation.incident_id,
                    target=repair.id,
                    relation="repair_requested_of_owner",
                )
            )

        return evidence_chain_view(
            InvestigationEvidenceChain(
                investigation_id=investigation.id,
                incident_id=investigation.incident_id,
                nodes=tuple(nodes),
                edges=tuple(edges),
                relationships=relationships,
            )
        )

    def questions(self, query: GetInvestigationQuestions) -> tuple[QuestionView, ...]:
        self._read(query)
        investigation = self.investigation_or_404(query.org_id, query.investigation_id)
        rows = self._repos.questions.for_investigation(query.org_id, investigation.id)
        return tuple(question_view(q) for q in sorted(rows, key=lambda q: (q.asked_at, q.id)))

    def observations(self, query: GetInvestigationObservations) -> tuple[ObservationView, ...]:
        self._read(query)
        investigation = self.investigation_or_404(query.org_id, query.investigation_id)
        rows = self._repos.observations.for_investigation(query.org_id, investigation.id)
        return tuple(observation_view(o) for o in sorted(rows, key=lambda o: (o.sequence, o.id)))

    def conclusions(self, query: GetInvestigationConclusions) -> tuple[ConclusionView, ...]:
        """Every conclusion, superseded ones included — **that is the point**. An examiner needs to
        see that the first answer was wrong and when it was corrected."""
        self._read(query)
        investigation = self.investigation_or_404(query.org_id, query.investigation_id)
        rows = self._repos.conclusions.for_investigation(query.org_id, investigation.id)
        return tuple(conclusion_view(c) for c in sorted(rows, key=lambda c: (c.version, c.id)))

    def assess_closure(self, query: AssessClosure) -> ClosureAssessmentView:
        self._read(query)
        investigation = self.investigation_or_404(query.org_id, query.investigation_id)
        return closure_view(self._assess(query.org_id, investigation))

    def package(self, query: GetAuditPackage) -> AuditPackageView:
        self._authz.require(query.actor_id, query.org_id, P.BILLING_AUDIT_EXPORT)
        investigation = self.investigation_or_404(query.org_id, query.investigation_id)
        found = (
            self._repos.packages.get(query.org_id, query.package_id)
            if query.package_id
            else self._repos.packages.latest(query.org_id, investigation.id)
        )
        if found is None or found.investigation_id != investigation.id:
            raise _NotFound(
                f"no audit package has been exported for investigation {investigation.id!r}"
            )
        return package_view(found)

    def posture(self, query: GetInvestigationPosture) -> InvestigationPostureView:
        """Counts, computed by the database. Never a figure, and never a Python-side scan."""
        self._read(query)
        counts = self._repos.investigations.counts(query.org_id)
        by_status = counts.get("by_status", {})
        return posture_view(
            InvestigationPosture(
                org_id=query.org_id,
                total=sum(by_status.values()),
                by_status=by_status,
                by_priority=counts.get("by_priority", {}),
                blocked=by_status.get(InvestigationStatus.AWAITING_EVIDENCE.value, 0),
                concluded_awaiting_closure=by_status.get(
                    InvestigationStatus.CONCLUDED.value, 0
                ),
                unavailable_evidence=counts.get("totals", {}).get("unavailable_evidence", 0),
                packages_exported=counts.get("totals", {}).get("packages", 0),
                incomplete_packages=counts.get("totals", {}).get("incomplete_packages", 0),
            )
        )

    def _read(self, query) -> None:
        self._authz.require(query.actor_id, query.org_id, P.BILLING_INVESTIGATION_READ)


def register_investigation_use_cases(
    command_bus, query_bus, uc: InvestigationUseCases, q: InvestigationQueries
):
    """Wire the investigation vocabulary onto the shared buses. One handler per message."""
    command_bus.register(OpenInvestigation, uc.open)
    command_bus.register(TriageInvestigation, uc.triage)
    command_bus.register(StartInvestigation, uc.start)
    command_bus.register(CaptureEvidence, uc.capture)
    command_bus.register(CaptureIncidentEvidence, uc.capture_incident)
    command_bus.register(MarkEvidenceUnavailable, uc.mark_unavailable)
    command_bus.register(RedactEvidence, uc.redact)
    command_bus.register(RecordQuestion, uc.record_question)
    command_bus.register(AnswerQuestion, uc.answer_question)
    command_bus.register(RecordObservation, uc.record_observation)
    command_bus.register(SettleHypothesis, uc.settle_hypothesis)
    command_bus.register(RecordConclusion, uc.record_conclusion)
    command_bus.register(AwaitEvidence, uc.await_evidence)
    command_bus.register(ResumeInvestigation, uc.resume)
    command_bus.register(CloseInvestigation, uc.close)
    command_bus.register(ExportAuditPackage, uc.export)
    query_bus.register(GetInvestigation, q.get)
    query_bus.register(GetInvestigationForIncident, q.for_incident)
    query_bus.register(ListInvestigations, q.list_investigations)
    query_bus.register(SearchInvestigations, q.search)
    query_bus.register(GetInvestigationTimeline, q.timeline)
    query_bus.register(GetInvestigationEvidence, q.evidence)
    query_bus.register(GetCustodyChains, q.custody)
    query_bus.register(GetEvidenceChain, q.evidence_chain)
    query_bus.register(GetInvestigationQuestions, q.questions)
    query_bus.register(GetInvestigationObservations, q.observations)
    query_bus.register(GetInvestigationConclusions, q.conclusions)
    query_bus.register(AssessClosure, q.assess_closure)
    query_bus.register(GetAuditPackage, q.package)
    query_bus.register(GetInvestigationPosture, q.posture)


__all__ = [
    "ALL_INVESTIGATION_COMMANDS",
    "ALL_INVESTIGATION_QUERIES",
    "AnswerQuestion",
    "AssessClosure",
    "AuditPackageView",
    "AwaitEvidence",
    "CaptureEvidence",
    "CaptureIncidentEvidence",
    "CloseInvestigation",
    "ClosureAssessmentView",
    "ConclusionView",
    "CustodyChainView",
    "CustodyLinkView",
    "EvidenceChainEdgeView",
    "EvidenceChainNodeView",
    "EvidenceChainView",
    "EvidenceGapView",
    "EvidencePageView",
    "EvidenceView",
    "ExportAuditPackage",
    "GetAuditPackage",
    "GetCustodyChains",
    "GetEvidenceChain",
    "GetInvestigation",
    "GetInvestigationConclusions",
    "GetInvestigationEvidence",
    "GetInvestigationForIncident",
    "GetInvestigationObservations",
    "GetInvestigationPosture",
    "GetInvestigationQuestions",
    "GetInvestigationTimeline",
    "InvestigationPageView",
    "InvestigationPostureView",
    "InvestigationQueries",
    "InvestigationRepositories",
    "InvestigationServices",
    "InvestigationUseCases",
    "InvestigationView",
    "ListInvestigations",
    "MarkEvidenceUnavailable",
    "ObservationView",
    "OpenInvestigation",
    "QuestionView",
    "RecordConclusion",
    "RecordObservation",
    "RecordQuestion",
    "RedactEvidence",
    "ResumeInvestigation",
    "SearchInvestigations",
    "SettleHypothesis",
    "StartInvestigation",
    "TimelineEntryView",
    "TimelinePageView",
    "TriageInvestigation",
    "chain_view",
    "closure_view",
    "conclusion_view",
    "evidence_chain_view",
    "evidence_view",
    "gap_view",
    "investigation_view",
    "observation_view",
    "package_view",
    "posture_view",
    "question_view",
    "register_investigation_use_cases",
    "timeline_view",
]
