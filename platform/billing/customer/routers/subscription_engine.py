"""Subscription Engine routes (M9.9) — transport only.

Additive to the M9.5–M9.8 transport layer and identical in discipline: parse, take identity from
the verified token, construct a command or query, dispatch, serialize. No subscription rule is
evaluated here — pin validity, item consistency, lifecycle legality, cycle advancement, trial
expiry, and schedule conflicts are all Foundation behaviour reached through the Application.

Mounted at **`/agreements`**, not under M9.5's `/subscriptions`. Same reason as M9.8's
`/billing-accounts`: `/subscriptions/{subscription_id}` is an existing wildcard, and any sibling
under it would be shadowed depending on router registration order. A separate prefix makes the
two surfaces independent of each other's ordering — a correctness property, not a naming
preference. The M9.5 routes are untouched.

**No endpoint here computes an amount, and none applies a scheduled change.** Scheduling records
intent; a caller dispatches the real command when the time comes, and the FSM validates it then.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query

from billing.application import (
    ActivateSubscription,
    AddSubscriptionItem,
    ArchiveSubscription,
    BillingApplication,
    CancelScheduledChange,
    CancelSubscription,
    ConvertSubscriptionTrial,
    CreateSubscription,
    ExpireSubscription,
    ExpireSubscriptionTrial,
    GetCommercialSubscription,
    GetSubscriptionSnapshot,
    ListScheduledChanges,
    MarkSubscriptionPastDue,
    PauseSubscription,
    PublishSubscriptionVersion,
    ReactivateSubscription,
    RecoverSubscription,
    RemoveSubscriptionItem,
    RenewSubscription,
    ResumeSubscription,
    RollbackSubscription,
    ScheduleSubscriptionChange,
    SearchSubscriptions,
    SetRenewalPolicy,
    StartSubscriptionTrial,
    SubscriptionTimeline,
    SubscriptionVersionHistory,
    SuspendSubscription,
    UpdateSubscriptionItem,
)
from billing.foundation import (
    AgreementItem,
    AgreementState,
    BillingInterval,
    BillingNotFound,
    Currency,
)

from ..auth import require_org
from ..container import get_application
from ..context import BillingRequestContext
from ..schemas import ERROR_RESPONSES
from ..subscription_schemas import (
    AddItemBody,
    CancelBody,
    CreateSubscriptionBody,
    PublishVersionBody,
    ReasonBody,
    RollbackBody,
    ScheduleChangeBody,
    ScheduledChangeResponse,
    SetRenewalBody,
    StartTrialBody,
    SubscriptionActivityResponse,
    SubscriptionResponse,
    SubscriptionSearchResponse,
    SubscriptionSnapshotResponse,
    SubscriptionVersionResponse,
    UpdateItemBody,
    build_subscription_query,
)

router = APIRouter(prefix="/agreements", tags=["subscriptions"], responses=ERROR_RESPONSES)


def _response(app: BillingApplication, ctx: BillingRequestContext, identifier: str):
    """Re-read through the query surface after a command.

    A command returns the aggregate, but the response also carries `allowed_triggers` — which
    lifecycle moves are legal next — and that is a Foundation decision. Reading it back keeps
    the computation below this layer instead of importing the state machine into a controller."""
    view = app.queries.dispatch(
        GetCommercialSubscription(
            actor_id=ctx.actor(), org_id=ctx.organization(), subscription_id=identifier
        )
    )
    if view is None:  # pragma: no cover — a just-written agreement is always readable
        raise BillingNotFound(f"subscription {identifier!r} not found")
    return SubscriptionResponse.from_view(view)


def _items(bodies) -> tuple[AgreementItem, ...]:
    """Wire items → Foundation items. Ids are minted by the Application's factory, so a
    controller never invents one."""
    return tuple(
        AgreementItem(
            id="",
            price_code=b.price_code,
            price_version=b.price_version,
            kind=b.kind,
            quantity=b.quantity,
            meter_code=b.meter_code,
            included_units=b.included_units,
            label=b.label,
        )
        for b in bodies
    )


# ── the agreement ───────────────────────────────────────────────────────────────


@router.post(
    "", response_model=SubscriptionResponse, status_code=201, summary="Create a draft subscription"
)
def create_subscription(
    body: CreateSubscriptionBody,
    ctx: BillingRequestContext = Depends(require_org),
    app: BillingApplication = Depends(get_application),
) -> SubscriptionResponse:
    """The three references — account, catalog version, pricing version — are fixed here and
    never change again. Moving onto newer terms publishes a new *version*."""
    subscription = app.commands.dispatch(
        CreateSubscription(
            actor_id=ctx.actor(),
            org_id=ctx.organization(),
            account_id=body.account_id,
            catalog_version=body.catalog_version,
            pricing_version=body.pricing_version,
            currency=body.currency,
            plan_code=body.plan_code,
            subscription_number=body.subscription_number,
            interval=body.interval,
            anchor_day=body.anchor_day,
            items=_items(body.items),
            trial=body.trial.to_policy(),
            renewal=body.renewal.to_policy(),
            pause=body.pause.to_policy(),
            cancellation=body.cancellation.to_policy(),
            contract_id=body.contract_id,
        )
    )
    return _response(app, ctx, subscription.id)


@router.get(
    "",
    response_model=SubscriptionSearchResponse,
    summary="Search subscriptions (deterministic filtering)",
)
def search_subscriptions(
    text: str = Query(default="", description="Case-insensitive substring; no relevance ranking"),
    states: list[AgreementState] = Query(default=[]),
    account_id: str = Query(default=""),
    plan_code: str = Query(default=""),
    currencies: list[Currency] = Query(default=[]),
    intervals: list[BillingInterval] = Query(default=[]),
    catalog_version: int | None = Query(default=None),
    pricing_version: int | None = Query(default=None),
    meter_code: str = Query(default=""),
    live_only: bool = Query(default=False),
    trialing_only: bool = Query(default=False),
    with_pending_changes: bool = Query(default=False),
    renews_automatically: bool | None = Query(default=None),
    active_at: float | None = Query(default=None),
    limit: int = Query(default=100, ge=0, le=500),
    offset: int = Query(default=0, ge=0),
    ctx: BillingRequestContext = Depends(require_org),
    app: BillingApplication = Depends(get_application),
) -> SubscriptionSearchResponse:
    view = app.queries.dispatch(
        SearchSubscriptions(
            actor_id=ctx.actor(),
            org_id=ctx.organization(),
            query=build_subscription_query(
                text=text,
                states=states,
                account_id=account_id,
                plan_code=plan_code,
                currencies=currencies,
                intervals=intervals,
                catalog_version=catalog_version,
                pricing_version=pricing_version,
                meter_code=meter_code,
                live_only=live_only,
                trialing_only=trialing_only,
                with_pending_changes=with_pending_changes,
                renews_automatically=renews_automatically,
                active_at=active_at,
                limit=limit,
                offset=offset,
            ),
        )
    )
    return SubscriptionSearchResponse.from_view(view)


@router.get(
    "/{identifier}",
    response_model=SubscriptionResponse,
    summary="Get a subscription by id or number",
)
def get_subscription(
    identifier: str,
    ctx: BillingRequestContext = Depends(require_org),
    app: BillingApplication = Depends(get_application),
) -> SubscriptionResponse:
    view = app.queries.dispatch(
        GetCommercialSubscription(
            actor_id=ctx.actor(), org_id=ctx.organization(), subscription_id=identifier
        )
    )
    if view is None:
        raise BillingNotFound(f"subscription {identifier!r} not found")
    return SubscriptionResponse.from_view(view)


# ── lifecycle ───────────────────────────────────────────────────────────────────


@router.post(
    "/{identifier}/activate",
    response_model=SubscriptionResponse,
    summary="Activate a draft (opens the first billing period)",
)
def activate(
    identifier: str,
    ctx: BillingRequestContext = Depends(require_org),
    app: BillingApplication = Depends(get_application),
) -> SubscriptionResponse:
    app.commands.dispatch(
        ActivateSubscription(
            actor_id=ctx.actor(), org_id=ctx.organization(), subscription_id=identifier
        )
    )
    return _response(app, ctx, identifier)


@router.post(
    "/{identifier}/trial/start", response_model=SubscriptionResponse, summary="Start the trial"
)
def start_trial(
    identifier: str,
    body: StartTrialBody,
    ctx: BillingRequestContext = Depends(require_org),
    app: BillingApplication = Depends(get_application),
) -> SubscriptionResponse:
    app.commands.dispatch(
        StartSubscriptionTrial(
            actor_id=ctx.actor(),
            org_id=ctx.organization(),
            subscription_id=identifier,
            length_days=body.length_days,
        )
    )
    return _response(app, ctx, identifier)


@router.post(
    "/{identifier}/trial/convert",
    response_model=SubscriptionResponse,
    summary="Convert a trial to an active agreement",
)
def convert_trial(
    identifier: str,
    ctx: BillingRequestContext = Depends(require_org),
    app: BillingApplication = Depends(get_application),
) -> SubscriptionResponse:
    """409 with the reason when conversion is blocked — the Foundation returns *why*, so a UI
    can show it before the customer clicks."""
    app.commands.dispatch(
        ConvertSubscriptionTrial(
            actor_id=ctx.actor(), org_id=ctx.organization(), subscription_id=identifier
        )
    )
    return _response(app, ctx, identifier)


@router.post(
    "/{identifier}/trial/expire", response_model=SubscriptionResponse, summary="Expire a trial"
)
def expire_trial(
    identifier: str,
    ctx: BillingRequestContext = Depends(require_org),
    app: BillingApplication = Depends(get_application),
) -> SubscriptionResponse:
    app.commands.dispatch(
        ExpireSubscriptionTrial(
            actor_id=ctx.actor(), org_id=ctx.organization(), subscription_id=identifier
        )
    )
    return _response(app, ctx, identifier)


@router.post(
    "/{identifier}/past-due",
    response_model=SubscriptionResponse,
    summary="Reflect a payment failure onto the agreement",
)
def mark_past_due(
    identifier: str,
    body: ReasonBody,
    ctx: BillingRequestContext = Depends(require_org),
    app: BillingApplication = Depends(get_application),
) -> SubscriptionResponse:
    """Whether a payment failed is the Payment domain's fact; this only reflects it here."""
    app.commands.dispatch(
        MarkSubscriptionPastDue(
            actor_id=ctx.actor(),
            org_id=ctx.organization(),
            subscription_id=identifier,
            reason=body.reason,
        )
    )
    return _response(app, ctx, identifier)


