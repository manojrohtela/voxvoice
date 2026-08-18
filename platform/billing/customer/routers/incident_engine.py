"""Financial Integrity Correlation routes (M9.20) — transport only.

Additive to the M9.5–M9.19 transport layer and identical in discipline: parse, take identity from
the verified token, construct a command or query, dispatch, serialize. No correlation rule is
evaluated here — which findings share a cause, what anchor proved it, how severe the incident is,
what the root cause is and whether the evidence permits resolution are all Foundation behaviour
reached through the Application.

Mounted at **`/integrity`** alongside M9.19, under the reserved `incidents` segment. That is
deliberate rather than convenient: correlation and reconciliation are one bounded context — a
finding and the incident that groups it are the same subject at two altitudes — and splitting them
across prefixes would make the client join two trees for one screen.

The safety requirement is **path disjointness**, not prefix disjointness. Every route here begins
`/integrity/incidents`, a segment M9.19 does not use, and a test asserts that the two routers share
no path. `posture` and `search` are declared **before** `{incident_id}` so a literal segment is
never swallowed by the parameterised one — the same ordering M9.19 uses for `/findings/search`.

**Nine things no endpoint here does.** None correlates two findings — that happens in
`CorrelationService`. None decides a severity, a root cause or an impact. None calculates a price,
a rate, a tax, an invoice total, a recognized amount, a balance or a financial impact. **None posts
a ledger entry, and none can**: the only repair this surface can request is M9.19's own command,
which itself only asks an owning domain. None moves money or touches a provider. None changes a
dunning stage. None resolves an incident because a caller said so — `/resolve` re-reads every
attached finding and refuses unless all are settled and none was unreadable. None rewrites history:
a merge and a correction are new facts. And there is **no DELETE, PUT or PATCH anywhere in this
file** — historical financial evidence is not mutated.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query

from billing.application import (
    AcknowledgeIncident,
    BillingApplication,
    ClassifyIncidentRootCause,
    CloseIncident,
    CorrectCorrelation,
    CorrelateFindings,
    ExplainIncident,
    GetCorrelationCorrections,
    GetFindingIncidents,
    GetIncident,
    GetIncidentEvidence,
    GetIncidentFindings,
    GetIncidentMerges,
    GetIncidentPosture,
    GetIncidentRepairs,
    GetIncidentTimeline,
    InspectIncident,
    InvestigateIncident,
    ListIncidents,
    LiveIncidents,
    MergeIncidents,
    ObserveIncidentRepair,
    RequestIncidentRepair,
    ResolveIncident,
    SearchIncidents,
    VerifyIncident,
)

from ..auth import require_org
from ..container import get_application
from ..context import BillingRequestContext
from ..incident_schemas import (
    CorrectCorrelationBody,
    CorrelateFindingsBody,
    CorrelationCorrectionResponse,
    CorrelationReceiptResponse,
    EvidenceGraphResponse,
    FindingLinkResponse,
    IncidentEventResponse,
    IncidentExplanationResponse,
    IncidentInspectionResponse,
    IncidentMergeResponse,
    IncidentNoteBody,
    IncidentPageResponse,
    IncidentPostureResponse,
    IncidentReasonBody,
    IncidentRepairResponse,
    IntegrityIncidentResponse,
    MergeIncidentsBody,
    ObserveRepairBody,
    RequestIncidentRepairBody,
    ResolveIncidentBody,
    VerificationResponse,
)
from ..schemas import ERROR_RESPONSES

router = APIRouter(prefix="/integrity", tags=["integrity"], responses=ERROR_RESPONSES)


# ── correlation ─────────────────────────────────────────────────────────────────


@router.post(
    "/incidents/correlate",
    response_model=CorrelationReceiptResponse,
    status_code=201,
    summary="Correlate reconciliation findings into incidents",
)
def correlate(
    body: CorrelateFindingsBody,
    ctx: BillingRequestContext = Depends(require_org),
    app: BillingApplication = Depends(get_application),
) -> CorrelationReceiptResponse:
    """**The endpoint this milestone exists for.**

    Each finding's correlation identity is *derived* from immutable evidence — a ledger source, a
    document revision, a source fact — and never supplied by the caller. Idempotent: the same
    finding correlated twice attaches once, and `already_attached` reports it."""
    return CorrelationReceiptResponse.of(
        app.commands.dispatch(
            CorrelateFindings(
                actor_id=ctx.actor(),
                org_id=ctx.organization(),
                finding_ids=tuple(body.finding_ids),
                run_id=body.run_id,
                limit=body.limit,
            )
        )
    )


# ── collections (declared before `{incident_id}` so literals are not swallowed) ──


@router.get(
    "/incidents",
    response_model=IncidentPageResponse,
    summary="Incidents, paginated at the database",
)
def list_incidents(
    status: list[str] = Query(default=[]),
    severity: list[str] = Query(default=[]),
    account_id: str = "",
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    ctx: BillingRequestContext = Depends(require_org),
    app: BillingApplication = Depends(get_application),
) -> IncidentPageResponse:
    """`total` comes from a database count. Paging by fetching everything is not an option at this
    milestone's assumed scale."""
    return IncidentPageResponse.of(
        app.queries.dispatch(
            ListIncidents(
                actor_id=ctx.actor(),
                org_id=ctx.organization(),
                statuses=tuple(status),
                severities=tuple(severity),
                account_id=account_id,
                limit=limit,
                offset=offset,
            )
        )
    )


