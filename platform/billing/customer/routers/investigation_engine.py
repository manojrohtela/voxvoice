"""Financial Investigation & Audit Evidence routes (M9.21) — transport only.

Additive to the M9.5–M9.20 transport layer and identical in discipline: parse, take identity from
the verified token, construct a command or query, dispatch, serialize. No investigation rule is
evaluated here — whether a statement is evidence-backed, whether a conclusion may be confirmed,
whether a case may close and what an audit package contains are all Foundation behaviour reached
through the Application.

Mounted at **`/investigations`**, its own prefix. That is deliberate rather than convenient: an
investigation is a different subject from a finding or an incident — a *workflow over* the books
rather than a statement *about* them — and hanging it under `/integrity` would suggest a client
could reach an incident's state by walking down from it. It cannot: this surface changes no
incident.

**Ten things no endpoint here does.** None reconciles — that is M9.19. None correlates or merges —
that is M9.20. None calculates a price, a rate, a tax, an invoice total, a recognized amount, a
balance or a financial impact. **None posts a ledger entry, and none can.** None executes a
repair: an investigation *references* a repair the owning domain performed and can ask no domain to
perform one, because there is no outward command on this surface at all. None resolves an incident
or changes its root-cause classification — a conclusion that disagrees records a discrepancy for
the owning M9.20 workflow. None marks unavailable evidence as verified: `/capture-evidence`
records an `UNAVAILABLE` link when a source cannot be read rather than substituting a live object.
None closes a case with required questions outstanding — `/close` consults the same assessment
`/closure` returns. None rewrites an audit package: a changed investigation exports a new one
beside the old. And there is **no DELETE, PUT or PATCH anywhere in this file** — historical
financial evidence is not mutated.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query

from billing.application import (
    AnswerQuestion,
    AssessClosure,
    AwaitEvidence,
    BillingApplication,
    CaptureEvidence,
    CaptureIncidentEvidence,
    CloseInvestigation,
    ExportAuditPackage,
    GetAuditPackage,
    GetCustodyChains,
    GetEvidenceChain,
    GetInvestigation,
    GetInvestigationConclusions,
    GetInvestigationEvidence,
    GetInvestigationForIncident,
    GetInvestigationObservations,
    GetInvestigationPosture,
    GetInvestigationQuestions,
    GetInvestigationTimeline,
    ListInvestigations,
    MarkEvidenceUnavailable,
    OpenInvestigation,
    RecordConclusion,
    RecordObservation,
    RecordQuestion,
    RedactEvidence,
    ResumeInvestigation,
    SearchInvestigations,
    SettleHypothesis,
    StartInvestigation,
    TriageInvestigation,
)
from billing.foundation import InvestigationPriority, InvestigationStatus

from ..auth import require_org
from ..container import get_application
from ..context import BillingRequestContext
from ..investigation_schemas import (
    AnswerQuestionBody,
    AuditPackageResponse,
    CaptureEvidenceBody,
    CaptureIncidentEvidenceBody,
    ClosureAssessmentResponse,
    ConclusionResponse,
    CustodyChainResponse,
    EvidenceCaptureResponse,
    EvidenceChainResponse,
    EvidencePageResponse,
    InvestigationNoteBody,
    InvestigationPageResponse,
    InvestigationPostureResponse,
    InvestigationReasonBody,
    InvestigationResponse,
    MarkUnavailableBody,
    ObservationResponse,
    OpenInvestigationBody,
    QuestionResponse,
    RecordConclusionBody,
    RecordObservationBody,
    RecordQuestionBody,
    RedactEvidenceBody,
    SettleHypothesisBody,
    TimelinePageResponse,
    TriageInvestigationBody,
)
from ..schemas import ERROR_RESPONSES

router = APIRouter(
    prefix="/investigations", tags=["investigations"], responses=ERROR_RESPONSES
)


# ── opening ─────────────────────────────────────────────────────────────────────


@router.post(
    "",
    response_model=InvestigationResponse,
    status_code=201,
    summary="Open an investigation into an integrity incident",
)
def open_investigation(
    body: OpenInvestigationBody,
    ctx: BillingRequestContext = Depends(require_org),
    app: BillingApplication = Depends(get_application),
) -> InvestigationResponse:
    """**The endpoint this milestone exists for.**

    Refuses without a readable incident: an investigation of nothing is a container for a narrative
    no observation supports. Idempotent — two investigators opening a case on one incident get one
    investigation, because the identity is derived from `(org, incident)`."""
    return InvestigationResponse.of(
        app.commands.dispatch(
            OpenInvestigation(
                actor_id=ctx.actor(),
                org_id=ctx.organization(),
                incident_id=body.incident_id,
                summary=body.summary,
                priority=body.priority,
            )
        )
    )


# ── collections (declared before `{investigation_id}` so literals are not swallowed) ──


@router.get(
    "",
    response_model=InvestigationPageResponse,
    summary="Investigations, paginated at the database",
)
def list_investigations(
    status: InvestigationStatus | None = None,
    priority: InvestigationPriority | None = None,
    incident_id: str = "",
    blocked_only: bool = False,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    ctx: BillingRequestContext = Depends(require_org),
    app: BillingApplication = Depends(get_application),
) -> InvestigationPageResponse:
    """`total` comes from a database count. Paging by fetching everything is not an option."""
    return InvestigationPageResponse.of(
        app.queries.dispatch(
            ListInvestigations(
                actor_id=ctx.actor(),
                org_id=ctx.organization(),
                status=status,
                priority=priority,
                incident_id=incident_id,
                blocked_only=blocked_only,
                limit=limit,
                offset=offset,
            )
        )
    )


@router.get(
    "/posture",
    response_model=InvestigationPostureResponse,
    summary="The org's investigation picture — counts, never money",
)
def posture(
    ctx: BillingRequestContext = Depends(require_org),
    app: BillingApplication = Depends(get_application),
) -> InvestigationPostureResponse:
    """Grouped counts computed by the database. **No amount on this response.**"""
    return InvestigationPostureResponse.of(
        app.queries.dispatch(
            GetInvestigationPosture(actor_id=ctx.actor(), org_id=ctx.organization())
        )
    )


@router.get(
    "/search",
    response_model=InvestigationPageResponse,
    summary="Search investigations — every filter is a SQL predicate",
)
def search(
    incident_id: str = "",
    status: InvestigationStatus | None = None,
    priority: InvestigationPriority | None = None,
    opened_by: str = "",
    opened_from: float | None = None,
    opened_to: float | None = None,
    blocked_only: bool = False,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    ctx: BillingRequestContext = Depends(require_org),
    app: BillingApplication = Depends(get_application),
) -> InvestigationPageResponse:
    """Nothing here is filtered in Python after loading."""
    return InvestigationPageResponse.of(
        app.queries.dispatch(
            SearchInvestigations(
                actor_id=ctx.actor(),
                org_id=ctx.organization(),
                incident_id=incident_id,
                status=status,
                priority=priority,
                opened_by=opened_by,
                opened_from=opened_from,
                opened_to=opened_to,
                blocked_only=blocked_only,
                limit=limit,
                offset=offset,
            )
        )
    )


@router.get(
    "/for-incident/{incident_id}",
    response_model=InvestigationResponse,
    summary="The investigation opened on one incident, if any",
)
def for_incident(
    incident_id: str,
    ctx: BillingRequestContext = Depends(require_org),
    app: BillingApplication = Depends(get_application),
) -> InvestigationResponse:
    return InvestigationResponse.of(
        app.queries.dispatch(
            GetInvestigationForIncident(
                actor_id=ctx.actor(), org_id=ctx.organization(), incident_id=incident_id
            )
        )
    )


# ── one investigation ───────────────────────────────────────────────────────────


@router.get(
    "/{investigation_id}",
    response_model=InvestigationResponse,
    summary="One investigation, with the triggers the server will accept",
)
def get_investigation(
    investigation_id: str,
    ctx: BillingRequestContext = Depends(require_org),
    app: BillingApplication = Depends(get_application),
) -> InvestigationResponse:
    """`allowed_triggers` is the server's answer to *what can I do with this*. A closed
    investigation returns an empty list — the lifecycle has no edge out of it."""
    return InvestigationResponse.of(
        app.queries.dispatch(
            GetInvestigation(
                actor_id=ctx.actor(),
                org_id=ctx.organization(),
                investigation_id=investigation_id,
            )
        )
    )


@router.get(
    "/{investigation_id}/timeline",
    response_model=TimelinePageResponse,
    summary="The investigation timeline — the record, not a log of it",
)
def timeline(
    investigation_id: str,
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    ctx: BillingRequestContext = Depends(require_org),
    app: BillingApplication = Depends(get_application),
) -> TimelinePageResponse:
    """Append-only and totally ordered. An examiner reconstructs who looked, when and on what
    basis from these rows rather than from application logs."""
    return TimelinePageResponse.of(
        app.queries.dispatch(
            GetInvestigationTimeline(
                actor_id=ctx.actor(),
                org_id=ctx.organization(),
                investigation_id=investigation_id,
                limit=limit,
                offset=offset,
            )
        )
    )


@router.get(
    "/{investigation_id}/evidence",
    response_model=EvidencePageResponse,
    summary="Captured evidence, paginated",
)
def evidence(
    investigation_id: str,
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    ctx: BillingRequestContext = Depends(require_org),
    app: BillingApplication = Depends(get_application),
) -> EvidencePageResponse:
    """Each entry states its `mode` and its `content_digest`, so a client can tell a historical
    claim from a pointer without inspecting the body."""
    return EvidencePageResponse.of(
        app.queries.dispatch(
            GetInvestigationEvidence(
                actor_id=ctx.actor(),
                org_id=ctx.organization(),
                investigation_id=investigation_id,
                limit=limit,
                offset=offset,
            )
        )
    )


@router.get(
    "/{investigation_id}/custody",
    response_model=list[CustodyChainResponse],
    summary="Chains of custody — what was captured first, and what changed",
)
def custody(
    investigation_id: str,
    ctx: BillingRequestContext = Depends(require_org),
    app: BillingApplication = Depends(get_application),
) -> list[CustodyChainResponse]:
    """Nothing is ever replaced, so each chain shows every representation this investigation
    held of one source, oldest first."""
    return [
        CustodyChainResponse.of(c)
        for c in app.queries.dispatch(
            GetCustodyChains(
                actor_id=ctx.actor(),
                org_id=ctx.organization(),
                investigation_id=investigation_id,
            )
        )
    ]


@router.get(
    "/{investigation_id}/chain",
    response_model=EvidenceChainResponse,
    summary="The incident → finding → evidence → repair chain",
)
def evidence_chain(
    investigation_id: str,
    ctx: BillingRequestContext = Depends(require_org),
    app: BillingApplication = Depends(get_application),
) -> EvidenceChainResponse:
    """Assembled from references. **This endpoint creates none of these facts** — every node names
    something another milestone owns, and an unreadable one is marked rather than dropped."""
    return EvidenceChainResponse.of(
        app.queries.dispatch(
            GetEvidenceChain(
                actor_id=ctx.actor(),
                org_id=ctx.organization(),
                investigation_id=investigation_id,
            )
        )
    )


@router.get(
    "/{investigation_id}/questions",
    response_model=list[QuestionResponse],
    summary="Questions and their outcomes",
)
def questions(
    investigation_id: str,
    ctx: BillingRequestContext = Depends(require_org),
    app: BillingApplication = Depends(get_application),
) -> list[QuestionResponse]:
    return [
        QuestionResponse.of(q)
        for q in app.queries.dispatch(
            GetInvestigationQuestions(
                actor_id=ctx.actor(),
                org_id=ctx.organization(),
                investigation_id=investigation_id,
            )
        )
    ]


@router.get(
    "/{investigation_id}/observations",
    response_model=list[ObservationResponse],
    summary="Observed facts and hypotheses, kept apart",
)
def observations(
    investigation_id: str,
    ctx: BillingRequestContext = Depends(require_org),
    app: BillingApplication = Depends(get_application),
) -> list[ObservationResponse]:
    """`kind` distinguishes an observed fact from a hypothesis, and a rejected hypothesis stays on
    the record beside the conclusion that replaced it."""
    return [
        ObservationResponse.of(o)
        for o in app.queries.dispatch(
            GetInvestigationObservations(
                actor_id=ctx.actor(),
                org_id=ctx.organization(),
                investigation_id=investigation_id,
            )
        )
    ]


@router.get(
    "/{investigation_id}/conclusions",
    response_model=list[ConclusionResponse],
    summary="Every conclusion, superseded ones included",
)
def conclusions(
    investigation_id: str,
    ctx: BillingRequestContext = Depends(require_org),
    app: BillingApplication = Depends(get_application),
) -> list[ConclusionResponse]:
    """Superseded conclusions are returned deliberately: an examiner needs to see that the first
    answer was wrong and when it was corrected."""
    return [
        ConclusionResponse.of(c)
        for c in app.queries.dispatch(
            GetInvestigationConclusions(
                actor_id=ctx.actor(),
                org_id=ctx.organization(),
                investigation_id=investigation_id,
            )
        )
    ]


@router.get(
    "/{investigation_id}/closure",
    response_model=ClosureAssessmentResponse,
    summary="What is outstanding, before anybody presses close",
)
def closure(
    investigation_id: str,
    ctx: BillingRequestContext = Depends(require_org),
    app: BillingApplication = Depends(get_application),
) -> ClosureAssessmentResponse:
    """The same assessment `/close` consults. The client renders `reasons`; it never decides
    closability locally."""
    return ClosureAssessmentResponse.of(
        app.queries.dispatch(
            AssessClosure(
                actor_id=ctx.actor(),
                org_id=ctx.organization(),
                investigation_id=investigation_id,
            )
        )
    )


@router.get(
    "/{investigation_id}/audit-package",
    response_model=AuditPackageResponse,
    summary="The latest immutable audit package",
)
def audit_package(
    investigation_id: str,
    package_id: str = "",
    ctx: BillingRequestContext = Depends(require_org),
    app: BillingApplication = Depends(get_application),
) -> AuditPackageResponse:
    """Requires audit permission. `manifest_digest` is recomputable from `manifest`, which is what
    makes the export auditable rather than merely downloadable."""
    return AuditPackageResponse.of(
        app.queries.dispatch(
            GetAuditPackage(
                actor_id=ctx.actor(),
                org_id=ctx.organization(),
                investigation_id=investigation_id,
                package_id=package_id,
            )
        )
    )


# ── lifecycle ───────────────────────────────────────────────────────────────────


@router.post(
    "/{investigation_id}/triage",
    response_model=InvestigationResponse,
    summary="Assign priority",
)
def triage(
    investigation_id: str,
    body: TriageInvestigationBody,
    ctx: BillingRequestContext = Depends(require_org),
    app: BillingApplication = Depends(get_application),
) -> InvestigationResponse:
    """`priority` is operational urgency, **not severity** — M9.20 owns severity and this never
    restates it."""
    return InvestigationResponse.of(
        app.commands.dispatch(
            TriageInvestigation(
                actor_id=ctx.actor(),
                org_id=ctx.organization(),
                investigation_id=investigation_id,
                priority=body.priority,
                note=body.note,
            )
        )
    )


@router.post(
    "/{investigation_id}/start",
    response_model=InvestigationResponse,
    summary="Begin work",
)
def start(
    investigation_id: str,
    body: InvestigationNoteBody,
    ctx: BillingRequestContext = Depends(require_org),
    app: BillingApplication = Depends(get_application),
) -> InvestigationResponse:
    return InvestigationResponse.of(
        app.commands.dispatch(
            StartInvestigation(
                actor_id=ctx.actor(),
                org_id=ctx.organization(),
                investigation_id=investigation_id,
                note=body.note,
            )
        )
    )


@router.post(
    "/{investigation_id}/await-evidence",
    response_model=InvestigationResponse,
    summary="Park a case blocked on evidence that cannot be retrieved",
)
def await_evidence(
    investigation_id: str,
    body: InvestigationReasonBody,
    ctx: BillingRequestContext = Depends(require_org),
    app: BillingApplication = Depends(get_application),
) -> InvestigationResponse:
    """A blocked case is **visibly distinct** from one nobody is working on."""
    return InvestigationResponse.of(
        app.commands.dispatch(
            AwaitEvidence(
                actor_id=ctx.actor(),
                org_id=ctx.organization(),
                investigation_id=investigation_id,
                reason=body.reason,
            )
        )
    )


@router.post(
    "/{investigation_id}/resume",
    response_model=InvestigationResponse,
    summary="Resume a blocked or concluded case",
)
def resume(
    investigation_id: str,
    body: InvestigationNoteBody,
    ctx: BillingRequestContext = Depends(require_org),
    app: BillingApplication = Depends(get_application),
) -> InvestigationResponse:
    """A **closed** case is not resumable — there is no edge out of it, and the server refuses."""
    return InvestigationResponse.of(
        app.commands.dispatch(
            ResumeInvestigation(
                actor_id=ctx.actor(),
                org_id=ctx.organization(),
                investigation_id=investigation_id,
                note=body.note,
            )
        )
    )


@router.post(
    "/{investigation_id}/close",
    response_model=InvestigationResponse,
    summary="Close the investigation — not the incident",
)
def close(
    investigation_id: str,
    body: InvestigationReasonBody,
    ctx: BillingRequestContext = Depends(require_org),
    app: BillingApplication = Depends(get_application),
) -> InvestigationResponse:
    """Requires elevated permission, and refuses with the outstanding list unless every required
    question has an outcome, every hypothesis is settled, no unavailable evidence blocks a
    conclusion and a conclusion is on record.

    **Closing the investigation does not resolve the incident.** M9.20 owns that, and a case may
    legitimately close `INCONCLUSIVE` while its incident is still open."""
    return InvestigationResponse.of(
        app.commands.dispatch(
            CloseInvestigation(
                actor_id=ctx.actor(),
                org_id=ctx.organization(),
                investigation_id=investigation_id,
                reason=body.reason,
            )
        )
    )


# ── evidence ────────────────────────────────────────────────────────────────────


@router.post(
    "/{investigation_id}/capture-evidence",
    response_model=EvidenceCaptureResponse,
    status_code=201,
    summary="Capture one source as a snapshot, or reference it live",
)
def capture_evidence(
    investigation_id: str,
    body: CaptureEvidenceBody,
    ctx: BillingRequestContext = Depends(require_org),
    app: BillingApplication = Depends(get_application),
) -> EvidenceCaptureResponse:
    """The caller supplies **no representation** — evidence a caller could author is not evidence.
    The server reads the owning domain and renders the body itself, and when it cannot, records an
    `UNAVAILABLE` link rather than substituting a live object."""
    return EvidenceCaptureResponse.of(
        app.commands.dispatch(
            CaptureEvidence(
                actor_id=ctx.actor(),
                org_id=ctx.organization(),
                investigation_id=investigation_id,
                domain=body.domain,
                source_type=body.source_type,
                source_id=body.source_id,
                mode=body.mode,
                note=body.note,
            )
        )
    )


@router.post(
    "/{investigation_id}/capture-incident",
    response_model=list[EvidenceCaptureResponse],
    status_code=201,
    summary="Capture the incident, its findings and its repairs in one pass",
)
def capture_incident(
    investigation_id: str,
    body: CaptureIncidentEvidenceBody,
    ctx: BillingRequestContext = Depends(require_org),
    app: BillingApplication = Depends(get_application),
) -> list[EvidenceCaptureResponse]:
    """Idempotent: an unchanged source recaptured lands on the same content-addressed row."""
    return [
        EvidenceCaptureResponse.of(c)
        for c in app.commands.dispatch(
            CaptureIncidentEvidence(
                actor_id=ctx.actor(),
                org_id=ctx.organization(),
                investigation_id=investigation_id,
                include_findings=body.include_findings,
                include_repairs=body.include_repairs,
            )
        )
    ]


@router.post(
    "/{investigation_id}/evidence-unavailable",
    response_model=EvidenceCaptureResponse,
    status_code=201,
    summary="Record that a source could not be retrieved",
)
def mark_unavailable(
    investigation_id: str,
    body: MarkUnavailableBody,
    ctx: BillingRequestContext = Depends(require_org),
    app: BillingApplication = Depends(get_application),
) -> EvidenceCaptureResponse:
    """**A new link, never an edit.** The previous capture keeps its identity, digest and author,
    so the chain reads *we held this, then we could not* — and "could not retrieve" never becomes
    "verified"."""
    return EvidenceCaptureResponse.of(
        app.commands.dispatch(
            MarkEvidenceUnavailable(
                actor_id=ctx.actor(),
                org_id=ctx.organization(),
                investigation_id=investigation_id,
                domain=body.domain,
                source_type=body.source_type,
                source_id=body.source_id,
                reason=body.reason,
            )
        )
    )


@router.post(
    "/{investigation_id}/redact-evidence",
    response_model=EvidenceCaptureResponse,
    status_code=201,
    summary="Supersede a representation with a narrower one",
)
def redact(
    investigation_id: str,
    body: RedactEvidenceBody,
    ctx: BillingRequestContext = Depends(require_org),
    app: BillingApplication = Depends(get_application),
) -> EvidenceCaptureResponse:
    """A POST rather than a PATCH, and that is the point: the original row is untouched and the
    replacement is appended naming it. The original identity is never destroyed."""
    return EvidenceCaptureResponse.of(
        app.commands.dispatch(
            RedactEvidence(
                actor_id=ctx.actor(),
                org_id=ctx.organization(),
                investigation_id=investigation_id,
                evidence_id=body.evidence_id,
                reason=body.reason,
                keep_fields=tuple(body.keep_fields),
            )
        )
    )


# ── questions, observations, conclusions ────────────────────────────────────────


@router.post(
    "/{investigation_id}/question",
    response_model=QuestionResponse,
    status_code=201,
    summary="Record a question",
)
def record_question(
    investigation_id: str,
    body: RecordQuestionBody,
    ctx: BillingRequestContext = Depends(require_org),
    app: BillingApplication = Depends(get_application),
) -> QuestionResponse:
    """A question is a **record**, not an executable query. Nothing here interprets the text."""
    return QuestionResponse.of(
        app.commands.dispatch(
            RecordQuestion(
                actor_id=ctx.actor(),
                org_id=ctx.organization(),
                investigation_id=investigation_id,
                kind=body.kind,
                text=body.text,
                required=body.required,
            )
        )
    )


@router.post(
    "/{investigation_id}/answer",
    response_model=QuestionResponse,
    summary="Answer a question, or record that evidence cannot settle it",
)
def answer_question(
    investigation_id: str,
    body: AnswerQuestionBody,
    ctx: BillingRequestContext = Depends(require_org),
    app: BillingApplication = Depends(get_application),
) -> QuestionResponse:
    """`unanswerable` is a real outcome: a required question the evidence cannot settle must be
    closable without anybody inventing an answer."""
    return QuestionResponse.of(
        app.commands.dispatch(
            AnswerQuestion(
                actor_id=ctx.actor(),
                org_id=ctx.organization(),
                investigation_id=investigation_id,
                question_id=body.question_id,
                answer=body.answer,
                evidence_ids=tuple(body.evidence_ids),
                unanswerable=body.unanswerable,
            )
        )
    )


@router.post(
    "/{investigation_id}/observation",
    response_model=ObservationResponse,
    status_code=201,
    summary="Record an observed fact or a hypothesis",
)
def record_observation(
    investigation_id: str,
    body: RecordObservationBody,
    ctx: BillingRequestContext = Depends(require_org),
    app: BillingApplication = Depends(get_application),
) -> ObservationResponse:
    """An `OBSERVED_FACT` is refused unless it cites an available snapshot; a `HYPOTHESIS` is held
    to no such bar. The two get **different identities**, so resubmitting a hypothesis as a fact
    appends a second record rather than promoting the first."""
    return ObservationResponse.of(
        app.commands.dispatch(
            RecordObservation(
                actor_id=ctx.actor(),
                org_id=ctx.organization(),
                investigation_id=investigation_id,
                kind=body.kind,
                statement=body.statement,
                evidence_ids=tuple(body.evidence_ids),
            )
        )
    )


@router.post(
    "/{investigation_id}/settle-hypothesis",
    response_model=ObservationResponse,
    summary="Support or reject a hypothesis, naming the evidence",
)
def settle_hypothesis(
    investigation_id: str,
    body: SettleHypothesisBody,
    ctx: BillingRequestContext = Depends(require_org),
    app: BillingApplication = Depends(get_application),
) -> ObservationResponse:
    """The hypothesis itself is not rewritten — the verdict is appended beside it, so *what did we
    believe, and what changed our mind* survives in the record."""
    return ObservationResponse.of(
        app.commands.dispatch(
            SettleHypothesis(
                actor_id=ctx.actor(),
                org_id=ctx.organization(),
                investigation_id=investigation_id,
                observation_id=body.observation_id,
                supported=body.supported,
                evidence_ids=tuple(body.evidence_ids),
                reason=body.reason,
            )
        )
    )


@router.post(
    "/{investigation_id}/conclusion",
    response_model=ConclusionResponse,
    status_code=201,
    summary="Record the conclusion — CONFIRMED, INCONCLUSIVE, or a contradiction",
)
def record_conclusion(
    investigation_id: str,
    body: RecordConclusionBody,
    ctx: BillingRequestContext = Depends(require_org),
    app: BillingApplication = Depends(get_application),
) -> ConclusionResponse:
    """Requires elevated permission.

    `CONFIRMED` needs an available snapshot behind it; `INCONCLUSIVE` needs none and is always
    available, so nobody is ever pushed into naming a root cause to close a case. A
    `CONTRADICTS_INCIDENT_CLASSIFICATION` conclusion records a discrepancy for the owning M9.20
    workflow — **it does not reclassify the incident, and this endpoint cannot.**"""
    return ConclusionResponse.of(
        app.commands.dispatch(
            RecordConclusion(
                actor_id=ctx.actor(),
                org_id=ctx.organization(),
                investigation_id=investigation_id,
                classification=body.classification,
                statement=body.statement,
                evidence_ids=tuple(body.evidence_ids),
                evidenced_root_cause=body.evidenced_root_cause,
                supersedes=body.supersedes,
            )
        )
    )


# ── export ──────────────────────────────────────────────────────────────────────


@router.post(
    "/{investigation_id}/export",
    response_model=AuditPackageResponse,
    status_code=201,
    summary="Produce an immutable, content-addressed audit package",
)
def export(
    investigation_id: str,
    ctx: BillingRequestContext = Depends(require_org),
    app: BillingApplication = Depends(get_application),
) -> AuditPackageResponse:
    """Requires audit permission. **Deterministic**: exporting unchanged state twice returns the
    same package and writes nothing; any change produces a new package beside the old one.

    `completeness` is decided on evidence availability, never on optimism — an unreadable incident,
    an unavailable source or an unresolved required question each make it `INCOMPLETE`, and every
    gap is named."""
    return AuditPackageResponse.of(
        app.commands.dispatch(
            ExportAuditPackage(
                actor_id=ctx.actor(),
                org_id=ctx.organization(),
                investigation_id=investigation_id,
            )
        )
    )
