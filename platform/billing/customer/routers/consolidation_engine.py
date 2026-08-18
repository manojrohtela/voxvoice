"""Period Scope & Consolidation routes (M9.23) — transport only.

Additive to the M9.5–M9.22 transport layer and identical in discipline: parse, take identity from
the verified token, construct a command or query, dispatch, serialize. No scope rule is evaluated
here — which accounts a mode includes, whether a source can answer for a period, whether a figure
may be aggregated and whether a run may finalize are all Foundation behaviour reached through the
Application.

Mounted at **`/consolidations`**, its own prefix, proven collision-free against every existing path.

**Twelve things no endpoint here does.** None posts a ledger entry, issues an invoice, determines
tax, executes a payment or recognizes revenue — and none can, because no repository that could is
reachable. None changes an account's parent or payer: M9.8's hierarchy is read through a contract
with no write method. None reconciles (M9.19), correlates (M9.20), investigates (M9.21) or files
(M9.22). None calculates a price, a rate, a tax component or a balance. **None converts a
currency**: a scope spanning currencies keeps one aggregate per currency, or refuses. None reports
a missing account or an unreadable source as zero — `/collect` pins an `UNAVAILABLE` binding with
`amount_minor: null` and a named exception. None guesses attribution. None finalizes around
validation: `/finalize` re-reads the verdict, the exceptions and the completeness inside the
transaction, and the lifecycle has no edge to `FINALIZED` except from `READY`. None edits a
finalized consolidation — `/correction` opens a new one beside it. And there is **no DELETE, PUT or
PATCH anywhere in this file**.

Static paths precede wildcards throughout, so `/consolidations/scopes` is never captured by
`/consolidations/{consolidation_id}`.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query

from billing.application import BillingApplication
from billing.application.consolidation_engine import (
    AcknowledgeConsolidationException,
    CollectConsolidationSources,
    CompleteCollection,
    FinalizeConsolidation,
    GetConsolidation,
    GetConsolidationPosture,
    GetConsolidationProvenance,
    GetConsolidationSnapshot,
    GetConsolidationTimeline,
    GetConsolidationValidation,
    GetScopeDefinition,
    GetScopeSnapshot,
    ListConsolidationExceptions,
    ListConsolidationPeriods,
    ListScopeDefinitions,
    ListScopeMembers,
    ListSourceBindings,
    OpenConsolidation,
    OpenConsolidationCorrection,
    OpenConsolidationPeriod,
    PublishScopeDefinition,
    RecollectConsolidation,
    ResolveScope,
    SearchConsolidations,
    ValidateConsolidation,
)
from billing.foundation import ConsolidationStatus, Currency

from ..auth import require_org
from ..consolidation_schemas import (
    CollectSourcesBody,
    ConsolidationEventResponse,
    ConsolidationExceptionResponse,
    ConsolidationNoteBody,
    ConsolidationPage,
    ConsolidationPeriodResponse,
    ConsolidationPostureResponse,
    ConsolidationProvenanceResponse,
    ConsolidationReasonBody,
    ConsolidationResponse,
    ConsolidationResultResponse,
    ConsolidationValidationResponse,
    OpenConsolidationBody,
    OpenConsolidationPeriodBody,
    PublishScopeDefinitionBody,
    ResolveScopeBody,
    ScopeDefinitionResponse,
    ScopeMemberPage,
    ScopeMemberResponse,
    ScopeSnapshotResponse,
    SourceBindingPage,
    SourceBindingResponse,
)
from ..container import get_application
from ..context import BillingRequestContext
from ..schemas import ERROR_RESPONSES

router = APIRouter(
    prefix="/consolidations", tags=["consolidations"], responses=ERROR_RESPONSES
)


# ── scopes ──────────────────────────────────────────────────────────────────────


@router.post(
    "/scopes",
    response_model=ScopeDefinitionResponse,
    status_code=201,
    summary="Publish a scope rule at a version",
)
def publish_scope(
    body: PublishScopeDefinitionBody,
    ctx: BillingRequestContext = Depends(require_org),
    app: BillingApplication = Depends(get_application),
) -> ScopeDefinitionResponse:
    """**Immutable once published.** Re-publishing the same version with different content is
    refused: consolidations already bound to it must keep covering the accounts they covered, and a
    rule that changes gets a new version."""
    return ScopeDefinitionResponse.of(
        app.commands.dispatch(
            PublishScopeDefinition(
                actor_id=ctx.actor(),
                org_id=ctx.organization(),
                scope_code=body.scope_code,
                version=body.version,
                mode=body.mode,
                title=body.title,
                anchor_account_id=body.anchor_account_id,
                member_account_ids=tuple(body.member_account_ids),
                excluded_account_ids=tuple(body.excluded_account_ids),
                currency_mode=body.currency_mode,
                declared_currency=(
                    Currency(body.declared_currency) if body.declared_currency else None
                ),
                period_basis=body.period_basis,
                required_domains=tuple(body.required_domains),
                integrity_blocks_finalization=body.integrity_blocks_finalization,
            )
        )
    )


@router.get(
    "/scopes",
    response_model=list[ScopeDefinitionResponse],
    summary="Published scope rules, newest version first",
)
def list_scopes(
    limit: int = Query(default=50, ge=1, le=200),
    ctx: BillingRequestContext = Depends(require_org),
    app: BillingApplication = Depends(get_application),
) -> list[ScopeDefinitionResponse]:
    return [
        ScopeDefinitionResponse.of(d)
        for d in app.queries.dispatch(
            ListScopeDefinitions(actor_id=ctx.actor(), org_id=ctx.organization(), limit=limit)
        )
    ]


@router.post(
    "/scopes/resolve",
    response_model=ScopeSnapshotResponse,
    status_code=201,
    summary="Resolve a scope into an immutable membership",
)
def resolve_scope(
    body: ResolveScopeBody,
    ctx: BillingRequestContext = Depends(require_org),
    app: BillingApplication = Depends(get_application),
) -> ScopeSnapshotResponse:
    """**The snapshot is the point.** A reporting scope must not depend on today's hierarchy: an
    account that moves parent in March must not change what January's consolidation covered. This
    resolves the hierarchy once, records the account revisions it saw, and never looks again.

    Idempotent by identity — resolving an unchanged hierarchy twice returns the same snapshot."""
    return ScopeSnapshotResponse.of(
        app.commands.dispatch(
            ResolveScope(
                actor_id=ctx.actor(),
                org_id=ctx.organization(),
                scope_definition_id=body.scope_definition_id,
            )
        )
    )


@router.get(
    "/scopes/snapshots/{snapshot_id}",
    response_model=ScopeSnapshotResponse,
    summary="One resolved membership",
)
def get_snapshot(
    snapshot_id: str,
    ctx: BillingRequestContext = Depends(require_org),
    app: BillingApplication = Depends(get_application),
) -> ScopeSnapshotResponse:
    return ScopeSnapshotResponse.of(
        app.queries.dispatch(
            GetScopeSnapshot(
                actor_id=ctx.actor(),
                org_id=ctx.organization(),
                scope_snapshot_id=snapshot_id,
            )
        )
    )


@router.get(
    "/scopes/snapshots/{snapshot_id}/members",
    response_model=ScopeMemberPage,
    summary="The accounts in a resolved scope, paginated",
)
def list_members(
    snapshot_id: str,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=200),
    ctx: BillingRequestContext = Depends(require_org),
    app: BillingApplication = Depends(get_application),
) -> ScopeMemberPage:
    """**Paginated at the database.** A hundred-thousand-account scope shows fifty rows without
    loading a hundred thousand."""
    members, total = app.queries.dispatch(
        ListScopeMembers(
            actor_id=ctx.actor(),
            org_id=ctx.organization(),
            scope_snapshot_id=snapshot_id,
            page=page,
            page_size=page_size,
        )
    )
    return ScopeMemberPage(
        members=[ScopeMemberResponse.of(m) for m in members],
        total=total,
        page=page,
        page_size=page_size,
    )


@router.get(
    "/scopes/{scope_definition_id}",
    response_model=ScopeDefinitionResponse,
    summary="One scope rule, at one version",
)
def get_scope(
    scope_definition_id: str,
    ctx: BillingRequestContext = Depends(require_org),
    app: BillingApplication = Depends(get_application),
) -> ScopeDefinitionResponse:
    return ScopeDefinitionResponse.of(
        app.queries.dispatch(
            GetScopeDefinition(
                actor_id=ctx.actor(),
                org_id=ctx.organization(),
                scope_definition_id=scope_definition_id,
            )
        )
    )


# ── periods ─────────────────────────────────────────────────────────────────────


@router.post(
    "/periods",
    response_model=ConsolidationPeriodResponse,
    status_code=201,
    summary="Open a consolidation period under a declared basis",
)
def open_period(
    body: OpenConsolidationPeriodBody,
    ctx: BillingRequestContext = Depends(require_org),
    app: BillingApplication = Depends(get_application),
) -> ConsolidationPeriodResponse:
    """**The basis is inside the identity.** An accounting period is not a tax period is not a
    recognition period, and Billing already owns four of these — a consolidation names which one it
    consumes rather than assuming they coincide."""
    return ConsolidationPeriodResponse.of(
        app.commands.dispatch(
            OpenConsolidationPeriod(
                actor_id=ctx.actor(),
                org_id=ctx.organization(),
                basis=body.basis,
                period_key=body.period_key,
                start=body.start,
                end=body.end,
                aligned_source_id=body.aligned_source_id,
            )
        )
    )


@router.get(
    "/periods",
    response_model=list[ConsolidationPeriodResponse],
    summary="Consolidation periods, newest first",
)
def list_periods(
    limit: int = Query(default=50, ge=1, le=200),
    ctx: BillingRequestContext = Depends(require_org),
    app: BillingApplication = Depends(get_application),
) -> list[ConsolidationPeriodResponse]:
    return [
        ConsolidationPeriodResponse.of(p)
        for p in app.queries.dispatch(
            ListConsolidationPeriods(
                actor_id=ctx.actor(), org_id=ctx.organization(), limit=limit
            )
        )
    ]


# ── consolidations ──────────────────────────────────────────────────────────────


@router.post(
    "",
    response_model=ConsolidationResponse,
    status_code=201,
    summary="Open a consolidation over a scope snapshot and a period",
)
def open_consolidation(
    body: OpenConsolidationBody,
    ctx: BillingRequestContext = Depends(require_org),
    app: BillingApplication = Depends(get_application),
) -> ConsolidationResponse:
    """Idempotent by identity: the id derives from `(org, snapshot, period, corrects)`, so two
    workers opening the same consolidation land on one row rather than consolidating twice."""
    return ConsolidationResponse.of(
        app.commands.dispatch(
            OpenConsolidation(
                actor_id=ctx.actor(),
                org_id=ctx.organization(),
                scope_snapshot_id=body.scope_snapshot_id,
                period_id=body.period_id,
            )
        )
    )


@router.get(
    "",
    response_model=ConsolidationPage,
    summary="Consolidations, filtered and paginated",
)
def search(
    scope_definition_id: str = Query(default=""),
    period_key: str = Query(default=""),
    status: ConsolidationStatus | None = Query(default=None),
    corrections_only: bool = Query(default=False),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=25, ge=1, le=100),
    ctx: BillingRequestContext = Depends(require_org),
    app: BillingApplication = Depends(get_application),
) -> ConsolidationPage:
    runs, total, pages = app.queries.dispatch(
        SearchConsolidations(
            actor_id=ctx.actor(),
            org_id=ctx.organization(),
            scope_definition_id=scope_definition_id,
            period_key=period_key,
            statuses=(status,) if status else (),
            corrections_only=corrections_only,
            page=page,
            page_size=page_size,
        )
    )
    return ConsolidationPage(
        consolidations=[ConsolidationResponse.of(r) for r in runs], total=total, pages=pages
    )


@router.get(
    "/posture",
    response_model=ConsolidationPostureResponse,
    summary="Consolidation counts by status and completeness",
)
def posture(
    ctx: BillingRequestContext = Depends(require_org),
    app: BillingApplication = Depends(get_application),
) -> ConsolidationPostureResponse:
    """**Counts and labels, from database aggregates. No money.**"""
    return ConsolidationPostureResponse.of(
        app.queries.dispatch(
            GetConsolidationPosture(actor_id=ctx.actor(), org_id=ctx.organization())
        )
    )


@router.get(
    "/{consolidation_id}",
    response_model=ConsolidationResponse,
    summary="One consolidation",
)
def get_consolidation(
    consolidation_id: str,
    ctx: BillingRequestContext = Depends(require_org),
    app: BillingApplication = Depends(get_application),
) -> ConsolidationResponse:
    return ConsolidationResponse.of(
        app.queries.dispatch(
            GetConsolidation(
                actor_id=ctx.actor(),
                org_id=ctx.organization(),
                consolidation_id=consolidation_id,
            )
        )
    )


@router.post(
    "/{consolidation_id}/collect",
    response_model=ConsolidationResponse,
    summary="Bind one batch of member accounts' sources",
)
def collect(
    consolidation_id: str,
    body: CollectSourcesBody,
    ctx: BillingRequestContext = Depends(require_org),
    app: BillingApplication = Depends(get_application),
) -> ConsolidationResponse:
    """Called repeatedly until `collected_count` reaches `member_count`.

    Members are bound in account-id order after a stored cursor, so a restart resumes rather than
    re-reads or skips — and two callers racing on one batch write the same rows rather than
    double-counting. **A source that cannot be read is pinned as `unavailable` with no figure at
    all**, never as zero."""
    return ConsolidationResponse.of(
        app.commands.dispatch(
            CollectConsolidationSources(
                actor_id=ctx.actor(),
                org_id=ctx.organization(),
                consolidation_id=consolidation_id,
                batch_size=body.batch_size,
            )
        )
    )


@router.post(
    "/{consolidation_id}/complete-collection",
    response_model=ConsolidationResponse,
    summary="Close collection once every member is bound",
)
def complete_collection(
    consolidation_id: str,
    ctx: BillingRequestContext = Depends(require_org),
    app: BillingApplication = Depends(get_application),
) -> ConsolidationResponse:
    """Refused while any member is unbound: a partial scope is never treated as the whole."""
    return ConsolidationResponse.of(
        app.commands.dispatch(
            CompleteCollection(
                actor_id=ctx.actor(),
                org_id=ctx.organization(),
                consolidation_id=consolidation_id,
            )
        )
    )


@router.post(
    "/{consolidation_id}/validate",
    response_model=ConsolidationValidationResponse,
    summary="Validate a consolidation and aggregate its pinned sources",
)
def validate(
    consolidation_id: str,
    ctx: BillingRequestContext = Depends(require_org),
    app: BillingApplication = Depends(get_application),
) -> ConsolidationValidationResponse:
    """**Validation is not reconciliation.** Every check asks about the consolidation — is the
    membership pinned, did collection finish, is every required domain bound, is the currency
    handling coherent, are there blocking exceptions, is the integrity record settled. None
    compares two financial domains; that is M9.19's question and stays M9.19's."""
    return ConsolidationValidationResponse.of(
        app.commands.dispatch(
            ValidateConsolidation(
                actor_id=ctx.actor(),
                org_id=ctx.organization(),
                consolidation_id=consolidation_id,
            )
        )
    )


@router.post(
    "/{consolidation_id}/finalize",
    response_model=ConsolidationResultResponse,
    summary="Finalize a validated consolidation",
)
def finalize(
    consolidation_id: str,
    ctx: BillingRequestContext = Depends(require_org),
    app: BillingApplication = Depends(get_application),
) -> ConsolidationResultResponse:
    """**Reachable only from `READY`, and `READY` only from a validation that passed.** There is no
    override flag and no edge that reaches around validation. An acknowledged blocking exception
    still blocks — somebody having seen a gap is a different fact from the gap being closed."""
    return ConsolidationResultResponse.of(
        app.commands.dispatch(
            FinalizeConsolidation(
                actor_id=ctx.actor(),
                org_id=ctx.organization(),
                consolidation_id=consolidation_id,
            )
        )
    )


@router.post(
    "/{consolidation_id}/recollect",
    response_model=ConsolidationResponse,
    summary="Send a blocked or ready consolidation back to collection",
)
def recollect(
    consolidation_id: str,
    body: ConsolidationReasonBody,
    ctx: BillingRequestContext = Depends(require_org),
    app: BillingApplication = Depends(get_application),
) -> ConsolidationResponse:
    """Going back after the sources were fixed is honest. Staying blocked with a green badge would
    not be, and neither would an edge from `BLOCKED` straight to `READY`."""
    return ConsolidationResponse.of(
        app.commands.dispatch(
            RecollectConsolidation(
                actor_id=ctx.actor(),
                org_id=ctx.organization(),
                consolidation_id=consolidation_id,
                reason=body.reason,
            )
        )
    )


@router.post(
    "/{consolidation_id}/correction",
    response_model=ConsolidationResponse,
    status_code=201,
    summary="Open a consolidation that supersedes a finalized one",
)
def open_correction(
    consolidation_id: str,
    body: ConsolidationReasonBody,
    ctx: BillingRequestContext = Depends(require_org),
    app: BillingApplication = Depends(get_application),
) -> ConsolidationResponse:
    """**Both survive.** The original keeps every figure, its digest, its result and its finalized
    state, gaining only a back-reference. A correction whose scope and source revisions are both
    unmoved is refused — nothing has moved, so there is nothing to correct."""
    return ConsolidationResponse.of(
        app.commands.dispatch(
            OpenConsolidationCorrection(
                actor_id=ctx.actor(),
                org_id=ctx.organization(),
                consolidation_id=consolidation_id,
                reason=body.reason,
            )
        )
    )


@router.get(
    "/{consolidation_id}/sources",
    response_model=SourceBindingPage,
    summary="The pinned sources, paginated",
)
def list_sources(
    consolidation_id: str,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=200),
    ctx: BillingRequestContext = Depends(require_org),
    app: BillingApplication = Depends(get_application),
) -> SourceBindingPage:
    """`amount_minor` is `null` for anything but a resolved source — **never `0`**."""
    rows, total = app.queries.dispatch(
        ListSourceBindings(
            actor_id=ctx.actor(),
            org_id=ctx.organization(),
            consolidation_id=consolidation_id,
            page=page,
            page_size=page_size,
        )
    )
    return SourceBindingPage(
        sources=[SourceBindingResponse.of(b) for b in rows],
        total=total,
        page=page,
        page_size=page_size,
    )


@router.get(
    "/{consolidation_id}/exceptions",
    response_model=list[ConsolidationExceptionResponse],
    summary="Named problems, worst first",
)
def list_exceptions(
    consolidation_id: str,
    ctx: BillingRequestContext = Depends(require_org),
    app: BillingApplication = Depends(get_application),
) -> list[ConsolidationExceptionResponse]:
    return [
        ConsolidationExceptionResponse.of(e)
        for e in app.queries.dispatch(
            ListConsolidationExceptions(
                actor_id=ctx.actor(),
                org_id=ctx.organization(),
                consolidation_id=consolidation_id,
            )
        )
    ]


@router.post(
    "/exceptions/{exception_id}/acknowledge",
    response_model=ConsolidationExceptionResponse,
    summary="Record that somebody saw a problem",
)
def acknowledge(
    exception_id: str,
    body: ConsolidationNoteBody,
    ctx: BillingRequestContext = Depends(require_org),
    app: BillingApplication = Depends(get_application),
) -> ConsolidationExceptionResponse:
    """**This does not unblock the consolidation.** The exception is never deleted and never
    cleared; `is_blocking` stays true, because an acknowledgement records that somebody looked, not
    that the problem is gone."""
    return ConsolidationExceptionResponse.of(
        app.commands.dispatch(
            AcknowledgeConsolidationException(
                actor_id=ctx.actor(),
                org_id=ctx.organization(),
                exception_id=exception_id,
                note=body.note,
            )
        )
    )


@router.get(
    "/{consolidation_id}/validation",
    response_model=ConsolidationValidationResponse,
    summary="The current verdict",
)
def get_validation(
    consolidation_id: str,
    ctx: BillingRequestContext = Depends(require_org),
    app: BillingApplication = Depends(get_application),
) -> ConsolidationValidationResponse:
    return ConsolidationValidationResponse.of(
        app.queries.dispatch(
            GetConsolidationValidation(
                actor_id=ctx.actor(),
                org_id=ctx.organization(),
                consolidation_id=consolidation_id,
            )
        )
    )


@router.get(
    "/{consolidation_id}/result",
    response_model=ConsolidationResultResponse,
    summary="The finalized artifact, content-addressed",
)
def get_result(
    consolidation_id: str,
    ctx: BillingRequestContext = Depends(require_org),
    app: BillingApplication = Depends(get_application),
) -> ConsolidationResultResponse:
    """Built from pinned state only. The digest and the representation are byte-identical after
    every source moves — which is what makes a consolidated artifact reconstructable six months
    later without relying on application logs."""
    return ConsolidationResultResponse.of(
        app.queries.dispatch(
            GetConsolidationSnapshot(
                actor_id=ctx.actor(),
                org_id=ctx.organization(),
                consolidation_id=consolidation_id,
            )
        )
    )


@router.get(
    "/{consolidation_id}/provenance",
    response_model=ConsolidationProvenanceResponse,
    summary="Which accounts contributed, and which did not",
)
def get_provenance(
    consolidation_id: str,
    ctx: BillingRequestContext = Depends(require_org),
    app: BillingApplication = Depends(get_application),
) -> ConsolidationProvenanceResponse:
    """Assembled from stored rows rather than recomputed — the per-account contribution, the
    paginated binding table and the snapshot's exclusion list, never a million source ids copied
    into one document."""
    return ConsolidationProvenanceResponse.of(
        app.queries.dispatch(
            GetConsolidationProvenance(
                actor_id=ctx.actor(),
                org_id=ctx.organization(),
                consolidation_id=consolidation_id,
            )
        )
    )


@router.get(
    "/{consolidation_id}/timeline",
    response_model=list[ConsolidationEventResponse],
    summary="The trail, oldest first",
)
def timeline(
    consolidation_id: str,
    limit: int = Query(default=200, ge=1, le=500),
    ctx: BillingRequestContext = Depends(require_org),
    app: BillingApplication = Depends(get_application),
) -> list[ConsolidationEventResponse]:
    """**The authority.** `status` is a projection over these entries, not the other way round."""
    return [
        ConsolidationEventResponse.of(e)
        for e in app.queries.dispatch(
            GetConsolidationTimeline(
                actor_id=ctx.actor(),
                org_id=ctx.organization(),
                consolidation_id=consolidation_id,
                limit=limit,
            )
        )
    ]


__all__ = ["router"]
