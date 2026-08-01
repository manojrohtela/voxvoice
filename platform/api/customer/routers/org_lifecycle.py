"""Organization Lifecycle + Invitation routes (M8.6) — additive, thin.

Extends the Organization surface with settings/rename (`PATCH`), soft-delete (`DELETE`),
activity (`GET .../activity`), and the invitation lifecycle (`.../invitations` +
accept/decline/cancel/resend). Every controller is transport-only: it maps the request to
an Application command/query and serializes the result — no business logic, no repository,
no transaction, no permission evaluation.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends

from api.application import (
    AcceptInvitation,
    ActivityFilter,
    Application,
    CancelInvitation,
    DeclineInvitation,
    DeleteOrganization,
    GetOrganizationActivity,
    GetRecentActivity,
    GetUserActivity,
    InviteToOrganization,
    ListPendingInvitations,
    ResendInvitation,
    UpdateOrganization,
)

from ..activity_params import activity_filter
from ..auth import authenticate
from ..container import get_application
from ..context import RequestContext
from ..schemas import (
    ERROR_RESPONSES,
    ActivityFeedResponse,
    ActivityResponse,
    InvitationResponse,
    InviteBody,
    OrganizationResponse,
    UpdateOrganizationBody,
)

router = APIRouter(prefix="/v1/organizations/{org_id}", tags=["organizations"])


@router.patch("", response_model=OrganizationResponse, responses=ERROR_RESPONSES)
def update_organization(
    org_id: str,
    body: UpdateOrganizationBody,
    ctx: RequestContext = Depends(authenticate),
    app: Application = Depends(get_application),
) -> OrganizationResponse:
    view = app.commands.dispatch(
        UpdateOrganization(
            actor_user_id=ctx.actor(), org_id=org_id, name=body.name,
            timezone=body.timezone, language=body.language, logo_url=body.logo_url,
            metadata=body.metadata,
        )
    )
    return OrganizationResponse.from_view(view)


@router.delete("", response_model=OrganizationResponse, responses=ERROR_RESPONSES)
def delete_organization(
    org_id: str,
    ctx: RequestContext = Depends(authenticate),
    app: Application = Depends(get_application),
) -> OrganizationResponse:
    """Soft-delete — marks the organization DELETED (row retained for audit/isolation)."""
    view = app.commands.dispatch(DeleteOrganization(actor_user_id=ctx.actor(), org_id=org_id))
    return OrganizationResponse.from_view(view)


@router.get("/activity", response_model=ActivityResponse, responses=ERROR_RESPONSES)
def organization_activity(
    org_id: str,
    _ctx: RequestContext = Depends(authenticate),
    filt: ActivityFilter = Depends(activity_filter),
    app: Application = Depends(get_application),
) -> ActivityResponse:
    """Organization activity — durable, paginated, filterable (M8.9)."""
    result = app.queries.dispatch(GetOrganizationActivity(org_id, filt))
    return ActivityResponse(**result)


@router.get("/activity/recent", response_model=ActivityFeedResponse, responses=ERROR_RESPONSES)
def recent_activity(
    org_id: str,
    _ctx: RequestContext = Depends(authenticate),
    filt: ActivityFilter = Depends(activity_filter),
    app: Application = Depends(get_application),
) -> ActivityFeedResponse:
    """Org-wide most-recent activity across every resource (the activity landing feed)."""
    return ActivityFeedResponse(**app.queries.dispatch(GetRecentActivity(org_id, filt)))


@router.get(
    "/activity/users/{user_id}",
    response_model=ActivityFeedResponse, responses=ERROR_RESPONSES,
)
def user_activity(
    org_id: str,
    user_id: str,
    _ctx: RequestContext = Depends(authenticate),
    filt: ActivityFilter = Depends(activity_filter),
    app: Application = Depends(get_application),
) -> ActivityFeedResponse:
    """Everything a given user did within the organization."""
    return ActivityFeedResponse(**app.queries.dispatch(GetUserActivity(org_id, user_id, filt)))


# ── invitations (tag: members) ────────────────────────────────────────────────────

_inv = APIRouter(prefix="/v1/organizations/{org_id}/invitations", tags=["members"])


@_inv.post("", response_model=InvitationResponse, status_code=201, responses=ERROR_RESPONSES)
def invite(
    org_id: str,
    body: InviteBody,
    ctx: RequestContext = Depends(authenticate),
    app: Application = Depends(get_application),
) -> InvitationResponse:
    d = app.commands.dispatch(
        InviteToOrganization(
            actor_user_id=ctx.actor(), org_id=org_id, user_id=body.user_id,
            tier=body.tier, ttl_seconds=body.ttl_seconds,
        )
    )
    return InvitationResponse.from_dict(d)


@_inv.get("", response_model=list[InvitationResponse], responses=ERROR_RESPONSES)
def list_pending(
    org_id: str,
    _ctx: RequestContext = Depends(authenticate),
    app: Application = Depends(get_application),
) -> list[InvitationResponse]:
    pending = app.queries.dispatch(ListPendingInvitations(org_id))
    return [InvitationResponse.from_dict(d) for d in pending]


@_inv.post("/{user_id}/accept", response_model=InvitationResponse, responses=ERROR_RESPONSES)
def accept(
    org_id: str,
    user_id: str,
    ctx: RequestContext = Depends(authenticate),
    app: Application = Depends(get_application),
) -> InvitationResponse:
    d = app.commands.dispatch(
        AcceptInvitation(actor_user_id=ctx.actor(), org_id=org_id, user_id=user_id)
    )
    return InvitationResponse.from_dict(d)


@_inv.post("/{user_id}/decline", response_model=InvitationResponse, responses=ERROR_RESPONSES)
def decline(
    org_id: str,
    user_id: str,
    ctx: RequestContext = Depends(authenticate),
    app: Application = Depends(get_application),
) -> InvitationResponse:
    d = app.commands.dispatch(
        DeclineInvitation(actor_user_id=ctx.actor(), org_id=org_id, user_id=user_id)
    )
    return InvitationResponse.from_dict(d)


@_inv.post("/{user_id}/cancel", response_model=InvitationResponse, responses=ERROR_RESPONSES)
def cancel(
    org_id: str,
    user_id: str,
    ctx: RequestContext = Depends(authenticate),
    app: Application = Depends(get_application),
) -> InvitationResponse:
    d = app.commands.dispatch(
        CancelInvitation(actor_user_id=ctx.actor(), org_id=org_id, user_id=user_id)
    )
    return InvitationResponse.from_dict(d)


@_inv.post("/{user_id}/resend", response_model=InvitationResponse, responses=ERROR_RESPONSES)
def resend(
    org_id: str,
    user_id: str,
    ctx: RequestContext = Depends(authenticate),
    app: Application = Depends(get_application),
) -> InvitationResponse:
    d = app.commands.dispatch(
        ResendInvitation(actor_user_id=ctx.actor(), org_id=org_id, user_id=user_id)
    )
    return InvitationResponse.from_dict(d)


# The invitations sub-router is exported alongside the main router.
invitations_router = _inv