@router.get(
    "/incidents/posture",
    response_model=IncidentPostureResponse,
    summary="The org's incident picture — counts, never money",
)
def posture(
    limit: int = Query(default=500, ge=1, le=1000),
    ctx: BillingRequestContext = Depends(require_org),
    app: BillingApplication = Depends(get_application),
) -> IncidentPostureResponse:
    """Derived on every call. There is no stored posture and **no amount on this response**."""
    return IncidentPostureResponse.of(
        app.queries.dispatch(
            GetIncidentPosture(actor_id=ctx.actor(), org_id=ctx.organization(), limit=limit)
        )
    )


@router.get(
    "/incidents/live",
    response_model=list[IntegrityIncidentResponse],
    summary="Unsettled incidents, worst first",
)
def live(
    limit: int = Query(default=200, ge=1, le=500),
    ctx: BillingRequestContext = Depends(require_org),
    app: BillingApplication = Depends(get_application),
) -> list[IntegrityIncidentResponse]:
    """**The read an external scheduler polls.** Billing runs no scheduler — whoever decides when
    to look lives outside this plane, exactly as for `/integrity/findings`."""
    return [
        IntegrityIncidentResponse.of(i)
        for i in app.queries.dispatch(
            LiveIncidents(actor_id=ctx.actor(), org_id=ctx.organization(), limit=limit)
        )
    ]


@router.get(
    "/incidents/search", response_model=IncidentPageResponse, summary="Search incidents"
)
def search(
    status: list[str] = Query(default=[]),
    severity: list[str] = Query(default=[]),
    root_cause: list[str] = Query(default=[]),
    impact: list[str] = Query(default=[]),
    owner: list[str] = Query(default=[]),
    account_id: str = "",
    correlation_key: str = "",
    anchor_value: str = "",
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    ctx: BillingRequestContext = Depends(require_org),
    app: BillingApplication = Depends(get_application),
) -> IncidentPageResponse:
    return IncidentPageResponse.of(
        app.queries.dispatch(
            SearchIncidents(
                actor_id=ctx.actor(),
                org_id=ctx.organization(),
                statuses=tuple(status),
                severities=tuple(severity),
                root_causes=tuple(root_cause),
                impacts=tuple(impact),
                owners=tuple(owner),
                account_id=account_id,
                correlation_key=correlation_key,
                anchor_value=anchor_value,
                limit=limit,
                offset=offset,
            )
        )
    )


@router.get(
    "/incidents/for-finding/{finding_id}",
    response_model=list[FindingLinkResponse],
    summary="Every incident a finding was ever attached to, including detached ones",
)
def finding_incidents(
    finding_id: str,
    ctx: BillingRequestContext = Depends(require_org),
    app: BillingApplication = Depends(get_application),
) -> list[FindingLinkResponse]:
    """Detached links are returned deliberately — "this was once believed to belong here" is
    exactly what an auditor asks about after a correlation correction."""
    return [
        FindingLinkResponse.of(link)
        for link in app.queries.dispatch(
            GetFindingIncidents(
                actor_id=ctx.actor(), org_id=ctx.organization(), finding_id=finding_id
            )
        )
    ]


# ── one incident ────────────────────────────────────────────────────────────────