@router.post(
    "/{identifier}/recover",
    response_model=SubscriptionResponse,
    summary="Recover a past-due agreement",
)
def recover(
    identifier: str,
    ctx: BillingRequestContext = Depends(require_org),
    app: BillingApplication = Depends(get_application),
) -> SubscriptionResponse:
    app.commands.dispatch(
        RecoverSubscription(
            actor_id=ctx.actor(), org_id=ctx.organization(), subscription_id=identifier
        )
    )
    return _response(app, ctx, identifier)


@router.post(
    "/{identifier}/suspend", response_model=SubscriptionResponse, summary="Suspend an agreement"
)
def suspend(
    identifier: str,
    body: ReasonBody,
    ctx: BillingRequestContext = Depends(require_org),
    app: BillingApplication = Depends(get_application),
) -> SubscriptionResponse:
    app.commands.dispatch(
        SuspendSubscription(
            actor_id=ctx.actor(),
            org_id=ctx.organization(),
            subscription_id=identifier,
            reason=body.reason,
        )
    )
    return _response(app, ctx, identifier)


@router.post(
    "/{identifier}/reactivate",
    response_model=SubscriptionResponse,
    summary="Reactivate a suspended agreement",
)
def reactivate(
    identifier: str,
    ctx: BillingRequestContext = Depends(require_org),
    app: BillingApplication = Depends(get_application),
) -> SubscriptionResponse:
    app.commands.dispatch(
        ReactivateSubscription(
            actor_id=ctx.actor(), org_id=ctx.organization(), subscription_id=identifier
        )
    )
    return _response(app, ctx, identifier)


