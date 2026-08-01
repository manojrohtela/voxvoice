"""Member + role routes (M8.5), nested under an organization."""

from __future__ import annotations

from fastapi import APIRouter, Depends

from api.application import (
    Application,
    GrantRole,
    InviteMember,
    ListMembers,
    RemoveMember,
)

from ..auth import authenticate
from ..container import get_application
from ..context import RequestContext
from ..schemas import ERROR_RESPONSES, GrantRoleBody, InviteMemberBody, MemberResponse

router = APIRouter(prefix="/v1/organizations/{org_id}/members", tags=["members"])


@router.post("", response_model=MemberResponse, status_code=201, responses=ERROR_RESPONSES)
def invite_member(
    org_id: str,
    body: InviteMemberBody,
    ctx: RequestContext = Depends(authenticate),
    app: Application = Depends(get_application),
) -> MemberResponse:
    view = app.commands.dispatch(
        InviteMember(actor_user_id=ctx.actor(), org_id=org_id, user_id=body.user_id)
    )
    return MemberResponse.from_view(view)


@router.get("", response_model=list[MemberResponse], responses=ERROR_RESPONSES)
def list_members(
    org_id: str,
    _ctx: RequestContext = Depends(authenticate),
    app: Application = Depends(get_application),
) -> list[MemberResponse]:
    views = app.queries.dispatch(ListMembers(org_id))
    return [MemberResponse.from_view(v) for v in views]


@router.post(
    "/{user_id}/roles", response_model=MemberResponse, status_code=201, responses=ERROR_RESPONSES,
)
def grant_role(
    org_id: str,
    user_id: str,
    body: GrantRoleBody,
    ctx: RequestContext = Depends(authenticate),
    app: Application = Depends(get_application),
) -> MemberResponse:
    view = app.commands.dispatch(
        GrantRole(
            actor_user_id=ctx.actor(), org_id=org_id, user_id=user_id,
            role=body.role, scope=body.scope, scope_id=body.scope_id,
        )
    )
    return MemberResponse.from_view(view)


@router.delete("/{user_id}", response_model=MemberResponse, responses=ERROR_RESPONSES)
def remove_member(
    org_id: str,
    user_id: str,
    ctx: RequestContext = Depends(authenticate),
    app: Application = Depends(get_application),
) -> MemberResponse:
    """Soft-remove (deactivate) a member — maps to the Foundation SUSPENDED transition."""
    view = app.commands.dispatch(
        RemoveMember(actor_user_id=ctx.actor(), org_id=org_id, user_id=user_id)
    )
    return MemberResponse.from_view(view)