@router.get(
    "/incidents/{incident_id}",
    response_model=IntegrityIncidentResponse,
    summary="One incident",
)
def get_incident(
    incident_id: str,
    ctx: BillingRequestContext = Depends(require_org),
    app: BillingApplication = Depends(get_application),
) -> IntegrityIncidentResponse:
    return IntegrityIncidentResponse.of(
        app.queries.dispatch(
            GetIncident(
                actor_id=ctx.actor(), org_id=ctx.organization(), incident_id=incident_id
            )
        )
    )


@router.get(
    "/incidents/{incident_id}/timeline",
    response_model=list[IncidentEventResponse],
    summary="The audit trail — the source of explanation, not a log",
)
def timeline(
    incident_id: str,
    limit: int = Query(default=500, ge=1, le=1000),
    ctx: BillingRequestContext = Depends(require_org),
    app: BillingApplication = Depends(get_application),
) -> list[IncidentEventResponse]:
    """Answers what happened, when Billing first observed it, which run found it, which findings
    were attached, who acknowledged it, what repair was requested of whom, what evidence proved it,
    and when it was verified — all from persisted rows."""
    return [
        IncidentEventResponse.of(e)
        for e in app.queries.dispatch(
            GetIncidentTimeline(
                actor_id=ctx.actor(),
                org_id=ctx.organization(),
                incident_id=incident_id,
                limit=limit,
            )
        )
    ]


@router.get(
    "/incidents/{incident_id}/findings",
    response_model=list[FindingLinkResponse],
    summary="Which findings belong, and the anchor that proved each one",
)
def incident_findings(
    incident_id: str,
    ctx: BillingRequestContext = Depends(require_org),
    app: BillingApplication = Depends(get_application),
) -> list[FindingLinkResponse]:
    return [
        FindingLinkResponse.of(link)
        for link in app.queries.dispatch(
            GetIncidentFindings(
                actor_id=ctx.actor(), org_id=ctx.organization(), incident_id=incident_id
            )
        )
    ]


@router.get(
    "/incidents/{incident_id}/evidence",
    response_model=EvidenceGraphResponse,
    summary="The evidence graph — every edge carries the anchor that proved it",
)
def evidence(
    incident_id: str,
    ctx: BillingRequestContext = Depends(require_org),
    app: BillingApplication = Depends(get_application),
) -> EvidenceGraphResponse:
    """A flat list says *these five things are wrong*. A graph says *because they all name this one
    ledger entry* — the difference between a queue and an explanation."""
    return EvidenceGraphResponse.of(
        app.queries.dispatch(
            GetIncidentEvidence(
                actor_id=ctx.actor(), org_id=ctx.organization(), incident_id=incident_id
            )
        )
    )


@router.get(
    "/incidents/{incident_id}/explain",
    response_model=IncidentExplanationResponse,
    summary="Why does this incident exist?",
)
def explain(
    incident_id: str,
    ctx: BillingRequestContext = Depends(require_org),
    app: BillingApplication = Depends(get_application),
) -> IncidentExplanationResponse:
    """Assembled entirely from stored evidence. **No domain is re-read** — by the time an auditor
    opens a six-month-old incident the payment has settled and the period has closed."""
    return IncidentExplanationResponse.of(
        app.queries.dispatch(
            ExplainIncident(
                actor_id=ctx.actor(), org_id=ctx.organization(), incident_id=incident_id
            )
        )
    )


@router.get(
    "/incidents/{incident_id}/inspect",
    response_model=IncidentInspectionResponse,
    summary="Is this incident counted correctly, scoped, connected and monotonic?",
)
def inspect(
    incident_id: str,
    ctx: BillingRequestContext = Depends(require_org),
    app: BillingApplication = Depends(get_application),
) -> IncidentInspectionResponse:
    return IncidentInspectionResponse.of(
        app.queries.dispatch(
            InspectIncident(
                actor_id=ctx.actor(), org_id=ctx.organization(), incident_id=incident_id
            )
        )
    )


@router.get(
    "/incidents/{incident_id}/repairs",
    response_model=list[IncidentRepairResponse],
    summary="What has been asked of which owning domain",
)
def repairs(
    incident_id: str,
    ctx: BillingRequestContext = Depends(require_org),
    app: BillingApplication = Depends(get_application),
) -> list[IncidentRepairResponse]:
    return [
        IncidentRepairResponse.of(r)
        for r in app.queries.dispatch(
            GetIncidentRepairs(
                actor_id=ctx.actor(), org_id=ctx.organization(), incident_id=incident_id
            )
        )
    ]