@router.post(
    "/{identifier}/pause",
    response_model=SubscriptionResponse,
    summary="Pause (entitlements are not automatically altered)",
)
def pause(
    identifier: str,
    body: ReasonBody,
    ctx: BillingRequestContext = Depends(require_org),
    app: BillingApplication = Depends(get_application),
) -> SubscriptionResponse:
    """409 when the agreed pause allowance is exhausted — a commercial limit, distinct from the
    FSM's structural one."""
    app.commands.dispatch(
        PauseSubscription(
            actor_id=ctx.actor(),
            org_id=ctx.organization(),
            subscription_id=identifier,
            reason=body.reason,
        )
    )
    return _response(app, ctx, identifier)


@router.post(
    "/{identifier}/resume", response_model=SubscriptionResponse, summary="Resume a paused agreement"
)
def resume(
    identifier: str,
    ctx: BillingRequestContext = Depends(require_org),
    app: BillingApplication = Depends(get_application),
) -> SubscriptionResponse:
    app.commands.dispatch(
        ResumeSubscription(
            actor_id=ctx.actor(), org_id=ctx.organization(), subscription_id=identifier
        )
    )
    return _response(app, ctx, identifier)


@router.post(
    "/{identifier}/cancel",
    response_model=SubscriptionResponse,
    summary="Cancel — immediately, at period end, or on a date",
)
def cancel(
    identifier: str,
    body: CancelBody,
    ctx: BillingRequestContext = Depends(require_org),
    app: BillingApplication = Depends(get_application),
) -> SubscriptionResponse:
    """Period-end is the default: a customer who has paid for a period keeps it."""
    app.commands.dispatch(
        CancelSubscription(
            actor_id=ctx.actor(),
            org_id=ctx.organization(),
            subscription_id=identifier,
            timing=body.timing,
            reason=body.reason,
            reason_code=body.reason_code,
        )
    )
    return _response(app, ctx, identifier)


