"""Subscription routes (M9.5) — transport only.

Every lifecycle move goes through **one** endpoint carrying a trigger, mirroring the
Application's single `TransitionSubscription` command. Whether a trigger is legal from the
current state is decided by the Foundation's table-driven state machine; an illegal edge
becomes a 409 and is never coerced into something adjacent.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends

from billing.application import (
    BillingApplication,
    ChangeSubscriptionPlan,
    GetSubscription,
    ListSubscriptions,
    StartSubscription,
    TransitionSubscription,
)
from billing.foundation import BillingNotFound

from ..auth import require_org
from ..container import get_application
from ..context import BillingRequestContext
from ..schemas import (
    ERROR_RESPONSES,
    ChangePlanBody,
    StartSubscriptionBody,
    SubscriptionResponse,
    TransitionSubscriptionBody,
)

router = APIRouter(prefix="/subscriptions", tags=["subscriptions"], responses=ERROR_RESPONSES)


@router.post(
    "", response_model=SubscriptionResponse, status_code=201, summary="Start a subscription"
)
def start_subscription(
    body: StartSubscriptionBody,
    ctx: BillingRequestContext = Depends(require_org),
    app: BillingApplication = Depends(get_application),
) -> SubscriptionResponse:
    subscription = app.commands.dispatch(
        StartSubscription(
            actor_id=ctx.actor(),
            org_id=ctx.organization(),
            account_id=body.account_id,
            plan_code=body.plan_code,
            plan_version=body.plan_version,
            price_book_id=body.price_book_id,
            price_book_version=body.price_book_version,
            currency=body.currency,
            interval=body.interval,
            items=body.to_items(),
            trial_seconds=body.trial_seconds,
            contract_id=body.contract_id,
        )
    )
    return SubscriptionResponse.from_aggregate(subscription)


@router.get(
    "", response_model=list[SubscriptionResponse], summary="List an account's subscriptions"
)
def list_subscriptions(
    account_id: str,
    ctx: BillingRequestContext = Depends(require_org),
    app: BillingApplication = Depends(get_application),
) -> list[SubscriptionResponse]:
    views = app.queries.dispatch(
        ListSubscriptions(actor_id=ctx.actor(), org_id=ctx.organization(), account_id=account_id)
    )
    return [SubscriptionResponse.from_view(v) for v in views]


@router.get("/{subscription_id}", response_model=SubscriptionResponse, summary="Get a subscription")
def get_subscription(
    subscription_id: str,
    ctx: BillingRequestContext = Depends(require_org),
    app: BillingApplication = Depends(get_application),
) -> SubscriptionResponse:
    view = app.queries.dispatch(
        GetSubscription(
            actor_id=ctx.actor(), org_id=ctx.organization(), subscription_id=subscription_id
        )
    )
    if view is None:
        raise BillingNotFound(f"subscription {subscription_id!r} not found")
    return SubscriptionResponse.from_view(view)


@router.post(
    "/{subscription_id}/transitions",
    response_model=SubscriptionResponse,
    summary="Apply a lifecycle trigger",
)
def transition(
    subscription_id: str,
    body: TransitionSubscriptionBody,
    ctx: BillingRequestContext = Depends(require_org),
    app: BillingApplication = Depends(get_application),
) -> SubscriptionResponse:
    subscription = app.commands.dispatch(
        TransitionSubscription(
            actor_id=ctx.actor(),
            org_id=ctx.organization(),
            subscription_id=subscription_id,
            trigger=body.trigger,
        )
    )
    return SubscriptionResponse.from_aggregate(subscription)


@router.post(
    "/{subscription_id}/plan-change",
    summary="Compute the proration outcome for a plan change",
)
def change_plan(
    subscription_id: str,
    body: ChangePlanBody,
    ctx: BillingRequestContext = Depends(require_org),
    app: BillingApplication = Depends(get_application),
) -> dict:
    """The proration arithmetic is the Foundation's `ProrationService`; this endpoint only
    carries the request and serializes the outcome."""
    outcome = app.commands.dispatch(
        ChangeSubscriptionPlan(
            actor_id=ctx.actor(),
            org_id=ctx.organization(),
            subscription_id=subscription_id,
            is_expansion=body.is_expansion,
            immediate_downgrade=body.immediate_downgrade,
            price_delta=body.price_delta.to_money() if body.price_delta else None,
            at=body.at,
        )
    )
    return {
        "timing": outcome.timing.value,
        "behaviour": outcome.behaviour.value,
        "elapsed_seconds": outcome.elapsed_seconds,
        "remaining_seconds": outcome.remaining_seconds,
        "numerator": outcome.numerator,
        "denominator": outcome.denominator,
        "reason": outcome.reason,
    }
