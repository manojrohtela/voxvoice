"""Rating Engine routes (M9.12) — transport only.

Additive to the M9.5–M9.11 transport layer and identical in discipline: parse, take identity from
the verified token, construct a command or query, dispatch, serialize. No rating rule is evaluated
here — extension, tier folding, package rounding, allowance precedence, discount re-basing,
commitment true-up, lifecycle validity, and run comparison are all Foundation behaviour reached
through the Application.

Mounted at **`/charges`**, not under M9.5's `/invoices` or M9.7's `/pricing`. Same reason as
M9.8's `/billing-accounts`, M9.9's `/agreements`, M9.10's `/capabilities`, and M9.11's `/usage`:
siblings under an existing wildcard would be shadowed depending on router registration order. A
separate prefix makes the surfaces independent of each other's ordering — a correctness property,
not a naming preference.

**Four things no endpoint here does.** None computes an amount: every figure on the wire came from
a Foundation service. None mutates another domain: there is no route that writes usage, a
subscription, a catalog version, a pricing version, or an entitlement snapshot. None can edit a
finalized run: the surface has no PUT and no PATCH on a run, and the FSM has no edge out of
`finalized`. And none can bill a period twice — rating returns **201** for a new run, **200** for
an unchanged one, so a caller can tell the difference without either being an error.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query, Response

from billing.application import (
    BillingApplication,
    CompareRatingRuns,
    DefineMinimumCommitment,
    DiagnoseRatingRun,
    DiscardRatingRun,
    ExplainRatingRun,
    FinalizeRatingRun,
    GetChargeBreakdown,
    GetRatingRun,
    GetRerateVerdict,
    InspectRatingRun,
    ListMinimumCommitments,
    ListRatingRuns,
    PreviewSubscriptionRating,
    RateAccountPeriod,
    RateSubscriptionPeriod,
    RatingTimeline,
    RecordRatingAdjustment,
    RerateSubscriptionPeriod,
    RetireMinimumCommitment,
    SearchRatingRuns,
    SnapshotRatingRun,
)
from billing.foundation import ChargeKind, Currency, RatingState, RerateReason

from ..auth import require_org
from ..container import get_application
from ..context import BillingRequestContext
from ..rating_schemas import (
    AccountPeriodBody,
    AdjustmentBody,
    BatchRatingReceiptResponse,
    CommitmentResponse,
    DefineCommitmentBody,
    RatedChargeResponse,
    RatingActivityResponse,
    RatingDeltaResponse,
    RatingDiagnosticsResponse,
    RatingExplanationResponse,
    RatingInspectionResponse,
    RatingPeriodBody,
    RatingReceiptResponse,
    RatingResultResponse,
    RatingRunResponse,
    RatingSearchResponse,
    RatingSnapshotResponse,
    ReasonBody,
    RerateBody,
    RerateVerdictResponse,
    build_rating_query,
)
from ..schemas import ERROR_RESPONSES

router = APIRouter(prefix="/charges", tags=["rating"], responses=ERROR_RESPONSES)


# ── minimum commitments ─────────────────────────────────────────────────────────


@router.post(
    "/commitments",
    response_model=CommitmentResponse,
    status_code=201,
    summary="Declare a minimum spend commitment",
)
def define_commitment(
    body: DefineCommitmentBody,
    ctx: BillingRequestContext = Depends(require_org),
    app: BillingApplication = Depends(get_application),
) -> CommitmentResponse:
    """A commitment never changes what anything costs. It adds a true-up charge when the covered
    spend falls short, applied after every other charge."""
    commitment = app.commands.dispatch(
        DefineMinimumCommitment(
            actor_id=ctx.actor(),
            org_id=ctx.organization(),
            account_id=body.account_id,
            code=body.code,
            amount_minor=body.amount.minor,
            currency=Currency(body.amount.currency),
            subscription_id=body.subscription_id,
            price_codes=tuple(body.price_codes),
            interval=body.interval,
            contract_id=body.contract_id,
            description=body.description,
            effective_from=body.effective_from,
            effective_until=body.effective_until,
        )
    )
    from billing.application import commitment_view

    return CommitmentResponse(**commitment_view(commitment).__dict__)


@router.post(
    "/commitments/{code}/retire",
    response_model=CommitmentResponse,
    summary="Retire a commitment (a state, never a deletion)",
)
def retire_commitment(
    code: str,
    body: ReasonBody,
    ctx: BillingRequestContext = Depends(require_org),
    app: BillingApplication = Depends(get_application),
) -> CommitmentResponse:
    commitment = app.commands.dispatch(
        RetireMinimumCommitment(
            actor_id=ctx.actor(), org_id=ctx.organization(), code=code, reason=body.reason
        )
    )
    from billing.application import commitment_view

    return CommitmentResponse(**commitment_view(commitment).__dict__)


@router.get(
    "/commitments",
    response_model=list[CommitmentResponse],
    summary="List minimum commitments",
)
def list_commitments(
    account_id: str = Query(default=""),
    ctx: BillingRequestContext = Depends(require_org),
    app: BillingApplication = Depends(get_application),
) -> list[CommitmentResponse]:
    found = app.queries.dispatch(
        ListMinimumCommitments(
            actor_id=ctx.actor(), org_id=ctx.organization(), account_id=account_id
        )
    )
    return [CommitmentResponse(**c.__dict__) for c in found]


# ── rating ──────────────────────────────────────────────────────────────────────


@router.post(
    "/runs",
    response_model=RatingReceiptResponse,
    summary="Rate one agreement for one period (idempotent on the basis digest)",
)
def rate(
    body: RatingPeriodBody,
    response: Response,
    ctx: BillingRequestContext = Depends(require_org),
    app: BillingApplication = Depends(get_application),
) -> RatingReceiptResponse:
    """**201** when a run was created, **200** when the inputs had not moved and the existing
    figure stands. Both are successes: a nightly job that finds nothing to do has still done its
    job, and a caller that cannot tell the two apart would either re-bill or re-check forever."""
    receipt = app.commands.dispatch(
        RateSubscriptionPeriod(
            actor_id=ctx.actor(),
            org_id=ctx.organization(),
            subscription_id=body.subscription_id,
            period_start=body.period_start,
            period_end=body.period_end,
        )
    )
    response.status_code = 200 if receipt.outcome == "unchanged" else 201
    return _receipt(receipt)


@router.post(
    "/runs/account",
    response_model=BatchRatingReceiptResponse,
    summary="Rate every live agreement on an account",
)
def rate_account(
    body: AccountPeriodBody,
    ctx: BillingRequestContext = Depends(require_org),
    app: BillingApplication = Depends(get_application),
) -> BatchRatingReceiptResponse:
    """One transaction per agreement, so a single bad agreement cannot roll back the ones that
    rated correctly. Safe to run on several instances at once."""
    batch = app.commands.dispatch(
        RateAccountPeriod(
            actor_id=ctx.actor(),
            org_id=ctx.organization(),
            account_id=body.account_id,
            period_start=body.period_start,
            period_end=body.period_end,
        )
    )
    return BatchRatingReceiptResponse(
        subscriptions_seen=batch.subscriptions_seen,
        rated=batch.rated,
        rerated=batch.rerated,
        unchanged=batch.unchanged,
        skipped=batch.skipped,
        receipts=[_receipt(r) for r in batch.receipts],
    )


@router.post(
    "/runs/rerate",
    response_model=RatingReceiptResponse,
    status_code=201,
    summary="Rate a period again because an input moved",
)
def rerate(
    body: RerateBody,
    response: Response,
    ctx: BillingRequestContext = Depends(require_org),
    app: BillingApplication = Depends(get_application),
) -> RatingReceiptResponse:
    """Never an edit. A provisional predecessor is superseded; a **finalized** one is adjusted and
    left exactly as it was billed."""
    receipt = app.commands.dispatch(
        RerateSubscriptionPeriod(
            actor_id=ctx.actor(),
            org_id=ctx.organization(),
            subscription_id=body.subscription_id,
            period_start=body.period_start,
            period_end=body.period_end,
            reason=body.reason,
            detail=body.detail,
        )
    )
    response.status_code = 200 if receipt.outcome == "unchanged" else 201
    return _receipt(receipt)


@router.post(
    "/runs/{rating_run_id}/finalize",
    response_model=RatingRunResponse,
    summary="Freeze a run — a one-way door",
)
def finalize(
    rating_run_id: str,
    ctx: BillingRequestContext = Depends(require_org),
    app: BillingApplication = Depends(get_application),
) -> RatingRunResponse:
    """Refused when the run's arithmetic does not explain itself: a figure nobody can defend must
    not reach an invoice."""
    run = app.commands.dispatch(
        FinalizeRatingRun(
            actor_id=ctx.actor(), org_id=ctx.organization(), rating_run_id=rating_run_id
        )
    )
    return _run(run)


@router.post(
    "/runs/{rating_run_id}/discard",
    response_model=RatingRunResponse,
    summary="Abandon a provisional run",
)
def discard(
    rating_run_id: str,
    body: ReasonBody,
    ctx: BillingRequestContext = Depends(require_org),
    app: BillingApplication = Depends(get_application),
) -> RatingRunResponse:
    run = app.commands.dispatch(
        DiscardRatingRun(
            actor_id=ctx.actor(),
            org_id=ctx.organization(),
            rating_run_id=rating_run_id,
            reason=body.reason,
        )
    )
    return _run(run)


@router.post(
    "/runs/{rating_run_id}/adjust",
    response_model=RatingRunResponse,
    status_code=201,
    summary="Record adjustment charges against a finalized run",
)
def adjust(
    rating_run_id: str,
    body: AdjustmentBody,
    ctx: BillingRequestContext = Depends(require_org),
    app: BillingApplication = Depends(get_application),
) -> RatingRunResponse:
    """The finalized run is read and left exactly as it is; the difference becomes new charges."""
    run = app.commands.dispatch(
        RecordRatingAdjustment(
            actor_id=ctx.actor(),
            org_id=ctx.organization(),
            rating_run_id=rating_run_id,
            against_run_id=body.against_run_id,
            detail=body.detail,
        )
    )
    return _run(run)


@router.post(
    "/runs/{rating_run_id}/snapshot",
    response_model=RatingSnapshotResponse,
    status_code=201,
    summary="Take an immutable snapshot of what a run decided",
)
def snapshot(
    rating_run_id: str,
    body: ReasonBody,
    ctx: BillingRequestContext = Depends(require_org),
    app: BillingApplication = Depends(get_application),
) -> RatingSnapshotResponse:
    taken = app.commands.dispatch(
        SnapshotRatingRun(
            actor_id=ctx.actor(),
            org_id=ctx.organization(),
            rating_run_id=rating_run_id,
            reason=body.reason,
        )
    )
    return RatingSnapshotResponse(**_snapshot(taken))


# ── reads ───────────────────────────────────────────────────────────────────────


@router.get("/runs", response_model=list[RatingRunResponse], summary="List rating runs")
def list_runs(
    account_id: str = Query(default=""),
    subscription_id: str = Query(default=""),
    period_start: float | None = Query(default=None),
    period_end: float | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=500),
    ctx: BillingRequestContext = Depends(require_org),
    app: BillingApplication = Depends(get_application),
) -> list[RatingRunResponse]:
    found = app.queries.dispatch(
        ListRatingRuns(
            actor_id=ctx.actor(),
            org_id=ctx.organization(),
            account_id=account_id,
            subscription_id=subscription_id,
            period_start=period_start,
            period_end=period_end,
            limit=limit,
        )
    )
    return [_run(r) for r in found]


@router.post(
    "/preview",
    response_model=RatingResultResponse,
    summary="What a period would cost — writes nothing",
)
def preview(
    body: RatingPeriodBody,
    ctx: BillingRequestContext = Depends(require_org),
    app: BillingApplication = Depends(get_application),
) -> RatingResultResponse:
    """A **query** despite the POST verb: the body carries a period, not an intention. Nothing is
    written — no run, no charge, no activity entry, no event — so a preview can never become the
    billed figure."""
    result = app.queries.dispatch(
        PreviewSubscriptionRating(
            actor_id=ctx.actor(),
            org_id=ctx.organization(),
            subscription_id=body.subscription_id,
            period_start=body.period_start,
            period_end=body.period_end,
        )
    )
    return RatingResultResponse(**_result(result))


@router.post(
    "/verdict",
    response_model=RerateVerdictResponse,
    summary="Whether a period needs rating again",
)
def verdict(
    body: RatingPeriodBody,
    ctx: BillingRequestContext = Depends(require_org),
    app: BillingApplication = Depends(get_application),
) -> RerateVerdictResponse:
    """Answered by comparing basis digests, so asking costs nothing and changes nothing."""
    answer = app.queries.dispatch(
        GetRerateVerdict(
            actor_id=ctx.actor(),
            org_id=ctx.organization(),
            subscription_id=body.subscription_id,
            period_start=body.period_start,
            period_end=body.period_end,
        )
    )
    return RerateVerdictResponse(**answer.__dict__)


@router.get("/runs/{rating_run_id}", response_model=RatingRunResponse, summary="One rating run")
def get_run(
    rating_run_id: str,
    ctx: BillingRequestContext = Depends(require_org),
    app: BillingApplication = Depends(get_application),
) -> RatingRunResponse:
    run = app.queries.dispatch(
        GetRatingRun(actor_id=ctx.actor(), org_id=ctx.organization(), rating_run_id=rating_run_id)
    )
    return _run(run)


@router.get(
    "/runs/{rating_run_id}/charges/{charge_id}",
    response_model=RatedChargeResponse,
    summary="One charge, with its full breakdown and provenance",
)
def breakdown(
    rating_run_id: str,
    charge_id: str,
    ctx: BillingRequestContext = Depends(require_org),
    app: BillingApplication = Depends(get_application),
) -> RatedChargeResponse:
    """The answer to "why is this line ₹4,120?" — which rate, times which quantity, from which
    usage records, less which reductions."""
    charge = app.queries.dispatch(
        GetChargeBreakdown(
            actor_id=ctx.actor(),
            org_id=ctx.organization(),
            rating_run_id=rating_run_id,
            charge_id=charge_id,
        )
    )
    return RatedChargeResponse(**_charge(charge))


@router.get(
    "/runs/{rating_run_id}/explain",
    response_model=RatingExplanationResponse,
    summary="A whole run, in sentences",
)
def explain(
    rating_run_id: str,
    ctx: BillingRequestContext = Depends(require_org),
    app: BillingApplication = Depends(get_application),
) -> RatingExplanationResponse:
    explanation = app.queries.dispatch(
        ExplainRatingRun(
            actor_id=ctx.actor(), org_id=ctx.organization(), rating_run_id=rating_run_id
        )
    )
    return RatingExplanationResponse(
        rating_run_id=explanation.rating_run_id,
        subscription_id=explanation.subscription_id,
        period_start=explanation.period_start,
        period_end=explanation.period_end,
        currency=explanation.currency,
        total=explanation.total.__dict__,
        basis_lines=list(explanation.basis_lines),
        charges=[
            {
                "charge_id": c.charge_id,
                "kind": c.kind,
                "description": c.description,
                "amount": c.amount.__dict__,
                "lines": list(c.lines),
                "provenance_lines": list(c.provenance_lines),
            }
            for c in explanation.charges
        ],
    )


@router.get(
    "/runs/{rating_run_id}/inspect",
    response_model=RatingInspectionResponse,
    summary="Does every charge add up, and is every amount attributable?",
)
def inspect(
    rating_run_id: str,
    ctx: BillingRequestContext = Depends(require_org),
    app: BillingApplication = Depends(get_application),
) -> RatingInspectionResponse:
    inspection = app.queries.dispatch(
        InspectRatingRun(
            actor_id=ctx.actor(), org_id=ctx.organization(), rating_run_id=rating_run_id
        )
    )
    return RatingInspectionResponse(
        rating_run_id=inspection.rating_run_id,
        subscription_id=inspection.subscription_id,
        period_start=inspection.period_start,
        period_end=inspection.period_end,
        state=inspection.state,
        total=inspection.total.__dict__,
        basis_digest=inspection.basis_digest,
        result_digest=inspection.result_digest,
        is_sound=inspection.is_sound,
        unsound=list(inspection.unsound),
        charges=[
            {
                "charge_id": c.charge_id,
                "kind": c.kind,
                "amount": c.amount.__dict__,
                "breaks_down_correctly": c.breaks_down_correctly,
                "has_provenance": c.has_provenance,
                "has_usage_attribution": c.has_usage_attribution,
                "component_count": c.component_count,
                "discount_count": c.discount_count,
                "is_sound": c.is_sound,
                "notes": list(c.notes),
            }
            for c in inspection.charges
        ],
        notes=list(inspection.notes),
    )


@router.get(
    "/runs/{rating_run_id}/diagnostics",
    response_model=RatingDiagnosticsResponse,
    summary="Structural findings for one run",
)
def diagnose(
    rating_run_id: str,
    ctx: BillingRequestContext = Depends(require_org),
    app: BillingApplication = Depends(get_application),
) -> RatingDiagnosticsResponse:
    report = app.queries.dispatch(
        DiagnoseRatingRun(
            actor_id=ctx.actor(), org_id=ctx.organization(), rating_run_id=rating_run_id
        )
    )
    return RatingDiagnosticsResponse(
        rating_run_id=report.rating_run_id,
        findings=[f.__dict__ for f in report.findings],
        error_count=report.error_count,
        warning_count=report.warning_count,
        is_valid=report.is_valid,
    )


@router.get("/compare", response_model=RatingDeltaResponse, summary="Why two runs differ")
def compare(
    before_run_id: str = Query(),
    after_run_id: str = Query(),
    ctx: BillingRequestContext = Depends(require_org),
    app: BillingApplication = Depends(get_application),
) -> RatingDeltaResponse:
    """Matched on what produced each charge, not on position — so one inserted line does not read
    as every line changing."""
    delta = app.queries.dispatch(
        CompareRatingRuns(
            actor_id=ctx.actor(),
            org_id=ctx.organization(),
            before_run_id=before_run_id,
            after_run_id=after_run_id,
        )
    )
    return RatingDeltaResponse(
        subscription_id=delta.subscription_id,
        period_start=delta.period_start,
        period_end=delta.period_end,
        before_run_id=delta.before_run_id,
        after_run_id=delta.after_run_id,
        currency=delta.currency,
        total_before=delta.total_before.__dict__ if delta.total_before else None,
        total_after=delta.total_after.__dict__ if delta.total_after else None,
        total_delta=delta.total_delta.__dict__,
        differences=[
            {
                **d.__dict__,
                "before": d.before.__dict__ if d.before else None,
                "after": d.after.__dict__ if d.after else None,
                "delta": d.delta.__dict__ if d.delta else None,
            }
            for d in delta.differences
        ],
        charges_changed=delta.charges_changed,
        is_material=delta.is_material,
    )


@router.get("/search", response_model=RatingSearchResponse, summary="Search rating runs")
def search(
    text: str = Query(default=""),
    account_id: str = Query(default=""),
    subscription_id: str = Query(default=""),
    states: list[RatingState] = Query(default=[]),
    kinds: list[ChargeKind] = Query(default=[]),
    reasons: list[RerateReason] = Query(default=[]),
    rerates_only: bool = Query(default=False),
    min_total_minor: int | None = Query(default=None),
    max_total_minor: int | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    ctx: BillingRequestContext = Depends(require_org),
    app: BillingApplication = Depends(get_application),
) -> RatingSearchResponse:
    found = app.queries.dispatch(
        SearchRatingRuns(
            actor_id=ctx.actor(),
            org_id=ctx.organization(),
            query=build_rating_query(
                text=text,
                account_id=account_id,
                subscription_id=subscription_id,
                states=states,
                kinds=kinds,
                reasons=reasons,
                rerates_only=rerates_only,
                min_total_minor=min_total_minor,
                max_total_minor=max_total_minor,
                limit=limit,
                offset=offset,
            ),
        )
    )
    return RatingSearchResponse(runs=[_run(r) for r in found.runs], total=found.total)


@router.get(
    "/timeline",
    response_model=list[RatingActivityResponse],
    summary="Rating history, newest first",
)
def timeline(
    subscription_id: str = Query(default=""),
    limit: int = Query(default=100, ge=1, le=500),
    ctx: BillingRequestContext = Depends(require_org),
    app: BillingApplication = Depends(get_application),
) -> list[RatingActivityResponse]:
    """Includes the runs that changed nothing — "we rated this again and the figure held" is the
    evidence determinism worked."""
    found = app.queries.dispatch(
        RatingTimeline(
            actor_id=ctx.actor(),
            org_id=ctx.organization(),
            subscription_id=subscription_id,
            limit=limit,
        )
    )
    return [
        RatingActivityResponse(**{**a.__dict__, "total": a.total.__dict__ if a.total else None})
        for a in found
    ]


# ── serialization helpers ───────────────────────────────────────────────────────


def _charge(charge) -> dict:
    return {
        **charge.__dict__,
        "list_amount": charge.list_amount.__dict__ if charge.list_amount else None,
        "gross_amount": charge.gross_amount.__dict__ if charge.gross_amount else None,
        "discount_amount": charge.discount_amount.__dict__ if charge.discount_amount else None,
        "net_amount": charge.net_amount.__dict__,
        "components": [
            {
                **c.__dict__,
                "amount": c.amount.__dict__,
                "unit_amount": c.unit_amount.__dict__ if c.unit_amount else None,
            }
            for c in charge.components
        ],
        "discounts": [{**d.__dict__, "reduction": d.reduction.__dict__} for d in charge.discounts],
        "provenance": {
            **charge.provenance.__dict__,
            "usage_ids": list(charge.provenance.usage_ids),
            "resolution_path": list(charge.provenance.resolution_path),
        },
    }


def _result(result) -> dict:
    return {
        **result.__dict__,
        "charges": [_charge(c) for c in result.charges],
        "kinds": list(result.kinds),
        "subtotal": result.subtotal.__dict__,
        "discount_total": result.discount_total.__dict__,
        "total": result.total.__dict__,
        "basis": {
            **result.basis.__dict__,
            "price_pins": list(result.basis.price_pins),
            "seat_counts": list(result.basis.seat_counts),
            "metered_units": list(result.basis.metered_units),
            "commitment_codes": list(result.basis.commitment_codes),
        },
    }


def _run(run) -> RatingRunResponse:
    return RatingRunResponse(
        **{
            **run.__dict__,
            "total": run.total.__dict__,
            "allowed_triggers": list(run.allowed_triggers),
            "result": _result(run.result) if run.result else None,
        }
    )


def _receipt(receipt) -> RatingReceiptResponse:
    return RatingReceiptResponse(
        outcome=receipt.outcome,
        rating_run_id=receipt.rating_run_id,
        subscription_id=receipt.subscription_id,
        basis_digest=receipt.basis_digest,
        total=receipt.total.__dict__,
        charge_count=receipt.charge_count,
        run=_run(receipt.run),
    )


def _snapshot(taken) -> dict:
    return {**taken.__dict__, "total": taken.total.__dict__ if taken.total else None}