@router.get(
    "/incidents/{incident_id}/merges",
    response_model=list[IncidentMergeResponse],
    summary="Merge decisions touching this incident",
)
def merges(
    incident_id: str,
    ctx: BillingRequestContext = Depends(require_org),
    app: BillingApplication = Depends(get_application),
) -> list[IncidentMergeResponse]:
    return [
        IncidentMergeResponse.of(m)
        for m in app.queries.dispatch(
            GetIncidentMerges(
                actor_id=ctx.actor(), org_id=ctx.organization(), incident_id=incident_id
            )
        )
    ]


@router.get(
    "/incidents/{incident_id}/corrections",
    response_model=list[CorrelationCorrectionResponse],
    summary="Correlation corrections — what was believed, and why it changed",
)
def corrections(
    incident_id: str,
    ctx: BillingRequestContext = Depends(require_org),
    app: BillingApplication = Depends(get_application),
) -> list[CorrelationCorrectionResponse]:
    return [
        CorrelationCorrectionResponse.of(c)
        for c in app.queries.dispatch(
            GetCorrelationCorrections(
                actor_id=ctx.actor(), org_id=ctx.organization(), incident_id=incident_id
            )
        )
    ]


# ── lifecycle ───────────────────────────────────────────────────────────────────


@router.post(
    "/incidents/{incident_id}/acknowledge",
    response_model=IntegrityIncidentResponse,
    summary="Somebody has seen it",
)
def acknowledge(
    incident_id: str,
    body: IncidentNoteBody,
    ctx: BillingRequestContext = Depends(require_org),
    app: BillingApplication = Depends(get_application),
) -> IntegrityIncidentResponse:
    return IntegrityIncidentResponse.of(
        app.commands.dispatch(
            AcknowledgeIncident(
                actor_id=ctx.actor(),
                org_id=ctx.organization(),
                incident_id=incident_id,
                note=body.note,
            )
        )
    )


@router.post(
    "/incidents/{incident_id}/investigate",
    response_model=IntegrityIncidentResponse,
    summary="Start investigating",
)
def investigate(
    incident_id: str,
    body: IncidentNoteBody,
    ctx: BillingRequestContext = Depends(require_org),
    app: BillingApplication = Depends(get_application),
) -> IntegrityIncidentResponse:
    return IntegrityIncidentResponse.of(
        app.commands.dispatch(
            InvestigateIncident(
                actor_id=ctx.actor(),
                org_id=ctx.organization(),
                incident_id=incident_id,
                note=body.note,
            )
        )
    )


@router.post(
    "/incidents/{incident_id}/classify",
    response_model=IntegrityIncidentResponse,
    summary="Re-derive root cause and impact from the attached findings",
)
def classify(
    incident_id: str,
    ctx: BillingRequestContext = Depends(require_org),
    app: BillingApplication = Depends(get_application),
) -> IntegrityIncidentResponse:
    """**Derivation, not assertion.** There is no body: a caller-supplied root cause would be
    exactly the invented fact this milestone forbids."""
    return IntegrityIncidentResponse.of(
        app.commands.dispatch(
            ClassifyIncidentRootCause(
                actor_id=ctx.actor(), org_id=ctx.organization(), incident_id=incident_id
            )
        )
    )


@router.post(
    "/incidents/{incident_id}/request-repair",
    response_model=IncidentRepairResponse,
    status_code=201,
    summary="Ask the owning domain to fix the underlying fact",
)
def request_repair(
    incident_id: str,
    body: RequestIncidentRepairBody,
    ctx: BillingRequestContext = Depends(require_org),
    app: BillingApplication = Depends(get_application),
) -> IncidentRepairResponse:
    """**M9.20 performs nothing.** The request goes through M9.19's own repair command, which
    itself only asks an owning domain — so this surface is two removes from any financial
    mutation. Acceptance is not evidence the incident is resolved; only verification is."""
    return IncidentRepairResponse.of(
        app.commands.dispatch(
            RequestIncidentRepair(
                actor_id=ctx.actor(),
                org_id=ctx.organization(),
                incident_id=incident_id,
                finding_id=body.finding_id,
                request_key=body.request_key,
                note=body.note,
            )
        )
    )


