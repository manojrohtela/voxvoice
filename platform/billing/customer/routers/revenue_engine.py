"""Revenue Recognition routes (M9.18) — transport only.

Additive to the M9.5–M9.17 transport layer and identical in discipline: parse, take identity from
the verified token, construct a command or query, dispatch, serialize. No recognition rule is
evaluated here — when an amount is earned, how it is split across periods, where a remainder goes,
which policy version applies and whether a period has matured are all Foundation behaviour reached
through the Application.

Mounted at **`/revenue`**, chosen by inspection: `/accounting` and `/ledger` are M9.13's,
`/invoices` and `/documents` are M9.14's, `/collections` is M9.16's and `/recovery` is M9.17's.
Two routers under one prefix shadow each other by registration order, and the one that loses does so
silently.

**Seven things no endpoint here does.** None calculates a recognition amount — every figure is the
server's, allocated by `Money.allocate`. None allocates a remainder. None decides a schedule or
chooses a policy. **None posts a ledger entry**: recognition dispatches M9.13's own command and the
Ledger decides the period, the entry id and the duplicate. None reads a payment or a collection
state — paid is not earned. None calculates tax. And there is **no DELETE anywhere in this file**:
financial history is corrected by a new fact, never removed.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query

from billing.application import (
    ActivateRevenueSchedule,
    BillingApplication,
    CancelRevenueSchedule,
    DeactivateRecognitionPolicy,
    DeferredRevenueSummary,
    DueRecognitions,
    ExplainRecognition,
    GetRecognitionEntry,
    GetRecognitionPolicyVersions,
    GetRecognitionPosition,
    GetRevenueSchedule,
    GetSchedulesForDocument,
    InspectRevenueSchedule,
    ListRecognitionAdjustments,
    ListRecognitionEntries,
    ListRecognitionPolicies,
    ListRevenueSchedules,
    PostRecognitionToLedger,
    PublishRecognitionPolicy,
    RecognitionTimeline,
    RecognizeRevenue,
    ReverseRecognition,
    ScheduleRevenue,
    SearchRevenueSchedules,
    SkipRecognitionEntry,
    SupersedeRevenueSchedule,
    UnpostedRecognitions,
)

from ..auth import require_org
from ..container import get_application
from ..context import BillingRequestContext
from ..revenue_schemas import (
    DeferredRevenueResponse,
    PublishRecognitionPolicyBody,
    RecognitionActivityResponse,
    RecognitionAdjustmentResponse,
    RecognitionEntryResponse,
    RecognitionExplanationResponse,
    RecognitionInspectionResponse,
    RecognitionPolicyResponse,
    RecognitionPositionResponse,
    RecognitionReasonBody,
    RecognitionReceiptResponse,
    RecognizeRevenueBody,
    RevenueScheduleResponse,
    RevenueSearchResponse,
    ReverseRecognitionBody,
    ScheduleRevenueBody,
    SupersedeScheduleBody,
)
from ..schemas import ERROR_RESPONSES

router = APIRouter(prefix="/revenue", tags=["revenue"], responses=ERROR_RESPONSES)


# ── policy: immutable, versioned ────────────────────────────────────────────────


@router.post(
    "/policies",
    response_model=RecognitionPolicyResponse,
    status_code=201,
    summary="Publish a recognition policy version (immutable — there is no update)",
)
def publish_policy(
    body: PublishRecognitionPolicyBody,
    ctx: BillingRequestContext = Depends(require_org),
    app: BillingApplication = Depends(get_application),
) -> RecognitionPolicyResponse:
    """Publishing the same version twice is idempotent. Publishing a *different* body under the
    same version is refused by the database, because a live schedule was judged by that body."""
    return RecognitionPolicyResponse.of(
        app.commands.dispatch(
            PublishRecognitionPolicy(
                actor_id=ctx.actor(),
                org_id=ctx.organization(),
                code=body.code,
                version=body.version,
                method=body.method,
                granularity=body.granularity,
                remainder=body.remainder,
                description=body.description,
            )
        )
    )


@router.get(
    "/policies", response_model=list[RecognitionPolicyResponse], summary="List policy versions"
)
def list_policies(
    include_inactive: bool = False,
    ctx: BillingRequestContext = Depends(require_org),
    app: BillingApplication = Depends(get_application),
) -> list[RecognitionPolicyResponse]:
    return [
        RecognitionPolicyResponse.of(p)
        for p in app.queries.dispatch(
            ListRecognitionPolicies(
                actor_id=ctx.actor(),
                org_id=ctx.organization(),
                include_inactive=include_inactive,
            )
        )
    ]


@router.get(
    "/policies/{code}/versions",
    response_model=list[RecognitionPolicyResponse],
    summary="Every version of one policy, oldest first",
)
def policy_versions(
    code: str,
    ctx: BillingRequestContext = Depends(require_org),
    app: BillingApplication = Depends(get_application),
) -> list[RecognitionPolicyResponse]:
    """The audit answer to "under what method was this revenue recognized in March?"."""
    return [
        RecognitionPolicyResponse.of(p)
        for p in app.queries.dispatch(
            GetRecognitionPolicyVersions(
                actor_id=ctx.actor(), org_id=ctx.organization(), code=code
            )
        )
    ]


@router.post(
    "/policies/{policy_id}/deactivate",
    response_model=RecognitionPolicyResponse,
    summary="Withdraw a version from future binding (the row stays readable forever)",
)
def deactivate_policy(
    policy_id: str,
    body: RecognitionReasonBody,
    ctx: BillingRequestContext = Depends(require_org),
    app: BillingApplication = Depends(get_application),
) -> RecognitionPolicyResponse:
    return RecognitionPolicyResponse.of(
        app.commands.dispatch(
            DeactivateRecognitionPolicy(
                actor_id=ctx.actor(),
                org_id=ctx.organization(),
                policy_id=policy_id,
                reason=body.reason,
            )
        )
    )


# ── schedules ───────────────────────────────────────────────────────────────────


@router.post(
    "/schedules",
    response_model=RevenueScheduleResponse,
    status_code=201,
    summary="Derive a recognition schedule from an issued document (idempotent on the basis)",
)
def schedule_revenue(
    body: ScheduleRevenueBody,
    ctx: BillingRequestContext = Depends(require_org),
    app: BillingApplication = Depends(get_application),
) -> RevenueScheduleResponse:
    """The amount comes from the snapshot the document pinned at issue — this endpoint takes no
    amount and cannot be used to recognize a figure nobody billed.

    Idempotent on the **basis**: same document revision, window, method and policy version derive
    the same schedule. Change any of them and it is a different schedule, never a mutated one."""
    return RevenueScheduleResponse.of(
        app.commands.dispatch(
            ScheduleRevenue(
                actor_id=ctx.actor(),
                org_id=ctx.organization(),
                document_id=body.document_id,
                service_start=body.service_start,
                service_end=body.service_end,
                policy_code=body.policy_code,
                method=body.method,
                milestones=tuple(tuple(m) for m in body.milestones),
                subscription_id=body.subscription_id,
            )
        )
    )


@router.get(
    "/schedules", response_model=list[RevenueScheduleResponse], summary="List revenue schedules"
)
def list_schedules(
    account_id: str = "",
    state: list[str] = Query(default=[]),
    limit: int = Query(default=200, ge=1, le=500),
    ctx: BillingRequestContext = Depends(require_org),
    app: BillingApplication = Depends(get_application),
) -> list[RevenueScheduleResponse]:
    return [
        RevenueScheduleResponse.of(s)
        for s in app.queries.dispatch(
            ListRevenueSchedules(
                actor_id=ctx.actor(),
                org_id=ctx.organization(),
                account_id=account_id,
                states=tuple(state),
                limit=limit,
            )
        )
    ]


@router.get(
    "/schedules/search", response_model=RevenueSearchResponse, summary="Search revenue schedules"
)
def search_schedules(
    account_id: str = "",
    document_id: str = "",
    state: list[str] = Query(default=[]),
    limit: int = Query(default=100, ge=1, le=500),
    ctx: BillingRequestContext = Depends(require_org),
    app: BillingApplication = Depends(get_application),
) -> RevenueSearchResponse:
    return RevenueSearchResponse.of(
        app.queries.dispatch(
            SearchRevenueSchedules(
                actor_id=ctx.actor(),
                org_id=ctx.organization(),
                account_id=account_id,
                document_id=document_id,
                states=tuple(state),
                limit=limit,
            )
        )
    )


@router.get(
    "/schedules/for-document",
    response_model=list[RevenueScheduleResponse],
    summary="Every schedule derived from one document — the supersession trail",
)
def schedules_for_document(
    document_id: str,
    ctx: BillingRequestContext = Depends(require_org),
    app: BillingApplication = Depends(get_application),
) -> list[RevenueScheduleResponse]:
    """A revised document produces a *new* schedule rather than editing the old one, so this is
    where "what did we recognize before the correction?" is answered."""
    return [
        RevenueScheduleResponse.of(s)
        for s in app.queries.dispatch(
            GetSchedulesForDocument(
                actor_id=ctx.actor(), org_id=ctx.organization(), document_id=document_id
            )
        )
    ]


@router.get(
    "/schedules/{schedule_id}", response_model=RevenueScheduleResponse, summary="One schedule"
)
def get_schedule(
    schedule_id: str,
    ctx: BillingRequestContext = Depends(require_org),
    app: BillingApplication = Depends(get_application),
) -> RevenueScheduleResponse:
    return RevenueScheduleResponse.of(
        app.queries.dispatch(
            GetRevenueSchedule(
                actor_id=ctx.actor(), org_id=ctx.organization(), schedule_id=schedule_id
            )
        )
    )


@router.post(
    "/schedules/{schedule_id}/activate",
    response_model=RevenueScheduleResponse,
    summary="Make a built schedule recognizable",
)
def activate_schedule(
    schedule_id: str,
    ctx: BillingRequestContext = Depends(require_org),
    app: BillingApplication = Depends(get_application),
) -> RevenueScheduleResponse:
    return RevenueScheduleResponse.of(
        app.commands.dispatch(
            ActivateRevenueSchedule(
                actor_id=ctx.actor(), org_id=ctx.organization(), schedule_id=schedule_id
            )
        )
    )


@router.post(
    "/schedules/{schedule_id}/supersede",
    response_model=RevenueScheduleResponse,
    summary="The document was revised — what was already recognized stands",
)
def supersede_schedule(
    schedule_id: str,
    body: SupersedeScheduleBody,
    ctx: BillingRequestContext = Depends(require_org),
    app: BillingApplication = Depends(get_application),
) -> RevenueScheduleResponse:
    """The old schedule stops and keeps its recognized entries exactly as they are. Nothing is
    recomputed, and no recognized amount is withdrawn — that would be a reversal, not a revision."""
    return RevenueScheduleResponse.of(
        app.commands.dispatch(
            SupersedeRevenueSchedule(
                actor_id=ctx.actor(),
                org_id=ctx.organization(),
                schedule_id=schedule_id,
                successor_schedule_id=body.successor_schedule_id,
                reason=body.reason,
            )
        )
    )


@router.post(
    "/schedules/{schedule_id}/cancel",
    response_model=RevenueScheduleResponse,
    summary="The document was cancelled before anything was earned (refused once revenue exists)",
)
def cancel_schedule(
    schedule_id: str,
    body: RecognitionReasonBody,
    ctx: BillingRequestContext = Depends(require_org),
    app: BillingApplication = Depends(get_application),
) -> RevenueScheduleResponse:
    """Once revenue has been recognized the way back is a **reversal** — a new financial fact with
    provenance — never a cancellation that would erase it."""
    return RevenueScheduleResponse.of(
        app.commands.dispatch(
            CancelRevenueSchedule(
                actor_id=ctx.actor(),
                org_id=ctx.organization(),
                schedule_id=schedule_id,
                reason=body.reason,
            )
        )
    )


# ── entries and evidence ────────────────────────────────────────────────────────


@router.get(
    "/entries/due",
    response_model=list[RecognitionEntryResponse],
    summary="Periods that have matured (a read — Billing runs no scheduler)",
)
def due_entries(
    at: float | None = None,
    limit: int = Query(default=200, ge=1, le=500),
    ctx: BillingRequestContext = Depends(require_org),
    app: BillingApplication = Depends(get_application),
) -> list[RecognitionEntryResponse]:
    """Whoever calls this on a timer lives outside Billing. The engine answers what has matured; it
    does not decide when to be asked, and it runs no loop of its own."""
    return [
        RecognitionEntryResponse.of(e)
        for e in app.queries.dispatch(
            DueRecognitions(
                actor_id=ctx.actor(), org_id=ctx.organization(), at=at, limit=limit
            )
        )
    ]


@router.get(
    "/entries/unposted",
    response_model=list[RecognitionEntryResponse],
    summary="Revenue recognized that the Ledger has not accepted — the repair queue",
)
def unposted_entries(
    limit: int = Query(default=200, ge=1, le=500),
    ctx: BillingRequestContext = Depends(require_org),
    app: BillingApplication = Depends(get_application),
) -> list[RecognitionEntryResponse]:
    """A non-empty list here is the one number an operator must never ignore: the decision is a
    fact and the financial record is missing."""
    return [
        RecognitionEntryResponse.of(e)
        for e in app.queries.dispatch(
            UnpostedRecognitions(actor_id=ctx.actor(), org_id=ctx.organization(), limit=limit)
        )
    ]


@router.get("/entries/{entry_id}", response_model=RecognitionEntryResponse, summary="One period")
def get_entry(
    entry_id: str,
    ctx: BillingRequestContext = Depends(require_org),
    app: BillingApplication = Depends(get_application),
) -> RecognitionEntryResponse:
    return RecognitionEntryResponse.of(
        app.queries.dispatch(
            GetRecognitionEntry(
                actor_id=ctx.actor(), org_id=ctx.organization(), entry_id=entry_id
            )
        )
    )


@router.post(
    "/entries/{entry_id}/recognize",
    response_model=RecognitionReceiptResponse,
    summary="Recognize one matured period and post the financial fact",
)
def recognize(
    entry_id: str,
    body: RecognizeRevenueBody,
    ctx: BillingRequestContext = Depends(require_org),
    app: BillingApplication = Depends(get_application),
) -> RecognitionReceiptResponse:
    """Two transactions: the decision commits first, then the posting. If the Ledger refuses — no
    open accounting period — the recognition survives as `unposted` and the receipt says so."""
    return RecognitionReceiptResponse.of(
        app.commands.dispatch(
            RecognizeRevenue(
                actor_id=ctx.actor(),
                org_id=ctx.organization(),
                entry_id=entry_id,
                at=body.at,
            )
        )
    )


@router.post(
    "/entries/{entry_id}/post",
    response_model=RecognitionReceiptResponse,
    summary="Repair a recognition whose ledger posting never landed",
)
def post_to_ledger(
    entry_id: str,
    ctx: BillingRequestContext = Depends(require_org),
    app: BillingApplication = Depends(get_application),
) -> RecognitionReceiptResponse:
    """Idempotent: the Ledger derives one entry id per source, so a repeated repair is a recorded
    duplicate rather than a second financial fact."""
    return RecognitionReceiptResponse.of(
        app.commands.dispatch(
            PostRecognitionToLedger(
                actor_id=ctx.actor(), org_id=ctx.organization(), entry_id=entry_id
            )
        )
    )


@router.post(
    "/entries/{entry_id}/reverse",
    response_model=RecognitionAdjustmentResponse,
    status_code=201,
    summary="Correct a recognized amount with a new fact",
)
def reverse(
    entry_id: str,
    body: ReverseRecognitionBody,
    ctx: BillingRequestContext = Depends(require_org),
    app: BillingApplication = Depends(get_application),
) -> RecognitionAdjustmentResponse:
    """**The original entry is never edited.** The adjustment carries full provenance: which
    recognition, why, from which document revision, under which policy version, effective when."""
    return RecognitionAdjustmentResponse.of(
        app.commands.dispatch(
            ReverseRecognition(
                actor_id=ctx.actor(),
                org_id=ctx.organization(),
                entry_id=entry_id,
                kind=body.kind,
                reason=body.reason,
                request_key=body.request_key,
                amount_minor=body.amount_minor,
            )
        )
    )


@router.post(
    "/entries/{entry_id}/skip",
    response_model=RecognitionEntryResponse,
    summary="Retire a pending period that will never be recognized",
)
def skip_entry(
    entry_id: str,
    body: RecognitionReasonBody,
    ctx: BillingRequestContext = Depends(require_org),
    app: BillingApplication = Depends(get_application),
) -> RecognitionEntryResponse:
    return RecognitionEntryResponse.of(
        app.commands.dispatch(
            SkipRecognitionEntry(
                actor_id=ctx.actor(),
                org_id=ctx.organization(),
                entry_id=entry_id,
                reason=body.reason,
            )
        )
    )


@router.get(
    "/schedules/{schedule_id}/entries",
    response_model=list[RecognitionEntryResponse],
    summary="Every period on a schedule, in order",
)
def list_entries(
    schedule_id: str,
    ctx: BillingRequestContext = Depends(require_org),
    app: BillingApplication = Depends(get_application),
) -> list[RecognitionEntryResponse]:
    return [
        RecognitionEntryResponse.of(e)
        for e in app.queries.dispatch(
            ListRecognitionEntries(
                actor_id=ctx.actor(), org_id=ctx.organization(), schedule_id=schedule_id
            )
        )
    ]


@router.get(
    "/schedules/{schedule_id}/adjustments",
    response_model=list[RecognitionAdjustmentResponse],
    summary="Corrections, append-only",
)
def list_adjustments(
    schedule_id: str,
    entry_id: str = "",
    ctx: BillingRequestContext = Depends(require_org),
    app: BillingApplication = Depends(get_application),
) -> list[RecognitionAdjustmentResponse]:
    return [
        RecognitionAdjustmentResponse.of(a)
        for a in app.queries.dispatch(
            ListRecognitionAdjustments(
                actor_id=ctx.actor(),
                org_id=ctx.organization(),
                schedule_id=schedule_id,
                entry_id=entry_id,
            )
        )
    ]


# ── derived reads ───────────────────────────────────────────────────────────────


@router.get(
    "/schedules/{schedule_id}/position",
    response_model=RecognitionPositionResponse,
    summary="Recognized and deferred — derived on this request, stored nowhere",
)
def position(
    schedule_id: str,
    ctx: BillingRequestContext = Depends(require_org),
    app: BillingApplication = Depends(get_application),
) -> RecognitionPositionResponse:
    """`deferred` is `total − recognized − adjusted`, computed from the entries every time. There is
    no `deferred_balance` column anywhere in this milestone, deliberately."""
    return RecognitionPositionResponse.of(
        app.queries.dispatch(
            GetRecognitionPosition(
                actor_id=ctx.actor(), org_id=ctx.organization(), schedule_id=schedule_id
            )
        )
    )


@router.get(
    "/schedules/{schedule_id}/explain",
    response_model=RecognitionExplanationResponse,
    summary="Why this amount was recognized — assembled from immutable evidence",
)
def explain(
    schedule_id: str,
    ctx: BillingRequestContext = Depends(require_org),
    app: BillingApplication = Depends(get_application),
) -> RecognitionExplanationResponse:
    """Reaches no price book, no tax rate, no subscription and no payment. That is what makes it
    still answerable in five years, after all of those have changed."""
    return RecognitionExplanationResponse.of(
        app.queries.dispatch(
            ExplainRecognition(
                actor_id=ctx.actor(), org_id=ctx.organization(), schedule_id=schedule_id
            )
        )
    )


@router.get(
    "/schedules/{schedule_id}/inspect",
    response_model=RecognitionInspectionResponse,
    summary="A schedule with its position and counts",
)
def inspect(
    schedule_id: str,
    at: float | None = None,
    ctx: BillingRequestContext = Depends(require_org),
    app: BillingApplication = Depends(get_application),
) -> RecognitionInspectionResponse:
    return RecognitionInspectionResponse.of(
        app.queries.dispatch(
            InspectRevenueSchedule(
                actor_id=ctx.actor(),
                org_id=ctx.organization(),
                schedule_id=schedule_id,
                at=at,
            )
        )
    )


@router.get(
    "/schedules/{schedule_id}/timeline",
    response_model=list[RecognitionActivityResponse],
    summary="The audit trail an accountant reads",
)
def timeline(
    schedule_id: str,
    limit: int = Query(default=200, ge=1, le=500),
    ctx: BillingRequestContext = Depends(require_org),
    app: BillingApplication = Depends(get_application),
) -> list[RecognitionActivityResponse]:
    return [
        RecognitionActivityResponse.of(a)
        for a in app.queries.dispatch(
            RecognitionTimeline(
                actor_id=ctx.actor(),
                org_id=ctx.organization(),
                schedule_id=schedule_id,
                limit=limit,
            )
        )
    ]


@router.get(
    "/deferred",
    response_model=DeferredRevenueResponse,
    summary="Deferred revenue for an account — summed from the facts on this request",
)
def deferred(
    account_id: str = "",
    limit: int = Query(default=500, ge=1, le=1000),
    ctx: BillingRequestContext = Depends(require_org),
    app: BillingApplication = Depends(get_application),
) -> DeferredRevenueResponse:
    """Mixed currencies are not summed. Converting would make Billing hold a foreign-exchange
    opinion, and ADR-0023 RS5 says conversion is presentation, never ledger."""
    return DeferredRevenueResponse.of(
        app.queries.dispatch(
            DeferredRevenueSummary(
                actor_id=ctx.actor(),
                org_id=ctx.organization(),
                account_id=account_id,
                limit=limit,
            )
        )
    )
