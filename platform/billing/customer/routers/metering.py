"""Metering routes (M9.5) — transport only.

**Billing never measures.** These endpoints ingest usage that an authoritative producer
already measured, and ingestion is idempotent on `(event_id, meter_code)`: replaying a
delivery returns the first record and bills once (ADR-0023 M4). That is why the source event
id is a required field and not an optional nicety — it is the exactly-once key.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Response

from billing.application import (
    BillingApplication,
    GetUsageSummary,
    QuarantineUsage,
    RecordUsage,
)

from ..auth import require_org
from ..container import get_application
from ..context import BillingRequestContext
from ..schemas import (
    ERROR_RESPONSES,
    PeriodBody,
    QuarantineUsageBody,
    RecordUsageBody,
    UsageSummaryResponse,
)

router = APIRouter(prefix="/metering", tags=["metering"], responses=ERROR_RESPONSES)


@router.post("/usage", status_code=202, summary="Ingest a usage record (idempotent)")
def record_usage(
    response: Response,
    body: RecordUsageBody,
    ctx: BillingRequestContext = Depends(require_org),
    app: BillingApplication = Depends(get_application),
) -> dict:
    """**202** — accepted and durable; rating and invoicing happen later, in a billing run.

    **200** — this `event_id` was already ingested for this meter, so nothing new was
    recorded. The Application reports a replay by returning no record; the API turns that
    into a distinguishable status rather than inventing a second record id or pretending a
    second ingestion happened. Either way the caller has the guarantee it needs: the event is
    counted exactly once."""
    record = app.commands.dispatch(
        RecordUsage(
            actor_id=ctx.actor(),
            org_id=ctx.organization(),
            account_id=body.account_id,
            meter_code=body.meter_code,
            units=body.units,
            unit=body.unit,
            scope=body.scope.to_scope(ctx.organization()),
            period=body.period.to_period(),
            event_id=body.event_id,
            event_type=body.event_type,
            observed_at=body.observed_at,
            subscription_id=body.subscription_id,
        )
    )
    if record is None:
        response.status_code = 200
        return {"duplicate": True, "event_id": body.event_id, "meter_code": body.meter_code}
    return {
        "duplicate": False,
        "id": record.id,
        "meter_code": record.meter_code,
        "units": record.quantity.units,
        "status": record.status.value,
    }


@router.post("/quarantine", status_code=202, summary="Quarantine an unbillable usage event")
def quarantine_usage(
    body: QuarantineUsageBody,
    ctx: BillingRequestContext = Depends(require_org),
    app: BillingApplication = Depends(get_application),
) -> dict:
    """Unrecognised or malformed usage is parked, never silently dropped and never billed —
    a quarantined event stays available for correction."""
    quarantined = app.commands.dispatch(
        QuarantineUsage(
            actor_id=ctx.actor(),
            org_id=ctx.organization(),
            account_id=body.account_id,
            meter_code=body.meter_code,
            units=body.units,
            unit=body.unit,
            scope=body.scope.to_scope(ctx.organization()),
            period=body.period.to_period(),
            event_id=body.event_id,
            event_type=body.event_type,
            observed_at=body.observed_at,
            reason=body.reason,
        )
    )
    return {"id": quarantined.id, "reason": quarantined.quarantine_reason}


@router.get(
    "/usage/{account_id}/{meter_code}",
    response_model=UsageSummaryResponse,
    summary="Summarize usage for a period",
)
def usage_summary(
    account_id: str,
    meter_code: str,
    period_start: float,
    period_end: float,
    ctx: BillingRequestContext = Depends(require_org),
    app: BillingApplication = Depends(get_application),
) -> UsageSummaryResponse:
    view = app.queries.dispatch(
        GetUsageSummary(
            actor_id=ctx.actor(),
            org_id=ctx.organization(),
            account_id=account_id,
            meter_code=meter_code,
            period=PeriodBody(start=period_start, end=period_end).to_period(),
        )
    )
    return UsageSummaryResponse.from_view(view)
