"""Metering Engine routes (M9.11) — transport only.

Additive to the M9.5–M9.10 transport layer and identical in discipline: parse, take identity from
the verified token, construct a command or query, dispatch, serialize. No metering rule is
evaluated here — normalization, attribution, late-arrival disposition, aggregation, sealing, and
replay comparison are all Foundation behaviour reached through the Application.

Mounted at **`/usage`**, not under M9.5's `/metering`. Same reason as M9.8's `/billing-accounts`,
M9.9's `/agreements`, and M9.10's `/capabilities`: `/metering/usage/{account_id}/{meter_code}` is
an existing wildcard, and siblings under it would be shadowed depending on router registration
order. A separate prefix makes the two surfaces independent of each other's ordering — a
correctness property, not a naming preference. The M9.5 routes are untouched.

**Three things no endpoint here does.** None measures anything: `POST /usage/events` accepts a
signal an authoritative producer already measured, and there is no route that fetches from
Runtime. None mutates a usage record: the surface has no PUT, no PATCH on usage, and exactly one
DELETE-free write path. And none can bill the same event twice — ingestion returns **200** for a
replay and **201** for a new record, so a caller can tell the difference without either outcome
being an error.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query, Response

from billing.application import (
    ActivateCommercialMeter,
    BeginUsagePeriodClose,
    BillingApplication,
    DefineCommercialMeter,
    DeprecateCommercialMeter,
    DiagnoseUsagePeriod,
    DiscardQuarantinedEvent,
    DrainMetricStream,
    ExportCommercialUsage,
    GetCommercialMeter,
    GetCommercialUsageSnapshot,
    GetUsagePeriodState,
    GetUsageReconciliationReport,
    IngestMetricSignal,
    InspectCommercialUsage,
    ListCommercialMeters,
    ListQuarantinedEvents,
    ListUsagePeriods,
    MeteringTimeline,
    OpenUsagePeriod,
    RebuildUsageAggregates,
    ReconcileUsagePeriod,
    RecordUsageCorrection,
    ReinstateCommercialMeter,
    ReplayUsagePeriod,
    ResolveQuarantinedEvent,
    RetireCommercialMeter,
    SealUsagePeriod,
    SearchCommercialUsage,
    VersionCommercialMeter,
    meter_view,
    quarantine_view,
    usage_view,
)
from billing.foundation import BillingNotFound, MetricSignal, UsageStatus

from ..auth import require_org
from ..container import get_application
from ..context import BillingRequestContext
from ..metering_schemas import (
    ActivityResponse,
    CorrectionBody,
    DefineMeterBody,
    DiagnosticsResponse,
    DrainBody,
    DrainReceiptResponse,
    ExportResponse,
    IngestBody,
    IngestionReceiptResponse,
    MeterResponse,
    QuarantineResponse,
    ReasonBody,
    RebuildBody,
    ReconciliationResponse,
    ReplayReceiptResponse,
    ResolveQuarantineBody,
    UsageInspectionResponse,
    UsagePeriodBody,
    UsagePeriodResponse,
    UsageResponse,
    UsageSearchResponse,
    UsageSnapshotResponse,
    VersionMeterBody,
    build_usage_query,
)
from ..schemas import ERROR_RESPONSES

router = APIRouter(prefix="/usage", tags=["metering"], responses=ERROR_RESPONSES)


# ── meters ──────────────────────────────────────────────────────────────────────


@router.post(
    "/meters",
    response_model=MeterResponse,
    status_code=201,
    summary="Declare a meter and its one authoritative source",
)
def define_meter(
    body: DefineMeterBody,
    ctx: BillingRequestContext = Depends(require_org),
    app: BillingApplication = Depends(get_application),
) -> MeterResponse:
    """A meter has exactly one authoritative source. Defining a second meter that claims the same
    `(source, event type)` is rejected — that is the double-counting failure mode."""
    meter = app.commands.dispatch(
        DefineCommercialMeter(
            actor_id=ctx.actor(),
            org_id=ctx.organization(),
            code=body.code,
            unit=body.unit,
            display_name=body.display_name,
            aggregation=body.aggregation,
            source=body.source,
            normalization=body.normalization.to_normalization(),
            late_arrival_window_seconds=body.late_arrival_window_seconds,
            accepted_event_types=tuple(body.accepted_event_types),
            description=body.description,
        )
    )
    return MeterResponse.from_view(meter_view(meter))


@router.get("/meters", response_model=list[MeterResponse], summary="List meters")
def list_meters(
    include_retired: bool = Query(default=False),
    ctx: BillingRequestContext = Depends(require_org),
    app: BillingApplication = Depends(get_application),
) -> list[MeterResponse]:
    views = app.queries.dispatch(
        ListCommercialMeters(
            actor_id=ctx.actor(), org_id=ctx.organization(), include_retired=include_retired
        )
    )
    return [MeterResponse.from_view(v) for v in views]


@router.get("/meters/{code}", response_model=MeterResponse, summary="Read a meter")
def get_meter(
    code: str,
    ctx: BillingRequestContext = Depends(require_org),
    app: BillingApplication = Depends(get_application),
) -> MeterResponse:
    view = app.queries.dispatch(
        GetCommercialMeter(actor_id=ctx.actor(), org_id=ctx.organization(), code=code)
    )
    if view is None:
        raise BillingNotFound(f"meter {code!r} is not defined")
    return MeterResponse.from_view(view)


@router.patch(
    "/meters/{code}",
    response_model=MeterResponse,
    summary="Amend a meter by publishing a new version of it",
)
def version_meter(
    code: str,
    body: VersionMeterBody,
    ctx: BillingRequestContext = Depends(require_org),
    app: BillingApplication = Depends(get_application),
) -> MeterResponse:
    """Never an in-place edit. Unit, aggregation, and normalization all change what a customer
    owes, and every usage record pins the `code@version` it was counted under."""
    meter = app.commands.dispatch(
        VersionCommercialMeter(
            actor_id=ctx.actor(),
            org_id=ctx.organization(),
            code=code,
            unit=body.unit,
            display_name=body.display_name,
            aggregation=body.aggregation,
            normalization=body.normalization.to_normalization() if body.normalization else None,
            late_arrival_window_seconds=body.late_arrival_window_seconds,
            accepted_event_types=(
                tuple(body.accepted_event_types) if body.accepted_event_types is not None else None
            ),
            description=body.description,
        )
    )
    return MeterResponse.from_view(meter_view(meter))


@router.post("/meters/{code}/activate", response_model=MeterResponse, summary="Activate a meter")
def activate_meter(
    code: str,
    ctx: BillingRequestContext = Depends(require_org),
    app: BillingApplication = Depends(get_application),
) -> MeterResponse:
    meter = app.commands.dispatch(
        ActivateCommercialMeter(actor_id=ctx.actor(), org_id=ctx.organization(), code=code)
    )
    return MeterResponse.from_view(meter_view(meter))


@router.post(
    "/meters/{code}/deprecate",
    response_model=MeterResponse,
    summary="Deprecate a meter (it keeps counting what customers already send)",
)
def deprecate_meter(
    code: str,
    body: ReasonBody,
    ctx: BillingRequestContext = Depends(require_org),
    app: BillingApplication = Depends(get_application),
) -> MeterResponse:
    meter = app.commands.dispatch(
        DeprecateCommercialMeter(
            actor_id=ctx.actor(), org_id=ctx.organization(), code=code, reason=body.reason
        )
    )
    return MeterResponse.from_view(meter_view(meter))


@router.post("/meters/{code}/reinstate", response_model=MeterResponse, summary="Reinstate a meter")
def reinstate_meter(
    code: str,
    ctx: BillingRequestContext = Depends(require_org),
    app: BillingApplication = Depends(get_application),
) -> MeterResponse:
    meter = app.commands.dispatch(
        ReinstateCommercialMeter(actor_id=ctx.actor(), org_id=ctx.organization(), code=code)
    )
    return MeterResponse.from_view(meter_view(meter))


@router.post(
    "/meters/{code}/retire",
    response_model=MeterResponse,
    summary="Retire a meter (its events are quarantined, never dropped)",
)
def retire_meter(
    code: str,
    body: ReasonBody,
    ctx: BillingRequestContext = Depends(require_org),
    app: BillingApplication = Depends(get_application),
) -> MeterResponse:
    meter = app.commands.dispatch(
        RetireCommercialMeter(
            actor_id=ctx.actor(), org_id=ctx.organization(), code=code, reason=body.reason
        )
    )
    return MeterResponse.from_view(meter_view(meter))


# ── ingestion ───────────────────────────────────────────────────────────────────


@router.post(
    "/events",
    response_model=IngestionReceiptResponse,
    status_code=201,
    summary="Ingest one metric signal (idempotent on event id + meter)",
)
def ingest(
    response: Response,
    body: IngestBody,
    ctx: BillingRequestContext = Depends(require_org),
    app: BillingApplication = Depends(get_application),
) -> IngestionReceiptResponse:
    """**201** — a new commercial usage record was created.

    **200** — this event was already counted for this meter, so nothing new was recorded. Both are
    successes: at-least-once delivery makes replays normal, and treating one as an error would
    make every retry look like an incident. The caller gets the guarantee either way — the event
    is counted exactly once.

    **202** — the event was quarantined. It is neither billed nor dropped, and it stays visible
    until somebody resolves or discards it."""
    receipt = app.commands.dispatch(
        IngestMetricSignal(
            actor_id=ctx.actor(),
            org_id=ctx.organization(),
            signal=MetricSignal(
                event_id=body.signal.event_id,
                event_type=body.signal.event_type,
                source=body.signal.source,
                observed_at=body.signal.observed_at,
                raw_units=body.signal.raw_units,
                raw_unit=body.signal.raw_unit,
                scope=body.signal.scope.to_scope(ctx.organization()),
                received_at=body.signal.received_at or 0.0,
                producer=body.signal.producer,
                sequence=body.signal.sequence,
                payload_digest=body.signal.payload_digest,
            ),
            period_start=body.period_start,
            period_end=body.period_end,
            ingest_run_id=body.ingest_run_id,
        )
    )
    if receipt.duplicate:
        response.status_code = 200
    elif receipt.quarantined:
        response.status_code = 202
    return IngestionReceiptResponse.from_view(receipt)


@router.post(
    "/drain",
    response_model=DrainReceiptResponse,
    summary="Ingest everything the platform has decoded (safe to run concurrently)",
)
def drain(
    body: DrainBody,
    ctx: BillingRequestContext = Depends(require_org),
    app: BillingApplication = Depends(get_application),
) -> DrainReceiptResponse:
    """Reads the platform's own buffer through `MetricStreamPort`. **Billing never polls Runtime
    and never reaches into runtime state** — an unwired port drains nothing rather than inventing
    usage."""
    receipt = app.commands.dispatch(
        DrainMetricStream(
            actor_id=ctx.actor(),
            org_id=ctx.organization(),
            period_start=body.period_start,
            period_end=body.period_end,
            limit=body.limit,
        )
    )
    return DrainReceiptResponse.from_view(receipt)


# ── periods ─────────────────────────────────────────────────────────────────────


@router.post(
    "/periods",
    response_model=UsagePeriodResponse,
    status_code=201,
    summary="Open a usage period (idempotent)",
)
def open_period(
    body: UsagePeriodBody,
    ctx: BillingRequestContext = Depends(require_org),
    app: BillingApplication = Depends(get_application),
) -> UsagePeriodResponse:
    period = app.commands.dispatch(
        OpenUsagePeriod(
            actor_id=ctx.actor(),
            org_id=ctx.organization(),
            account_id=body.account_id,
            period_start=body.period_start,
            period_end=body.period_end,
        )
    )
    return _period_response(app, ctx, period)


@router.get(
    "/periods/{account_id}",
    response_model=list[UsagePeriodResponse],
    summary="List an account's usage periods",
)
def list_periods(
    account_id: str,
    ctx: BillingRequestContext = Depends(require_org),
    app: BillingApplication = Depends(get_application),
) -> list[UsagePeriodResponse]:
    views = app.queries.dispatch(
        ListUsagePeriods(actor_id=ctx.actor(), org_id=ctx.organization(), account_id=account_id)
    )
    return [UsagePeriodResponse.from_view(v) for v in views]


@router.post(
    "/periods/close",
    response_model=UsagePeriodResponse,
    summary="Begin closing a period (no new in-period usage)",
)
def begin_close(
    body: UsagePeriodBody,
    ctx: BillingRequestContext = Depends(require_org),
    app: BillingApplication = Depends(get_application),
) -> UsagePeriodResponse:
    period = app.commands.dispatch(
        BeginUsagePeriodClose(
            actor_id=ctx.actor(),
            org_id=ctx.organization(),
            account_id=body.account_id,
            period_start=body.period_start,
            period_end=body.period_end,
        )
    )
    return _period_response(app, ctx, period)


@router.post(
    "/periods/seal",
    response_model=UsagePeriodResponse,
    summary="Seal a period — a one-way door",
)
def seal_period(
    body: UsagePeriodBody,
    ctx: BillingRequestContext = Depends(require_org),
    app: BillingApplication = Depends(get_application),
) -> UsagePeriodResponse:
    """After sealing, a correction is an adjustment in a later period or a credit note — never a
    retroactive mutation. The response carries the digest of exactly what was sealed, which is
    what lets a replay later *prove* it reproduced the same commercial usage."""
    period = app.commands.dispatch(
        SealUsagePeriod(
            actor_id=ctx.actor(),
            org_id=ctx.organization(),
            account_id=body.account_id,
            period_start=body.period_start,
            period_end=body.period_end,
        )
    )
    return _period_response(app, ctx, period)


def _period_response(app: BillingApplication, ctx: BillingRequestContext, period):
    """Re-read through the query surface after a command, so `allowed_triggers` — a Foundation
    decision — is computed below this layer rather than in a controller."""
    view = app.queries.dispatch(
        GetUsagePeriodState(
            actor_id=ctx.actor(),
            org_id=ctx.organization(),
            account_id=period.account_id,
            period_start=period.period.start,
            period_end=period.period.end,
        )
    )
    if view is None:  # pragma: no cover — a just-written period is always readable
        raise BillingNotFound("usage period not found")
    return UsagePeriodResponse.from_view(view)


# ── usage ───────────────────────────────────────────────────────────────────────


@router.get(
    "/records", response_model=UsageSearchResponse, summary="Search commercial usage records"
)
def search_usage(
    text: str = Query(default="", description="Case-insensitive substring; no relevance ranking"),
    account_id: str = Query(default=""),
    meter_codes: list[str] = Query(default=[]),
    statuses: list[UsageStatus] = Query(default=[]),
    scope_id: str = Query(default=""),
    subscription_id: str = Query(default=""),
    event_id: str = Query(default=""),
    period_start: float | None = Query(default=None),
    period_end: float | None = Query(default=None),
    observed_from: float | None = Query(default=None),
    observed_until: float | None = Query(default=None),
    billable_only: bool = Query(default=False),
    adjustments_only: bool = Query(default=False),
    late_only: bool = Query(default=False),
    limit: int = Query(default=100, ge=0, le=500),
    offset: int = Query(default=0, ge=0),
    ctx: BillingRequestContext = Depends(require_org),
    app: BillingApplication = Depends(get_application),
) -> UsageSearchResponse:
    view = app.queries.dispatch(
        SearchCommercialUsage(
            actor_id=ctx.actor(),
            org_id=ctx.organization(),
            query=build_usage_query(
                text=text,
                account_id=account_id,
                meter_codes=meter_codes,
                statuses=statuses,
                scope_id=scope_id,
                subscription_id=subscription_id,
                event_id=event_id,
                period_start=period_start,
                period_end=period_end,
                observed_from=observed_from,
                observed_until=observed_until,
                billable_only=billable_only,
                adjustments_only=adjustments_only,
                late_only=late_only,
                limit=limit,
                offset=offset,
            ),
        )
    )
    return UsageSearchResponse.from_view(view)


@router.get(
    "/records/{usage_id}",
    response_model=UsageInspectionResponse,
    summary="Full provenance for one billable fact",
)
def inspect_usage(
    usage_id: str,
    ctx: BillingRequestContext = Depends(require_org),
    app: BillingApplication = Depends(get_application),
) -> UsageInspectionResponse:
    """What it came from, how it was normalized, who it was attributed to, and why it landed in
    the period it did — the answer a billing dispute actually needs."""
    view = app.queries.dispatch(
        InspectCommercialUsage(actor_id=ctx.actor(), org_id=ctx.organization(), usage_id=usage_id)
    )
    if view is None:
        raise BillingNotFound(f"usage {usage_id!r} not found")
    return UsageInspectionResponse.from_view(view)


@router.post(
    "/records/{usage_id}/corrections",
    response_model=UsageResponse,
    status_code=201,
    summary="Append a correction (never edits the record it corrects)",
)
def correct_usage(
    usage_id: str,
    body: CorrectionBody,
    ctx: BillingRequestContext = Depends(require_org),
    app: BillingApplication = Depends(get_application),
) -> UsageResponse:
    """The original figure, the change, and the reason stay separately readable — which is what
    an auditor asks for and what a single overwritten number can never provide."""
    adjustment = app.commands.dispatch(
        RecordUsageCorrection(
            actor_id=ctx.actor(),
            org_id=ctx.organization(),
            usage_id=usage_id,
            delta_units=body.delta_units,
            reason=body.reason,
            reason_code=body.reason_code,
            period_start=body.period_start,
            period_end=body.period_end,
            sequence=body.sequence,
        )
    )
    return UsageResponse.from_view(usage_view(adjustment))


# ── aggregates, snapshot, export ────────────────────────────────────────────────


@router.post(
    "/aggregates/rebuild",
    response_model=UsageSnapshotResponse,
    summary="Recompute a period's aggregates (deterministic)",
)
def rebuild_aggregates(
    body: RebuildBody,
    ctx: BillingRequestContext = Depends(require_org),
    app: BillingApplication = Depends(get_application),
) -> UsageSnapshotResponse:
    app.commands.dispatch(
        RebuildUsageAggregates(
            actor_id=ctx.actor(),
            org_id=ctx.organization(),
            account_id=body.account_id,
            period_start=body.period_start,
            period_end=body.period_end,
            granularity=body.granularity,
        )
    )
    return _snapshot(app, ctx, body.account_id, body.period_start, body.period_end)


@router.get(
    "/snapshot/{account_id}",
    response_model=UsageSnapshotResponse,
    summary="What an account consumed in a period",
)
def usage_snapshot(
    account_id: str,
    period_start: float,
    period_end: float,
    ctx: BillingRequestContext = Depends(require_org),
    app: BillingApplication = Depends(get_application),
) -> UsageSnapshotResponse:
    return _snapshot(app, ctx, account_id, period_start, period_end)


def _snapshot(app, ctx, account_id: str, start: float, end: float) -> UsageSnapshotResponse:
    view = app.queries.dispatch(
        GetCommercialUsageSnapshot(
            actor_id=ctx.actor(),
            org_id=ctx.organization(),
            account_id=account_id,
            period_start=start,
            period_end=end,
        )
    )
    return UsageSnapshotResponse.from_view(view)


@router.get(
    "/export/{account_id}",
    response_model=ExportResponse,
    summary="Finance-grade export of a period's usage (deterministic and digested)",
)
def export_usage(
    account_id: str,
    period_start: float,
    period_end: float,
    ctx: BillingRequestContext = Depends(require_org),
    app: BillingApplication = Depends(get_application),
) -> ExportResponse:
    view = app.queries.dispatch(
        ExportCommercialUsage(
            actor_id=ctx.actor(),
            org_id=ctx.organization(),
            account_id=account_id,
            period_start=period_start,
            period_end=period_end,
        )
    )
    return ExportResponse.from_view(view)


# ── quarantine ──────────────────────────────────────────────────────────────────


@router.get(
    "/quarantine",
    response_model=list[QuarantineResponse],
    summary="Events that could not become usage (never billed, never dropped)",
)
def list_quarantine(
    pending_only: bool = Query(default=True),
    limit: int = Query(default=100, ge=1, le=500),
    ctx: BillingRequestContext = Depends(require_org),
    app: BillingApplication = Depends(get_application),
) -> list[QuarantineResponse]:
    views = app.queries.dispatch(
        ListQuarantinedEvents(
            actor_id=ctx.actor(),
            org_id=ctx.organization(),
            pending_only=pending_only,
            limit=limit,
        )
    )
    return [QuarantineResponse.from_view(v) for v in views]


@router.post(
    "/quarantine/{entry_id}/resolve",
    response_model=QuarantineResponse,
    summary="Re-ingest a quarantined event now its cause is fixed",
)
def resolve_quarantine(
    entry_id: str,
    body: ResolveQuarantineBody,
    ctx: BillingRequestContext = Depends(require_org),
    app: BillingApplication = Depends(get_application),
) -> QuarantineResponse:
    """Because the usage id is derived from the source event, resolving produces exactly the
    record the original ingestion would have — so resolving twice is idempotent too."""
    entry = app.commands.dispatch(
        ResolveQuarantinedEvent(
            actor_id=ctx.actor(),
            org_id=ctx.organization(),
            entry_id=entry_id,
            meter_code=body.meter_code,
            account_id=body.account_id,
            note=body.note,
        )
    )
    return QuarantineResponse.from_view(quarantine_view(entry))


@router.post(
    "/quarantine/{entry_id}/discard",
    response_model=QuarantineResponse,
    summary="Judge a quarantined event not billable, with a recorded reason",
)
def discard_quarantine(
    entry_id: str,
    body: ReasonBody,
    ctx: BillingRequestContext = Depends(require_org),
    app: BillingApplication = Depends(get_application),
) -> QuarantineResponse:
    entry = app.commands.dispatch(
        DiscardQuarantinedEvent(
            actor_id=ctx.actor(),
            org_id=ctx.organization(),
            entry_id=entry_id,
            reason=body.reason,
        )
    )
    return QuarantineResponse.from_view(quarantine_view(entry))


# ── replay, reconciliation, diagnostics ─────────────────────────────────────────


@router.post(
    "/replay",
    response_model=ReplayReceiptResponse,
    summary="Re-derive a period from the log and compare against its seal",
)
def replay(
    body: UsagePeriodBody,
    ctx: BillingRequestContext = Depends(require_org),
    app: BillingApplication = Depends(get_application),
) -> ReplayReceiptResponse:
    """`matches_seal` is the proof. Replay uses the **same pure ingestion decision** the live path
    uses, so a match means the commercial outcome is identical rather than merely similar."""
    receipt = app.commands.dispatch(
        ReplayUsagePeriod(
            actor_id=ctx.actor(),
            org_id=ctx.organization(),
            account_id=body.account_id,
            period_start=body.period_start,
            period_end=body.period_end,
        )
    )
    return ReplayReceiptResponse.from_view(receipt)


@router.post(
    "/reconcile",
    response_model=ReconciliationResponse,
    summary="Compare the event log against recorded usage",
)
def reconcile(
    body: UsagePeriodBody,
    ctx: BillingRequestContext = Depends(require_org),
    app: BillingApplication = Depends(get_application),
) -> ReconciliationResponse:
    receipt = app.commands.dispatch(
        ReconcileUsagePeriod(
            actor_id=ctx.actor(),
            org_id=ctx.organization(),
            account_id=body.account_id,
            period_start=body.period_start,
            period_end=body.period_end,
        )
    )
    return ReconciliationResponse.from_view(receipt)


@router.get(
    "/reconcile/{account_id}",
    response_model=ReconciliationResponse,
    summary="Read the reconciliation report without recording a run",
)
def reconciliation_report(
    account_id: str,
    period_start: float,
    period_end: float,
    ctx: BillingRequestContext = Depends(require_org),
    app: BillingApplication = Depends(get_application),
) -> ReconciliationResponse:
    view = app.queries.dispatch(
        GetUsageReconciliationReport(
            actor_id=ctx.actor(),
            org_id=ctx.organization(),
            account_id=account_id,
            period_start=period_start,
            period_end=period_end,
        )
    )
    return ReconciliationResponse.from_view(view)


@router.get(
    "/diagnostics/{account_id}",
    response_model=DiagnosticsResponse,
    summary="Everything structurally wrong with a period's metering",
)
def diagnostics(
    account_id: str,
    period_start: float,
    period_end: float,
    ctx: BillingRequestContext = Depends(require_org),
    app: BillingApplication = Depends(get_application),
) -> DiagnosticsResponse:
    """Reports *all* findings, including a seal-digest mismatch — the financial-integrity incident
    the digest exists to make detectable."""
    view = app.queries.dispatch(
        DiagnoseUsagePeriod(
            actor_id=ctx.actor(),
            org_id=ctx.organization(),
            account_id=account_id,
            period_start=period_start,
            period_end=period_end,
        )
    )
    if view is None:
        raise BillingNotFound(f"no usage period {period_start}–{period_end} for {account_id!r}")
    return DiagnosticsResponse.from_view(view)


@router.get(
    "/timeline",
    response_model=list[ActivityResponse],
    summary="Metering history, duplicate ingestions included",
)
def timeline(
    account_id: str = Query(default=""),
    limit: int = Query(default=100, ge=1, le=500),
    ctx: BillingRequestContext = Depends(require_org),
    app: BillingApplication = Depends(get_application),
) -> list[ActivityResponse]:
    """A duplicate appears here as its own entry: "we saw this event again and did nothing" is
    the evidence that idempotency worked."""
    views = app.queries.dispatch(
        MeteringTimeline(
            actor_id=ctx.actor(),
            org_id=ctx.organization(),
            account_id=account_id,
            limit=limit,
        )
    )
    return [ActivityResponse.from_view(v) for v in views]