@router.post(
    "/{identifier}/expire",
    response_model=SubscriptionResponse,
    summary="Expire a cancelled agreement",
)
def expire(
    identifier: str,
    ctx: BillingRequestContext = Depends(require_org),
    app: BillingApplication = Depends(get_application),
) -> SubscriptionResponse:
    app.commands.dispatch(
        ExpireSubscription(
            actor_id=ctx.actor(), org_id=ctx.organization(), subscription_id=identifier
        )
    )
    return _response(app, ctx, identifier)


@router.post(
    "/{identifier}/archive",
    response_model=SubscriptionResponse,
    summary="Archive an expired agreement (still fully readable)",
)
def archive(
    identifier: str,
    ctx: BillingRequestContext = Depends(require_org),
    app: BillingApplication = Depends(get_application),
) -> SubscriptionResponse:
    """There is no DELETE on this resource, in any state. A subscription is the commercial
    agreement itself, and ADR-0023 RS4 makes its record permanent."""
    app.commands.dispatch(
        ArchiveSubscription(
            actor_id=ctx.actor(), org_id=ctx.organization(), subscription_id=identifier
        )
    )
    return _response(app, ctx, identifier)


# ── items ───────────────────────────────────────────────────────────────────────


@router.post(
    "/{identifier}/items",
    response_model=SubscriptionResponse,
    status_code=201,
    summary="Add a subscription item",
)
def add_item(
    identifier: str,
    body: AddItemBody,
    ctx: BillingRequestContext = Depends(require_org),
    app: BillingApplication = Depends(get_application),
) -> SubscriptionResponse:
    """An item pins a price **code and version**. Several items is how hybrid commerce —
    recurring plus seats plus usage — is expressed on one agreement."""
    app.commands.dispatch(
        AddSubscriptionItem(
            actor_id=ctx.actor(),
            org_id=ctx.organization(),
            subscription_id=identifier,
            price_code=body.price_code,
            price_version=body.price_version,
            kind=body.kind,
            quantity=body.quantity,
            meter_code=body.meter_code,
            included_units=body.included_units,
            label=body.label,
        )
    )
    return _response(app, ctx, identifier)


@router.patch(
    "/{identifier}/items/{item_id}",
    response_model=SubscriptionResponse,
    summary="Amend a subscription item",
)
def update_item(
    identifier: str,
    item_id: str,
    body: UpdateItemBody,
    ctx: BillingRequestContext = Depends(require_org),
    app: BillingApplication = Depends(get_application),
) -> SubscriptionResponse:
    app.commands.dispatch(
        UpdateSubscriptionItem(
            actor_id=ctx.actor(),
            org_id=ctx.organization(),
            subscription_id=identifier,
            item_id=item_id,
            quantity=body.quantity,
            included_units=body.included_units,
            label=body.label,
        )
    )
    return _response(app, ctx, identifier)