@router.post(
    "/incidents/{incident_id}/observe-repair",
    response_model=IncidentRepairResponse,
    summary="Record that the owning domain reported back",
)
def observe_repair(
    incident_id: str,
    body: ObserveRepairBody,
    ctx: BillingRequestContext = Depends(require_org),
    app: BillingApplication = Depends(get_application),
) -> IncidentRepairResponse:
    """**Still not evidence of resolution.** The lifecycle has no edge from repair straight to
    resolved; the only path runs through verification."""
    return IncidentRepairResponse.of(
        app.commands.dispatch(
            ObserveIncidentRepair(
                actor_id=ctx.actor(),
                org_id=ctx.organization(),
                incident_id=incident_id,
                repair_id=body.repair_id,
                note=body.note,
            )
        )
    )


@router.post(
    "/incidents/{incident_id}/verify",
    response_model=VerificationResponse,
    summary="Re-read every attached finding and record what the books say now",
)
def verify(
    incident_id: str,
    body: IncidentNoteBody,
    ctx: BillingRequestContext = Depends(require_org),
    app: BillingApplication = Depends(get_application),
) -> VerificationResponse:
    """Verification can legitimately fail, and when it does the incident goes back to investigating
    rather than sitting in a state that implies progress."""
    return VerificationResponse.of(
        app.commands.dispatch(
            VerifyIncident(
                actor_id=ctx.actor(),
                org_id=ctx.organization(),
                incident_id=incident_id,
                note=body.note,
            )
        )
    )


@router.post(
    "/incidents/{incident_id}/resolve",
    response_model=IntegrityIncidentResponse,
    summary="Close an incident with evidence",
)
def resolve(
    incident_id: str,
    body: ResolveIncidentBody,
    ctx: BillingRequestContext = Depends(require_org),
    app: BillingApplication = Depends(get_application),
) -> IntegrityIncidentResponse:
    """**Not a checkbox.** The server re-reads every attached finding and refuses unless all are
    settled and none was unreadable — partial resolution must never masquerade as complete. There
    is no `force` flag."""
    return IntegrityIncidentResponse.of(
        app.commands.dispatch(
            ResolveIncident(
                actor_id=ctx.actor(),
                org_id=ctx.organization(),
                incident_id=incident_id,
                note=body.note,
            )
        )
    )


@router.post(
    "/incidents/{incident_id}/close",
    response_model=IntegrityIncidentResponse,
    summary="Accept an incident as permanent — a decision, not a repair",
)
def close(
    incident_id: str,
    body: IncidentReasonBody,
    ctx: BillingRequestContext = Depends(require_org),
    app: BillingApplication = Depends(get_application),
) -> IntegrityIncidentResponse:
    """Separate from resolve deliberately: resolved means the books agree, closed means somebody
    decided to live with it."""
    return IntegrityIncidentResponse.of(
        app.commands.dispatch(
            CloseIncident(
                actor_id=ctx.actor(),
                org_id=ctx.organization(),
                incident_id=incident_id,
                reason=body.reason,
            )
        )
    )


@router.post(
    "/incidents/{incident_id}/merge",
    response_model=IntegrityIncidentResponse,
    status_code=201,
    summary="Merge another incident into this one — a new fact, not a rewrite",
)
def merge(
    incident_id: str,
    body: MergeIncidentsBody,
    ctx: BillingRequestContext = Depends(require_org),
    app: BillingApplication = Depends(get_application),
) -> IntegrityIncidentResponse:
    """**Neither original is rewritten.** The merged incident keeps its severity, its findings and
    its whole history, and stays readable forever — it simply gains a canonical reference."""
    return IntegrityIncidentResponse.of(
        app.commands.dispatch(
            MergeIncidents(
                actor_id=ctx.actor(),
                org_id=ctx.organization(),
                canonical_id=incident_id,
                merged_id=body.merged_id,
                reason=body.reason,
            )
        )
    )


@router.post(
    "/incidents/{incident_id}/correct-correlation",
    response_model=IntegrityIncidentResponse,
    status_code=201,
    summary="Correct a wrong correlation — a new fact, not a rewrite",
)
def correct(
    incident_id: str,
    body: CorrectCorrelationBody,
    ctx: BillingRequestContext = Depends(require_org),
    app: BillingApplication = Depends(get_application),
) -> IntegrityIncidentResponse:
    """The old link is **detached, never deleted**, and a correction row records the previous key,
    the corrected key and the reason. A correction that would change nothing is refused rather than
    silently detaching the finding from everything."""
    return IntegrityIncidentResponse.of(
        app.commands.dispatch(
            CorrectCorrelation(
                actor_id=ctx.actor(),
                org_id=ctx.organization(),
                incident_id=incident_id,
                finding_id=body.finding_id,
                reason=body.reason,
            )
        )
    )
