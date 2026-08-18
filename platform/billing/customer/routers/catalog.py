"""Catalog routes (M9.5) — meters and plans. Transport only.

Note the shape of `POST /catalog/plans`: defining a plan and publishing it are two
Application commands, and `PublishPlan` takes the aggregate `DefinePlan` returned. The
controller therefore dispatches both and holds the intermediate value — sequencing two
dispatches, deciding nothing. A dedicated publish endpoint would have to *re-read* the plan,
which a controller may not do; the clean fix is a `GetPlan` query in a later Application
milestone, recorded as a known limitation rather than worked around here.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query

from billing.application import (
    BillingApplication,
    DefineMeter,
    DefinePlan,
    ListPublishedPlans,
    PublishPlan,
)

from ..auth import authenticate
from ..container import get_application
from ..context import BillingRequestContext
from ..schemas import ERROR_RESPONSES, DefineMeterBody, DefinePlanBody, PlanResponse

router = APIRouter(prefix="/catalog", tags=["catalog"], responses=ERROR_RESPONSES)


@router.post("/meters", status_code=201, summary="Define a meter")
def define_meter(
    body: DefineMeterBody,
    ctx: BillingRequestContext = Depends(authenticate),
    app: BillingApplication = Depends(get_application),
) -> dict:
    """Defines *what* is measured. Billing never measures — it consumes `metric.*`
    read-only (ADR-0023 M1)."""
    meter = app.commands.dispatch(
        DefineMeter(
            actor_id=ctx.actor(),
            org_id=ctx.organization(),
            code=body.code,
            display_name=body.display_name,
            unit=body.unit,
            aggregation=body.aggregation,
            source=body.source,
            rounding=body.rounding,
        )
    )
    return {"code": meter.code, "unit": meter.unit, "version": meter.version}


@router.post("/plans", response_model=PlanResponse, status_code=201, summary="Define a plan")
def define_plan(
    body: DefinePlanBody,
    publish: bool = Query(default=False, description="Publish the plan in the same request"),
    ctx: BillingRequestContext = Depends(authenticate),
    app: BillingApplication = Depends(get_application),
) -> PlanResponse:
    plan = app.commands.dispatch(
        DefinePlan(
            actor_id=ctx.actor(),
            org_id=ctx.organization(),
            code=body.code,
            display_name=body.display_name,
            tier=body.tier,
            features=body.to_features(),
            metered_codes=tuple(body.metered_codes),
        )
    )
    if publish:
        plan = app.commands.dispatch(
            PublishPlan(actor_id=ctx.actor(), org_id=ctx.organization(), plan=plan)
        )
    return PlanResponse.from_aggregate(plan)


@router.get("/plans", response_model=list[PlanResponse], summary="List published plans")
def list_plans(
    ctx: BillingRequestContext = Depends(authenticate),
    app: BillingApplication = Depends(get_application),
) -> list[PlanResponse]:
    views = app.queries.dispatch(
        ListPublishedPlans(actor_id=ctx.actor(), org_id=ctx.organization())
    )
    return [PlanResponse.from_view(v) for v in views]