@router.delete(
    "/{identifier}/items/{item_id}",
    response_model=SubscriptionResponse,
    summary="Remove a subscription item (the agreement itself is never deleted)",
)
def remove_item(
    identifier: str,
    item_id: str,
    ctx: BillingRequestContext = Depends(require_org),
    app: BillingApplication = Depends(get_application),
) -> SubscriptionResponse:
    app.commands.dispatch(
        RemoveSubscriptionItem(
            actor_id=ctx.actor(),
            org_id=ctx.organization(),
            subscription_id=identifier,
            item_id=item_id,
        )
    )
    return _response(app, ctx, identifier)


# ── scheduled changes ───────────────────────────────────────────────────────────


@router.post(
    "/{identifier}/scheduled-changes",
    response_model=SubscriptionResponse,
    status_code=201,
    summary="Schedule a future change (intent only — nothing fires it)",
)
def schedule_change(
    identifier: str,
    body: ScheduleChangeBody,
    ctx: BillingRequestContext = Depends(require_org),
    app: BillingApplication = Depends(get_application),
) -> SubscriptionResponse:
    """There is no scheduler in this plane. A caller dispatches the real command when the time
    comes, and the FSM validates it then — which is what keeps "what we agreed" separable from
    "what the system managed to run"."""
    app.commands.dispatch(
        ScheduleSubscriptionChange(
            actor_id=ctx.actor(),
            org_id=ctx.organization(),
            subscription_id=identifier,
            kind=body.kind,
            effective_at=body.effective_at,
            target_catalog_version=body.target_catalog_version,
            target_pricing_version=body.target_pricing_version,
            target_plan_code=body.target_plan_code,
            items=_items(body.items),
            timing=body.timing,
            reason=body.reason,
        )
    )
    return _response(app, ctx, identifier)


@router.get(
    "/{identifier}/scheduled-changes",
    response_model=list[ScheduledChangeResponse],
    summary="Pending scheduled changes",
)
def list_scheduled_changes(
    identifier: str,
    due_at: float | None = Query(
        default=None, description="Only changes due by this instant. A read — nothing is applied"
    ),
    ctx: BillingRequestContext = Depends(require_org),
    app: BillingApplication = Depends(get_application),
) -> list[ScheduledChangeResponse]:
    views = app.queries.dispatch(
        ListScheduledChanges(
            actor_id=ctx.actor(),
            org_id=ctx.organization(),
            subscription_id=identifier,
            due_at=due_at,
        )
    )
    return [ScheduledChangeResponse.from_view(v) for v in views]


@router.delete(
    "/{identifier}/scheduled-changes/{change_id}",
    response_model=SubscriptionResponse,
    summary="Cancel a pending scheduled change",
)
def cancel_scheduled_change(
    identifier: str,
    change_id: str,
    ctx: BillingRequestContext = Depends(require_org),
    app: BillingApplication = Depends(get_application),
) -> SubscriptionResponse:
    app.commands.dispatch(
        CancelScheduledChange(
            actor_id=ctx.actor(),
            org_id=ctx.organization(),
            subscription_id=identifier,
            change_id=change_id,
        )
    )
    return _response(app, ctx, identifier)


# ── renewal ─────────────────────────────────────────────────────────────────────


@router.post(
    "/{identifier}/renew",
    response_model=SubscriptionResponse,
    summary="Advance the billing cycle (not a state change, and not a scheduler)",
)
def renew(
    identifier: str,
    ctx: BillingRequestContext = Depends(require_org),
    app: BillingApplication = Depends(get_application),
) -> SubscriptionResponse:
    """409 when the agreed terms are exhausted — renewing beyond what was agreed is a
    commercial decision, not a computation."""
    app.commands.dispatch(
        RenewSubscription(
            actor_id=ctx.actor(), org_id=ctx.organization(), subscription_id=identifier
        )
    )
    return _response(app, ctx, identifier)


@router.patch(
    "/{identifier}/renewal",
    response_model=SubscriptionResponse,
    summary="Set the renewal policy",
)
def set_renewal(
    identifier: str,
    body: SetRenewalBody,
    ctx: BillingRequestContext = Depends(require_org),
    app: BillingApplication = Depends(get_application),
) -> SubscriptionResponse:
    app.commands.dispatch(
        SetRenewalPolicy(
            actor_id=ctx.actor(),
            org_id=ctx.organization(),
            subscription_id=identifier,
            renewal=body.to_policy(),
        )
    )
    return _response(app, ctx, identifier)


# ── versions, history, snapshot ─────────────────────────────────────────────────


@router.post(
    "/{identifier}/versions",
    response_model=SubscriptionVersionResponse,
    status_code=201,
    summary="Freeze the current terms into an immutable version",
)
def publish_version(
    identifier: str,
    body: PublishVersionBody,
    ctx: BillingRequestContext = Depends(require_org),
    app: BillingApplication = Depends(get_application),
) -> SubscriptionVersionResponse:
    """The version is immutable and readable forever; there is no PUT and no DELETE on it."""
    version = app.commands.dispatch(
        PublishSubscriptionVersion(
            actor_id=ctx.actor(),
            org_id=ctx.organization(),
            subscription_id=identifier,
            reason=body.reason,
        )
    )
    return SubscriptionVersionResponse.from_aggregate(version)


@router.post(
    "/{identifier}/versions/rollback",
    response_model=SubscriptionVersionResponse,
    status_code=201,
    summary="Roll back by publishing a new version with earlier terms",
)
def rollback(
    identifier: str,
    body: RollbackBody,
    ctx: BillingRequestContext = Depends(require_org),
    app: BillingApplication = Depends(get_application),
) -> SubscriptionVersionResponse:
    version = app.commands.dispatch(
        RollbackSubscription(
            actor_id=ctx.actor(),
            org_id=ctx.organization(),
            subscription_id=identifier,
            to_version=body.to_version,
            reason=body.reason,
        )
    )
    return SubscriptionVersionResponse.from_aggregate(version)


@router.get(
    "/{identifier}/versions",
    response_model=list[SubscriptionVersionResponse],
    summary="Version history (readable forever)",
)
def version_history(
    identifier: str,
    limit: int = Query(default=50, ge=1, le=500),
    ctx: BillingRequestContext = Depends(require_org),
    app: BillingApplication = Depends(get_application),
) -> list[SubscriptionVersionResponse]:
    views = app.queries.dispatch(
        SubscriptionVersionHistory(
            actor_id=ctx.actor(),
            org_id=ctx.organization(),
            subscription_id=identifier,
            limit=limit,
        )
    )
    return [SubscriptionVersionResponse.from_view(v) for v in views]


@router.get(
    "/{identifier}/timeline",
    response_model=list[SubscriptionActivityResponse],
    summary="Subscription timeline (append-only)",
)
def timeline(
    identifier: str,
    limit: int = Query(default=100, ge=1, le=500),
    ctx: BillingRequestContext = Depends(require_org),
    app: BillingApplication = Depends(get_application),
) -> list[SubscriptionActivityResponse]:
    views = app.queries.dispatch(
        SubscriptionTimeline(
            actor_id=ctx.actor(),
            org_id=ctx.organization(),
            subscription_id=identifier,
            limit=limit,
        )
    )
    return [SubscriptionActivityResponse.from_view(v) for v in views]


@router.get(
    "/{identifier}/snapshot",
    response_model=SubscriptionSnapshotResponse,
    summary="What agreement existed at an instant",
)
def snapshot(
    identifier: str,
    at: float | None = Query(default=None, description="Epoch seconds; omit for the current terms"),
    version: int | None = Query(
        default=None, description="Read from a frozen version instead of the live aggregate"
    ),
    ctx: BillingRequestContext = Depends(require_org),
    app: BillingApplication = Depends(get_application),
) -> SubscriptionSnapshotResponse:
    """A derived read model: everything in it is computed from the agreement, so there is no
    second copy of a commercial fact that can fall out of date."""
    view = app.queries.dispatch(
        GetSubscriptionSnapshot(
            actor_id=ctx.actor(),
            org_id=ctx.organization(),
            subscription_id=identifier,
            at=at,
            version=version,
        )
    )
    if view is None:
        raise BillingNotFound(f"subscription {identifier!r} not found")
    return SubscriptionSnapshotResponse.from_view(view)
