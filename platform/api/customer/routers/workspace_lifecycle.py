"""Workspace Lifecycle + Membership + Navigation routes (M8.7) — additive, thin.

Extends the Workspace surface (whose create/list/get/environments live in `workspaces.py`,
M8.5) with settings/rename (`PATCH`), archive/restore, activity, workspace membership, and
per-user navigation (switcher/recent/favorite/default/search). Every controller is
transport-only: it maps the request to an Application command/query and serializes the
result — no business logic, no repository, no transaction, no permission evaluation.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query

from api.application import (
    ActivityFilter,
    AddWorkspaceMember,
    Application,
    ArchiveWorkspace,
    FavoriteWorkspace,
    GetWorkspaceActivity,
    GetWorkspaceNavigation,
    ListWorkspaceMembers,
    RecordRecentWorkspace,
    RemoveWorkspaceMember,
    RestoreWorkspace,
    SearchWorkspaces,
    SetDefaultWorkspace,
    UpdateWorkspace,
)

from ..activity_params import activity_filter
from ..auth import authenticate
from ..container import get_application
from ..context import RequestContext
from ..schemas import (
    ERROR_RESPONSES,
    AddWorkspaceMemberBody,
    UpdateWorkspaceBody,
    WorkspaceActivityResponse,
    WorkspaceMemberResponse,
    WorkspaceNavigationResponse,
    WorkspaceResponse,
)

router = APIRouter(
    prefix="/v1/organizations/{org_id}/workspaces", tags=["workspaces"]
)


@router.patch(
    "/{workspace_id}", response_model=WorkspaceResponse, responses=ERROR_RESPONSES
)
def update_workspace(
    org_id: str,
    workspace_id: str,
    body: UpdateWorkspaceBody,
    ctx: RequestContext = Depends(authenticate),
    app: Application = Depends(get_application),
) -> WorkspaceResponse:
    view = app.commands.dispatch(
        UpdateWorkspace(
            actor_user_id=ctx.actor(), org_id=org_id, workspace_id=workspace_id,
            name=body.name, description=body.description, icon=body.icon,
            color=body.color, metadata=body.metadata,
        )
    )
    return WorkspaceResponse.from_view(view)


@router.post(
    "/{workspace_id}/archive", response_model=WorkspaceResponse, responses=ERROR_RESPONSES
)
def archive_workspace(
    org_id: str,
    workspace_id: str,
    ctx: RequestContext = Depends(authenticate),
    app: Application = Depends(get_application),
) -> WorkspaceResponse:
    """Soft-archive — marks the workspace ARCHIVED (row retained; restorable)."""
    view = app.commands.dispatch(
        ArchiveWorkspace(actor_user_id=ctx.actor(), org_id=org_id, workspace_id=workspace_id)
    )
    return WorkspaceResponse.from_view(view)


@router.post(
    "/{workspace_id}/restore", response_model=WorkspaceResponse, responses=ERROR_RESPONSES
)
def restore_workspace(
    org_id: str,
    workspace_id: str,
    ctx: RequestContext = Depends(authenticate),
    app: Application = Depends(get_application),
) -> WorkspaceResponse:
    view = app.commands.dispatch(
        RestoreWorkspace(actor_user_id=ctx.actor(), org_id=org_id, workspace_id=workspace_id)
    )
    return WorkspaceResponse.from_view(view)


@router.get(
    "/{workspace_id}/activity",
    response_model=WorkspaceActivityResponse, responses=ERROR_RESPONSES,
)
def workspace_activity(
    org_id: str,
    workspace_id: str,
    _ctx: RequestContext = Depends(authenticate),
    filt: ActivityFilter = Depends(activity_filter),
    app: Application = Depends(get_application),
) -> WorkspaceActivityResponse:
    """Workspace activity — durable, paginated, filterable (M8.9)."""
    result = app.queries.dispatch(GetWorkspaceActivity(org_id, workspace_id, filt))
    return WorkspaceActivityResponse(**result)


@router.get(
    "/search", response_model=list[WorkspaceResponse], responses=ERROR_RESPONSES
)
def search_workspaces(
    org_id: str,
    q: str = Query(default="", description="case-insensitive name/slug substring"),
    _ctx: RequestContext = Depends(authenticate),
    app: Application = Depends(get_application),
) -> list[WorkspaceResponse]:
    results = app.queries.dispatch(SearchWorkspaces(org_id, q))
    return [WorkspaceResponse.from_dict(d) for d in results]


# ── workspace members ─────────────────────────────────────────────────────────────


@router.post(
    "/{workspace_id}/members",
    response_model=WorkspaceMemberResponse, status_code=201, responses=ERROR_RESPONSES,
)
def add_workspace_member(
    org_id: str,
    workspace_id: str,
    body: AddWorkspaceMemberBody,
    ctx: RequestContext = Depends(authenticate),
    app: Application = Depends(get_application),
) -> WorkspaceMemberResponse:
    view = app.commands.dispatch(
        AddWorkspaceMember(
            actor_user_id=ctx.actor(), org_id=org_id, workspace_id=workspace_id,
            user_id=body.user_id, role=body.role,
        )
    )
    return WorkspaceMemberResponse.from_view(view)


@router.get(
    "/{workspace_id}/members",
    response_model=list[WorkspaceMemberResponse], responses=ERROR_RESPONSES,
)
def list_workspace_members(
    org_id: str,
    workspace_id: str,
    _ctx: RequestContext = Depends(authenticate),
    app: Application = Depends(get_application),
) -> list[WorkspaceMemberResponse]:
    members = app.queries.dispatch(ListWorkspaceMembers(org_id, workspace_id))
    return [WorkspaceMemberResponse.from_dict(d) for d in members]


@router.delete(
    "/{workspace_id}/members/{user_id}",
    response_model=WorkspaceMemberResponse, responses=ERROR_RESPONSES,
)
def remove_workspace_member(
    org_id: str,
    workspace_id: str,
    user_id: str,
    ctx: RequestContext = Depends(authenticate),
    app: Application = Depends(get_application),
) -> WorkspaceMemberResponse:
    view = app.commands.dispatch(
        RemoveWorkspaceMember(
            actor_user_id=ctx.actor(), org_id=org_id, workspace_id=workspace_id,
            user_id=user_id,
        )
    )
    return WorkspaceMemberResponse.from_view(view)


# ── navigation (switcher / recent / favorite / default) ──────────────────────────

_nav = APIRouter(prefix="/v1/organizations/{org_id}/workspace-navigation", tags=["workspaces"])


@_nav.get("", response_model=WorkspaceNavigationResponse, responses=ERROR_RESPONSES)
def get_navigation(
    org_id: str,
    ctx: RequestContext = Depends(authenticate),
    app: Application = Depends(get_application),
) -> WorkspaceNavigationResponse:
    """The workspace switcher payload for the current user."""
    nav = app.queries.dispatch(GetWorkspaceNavigation(org_id, ctx.actor()))
    return WorkspaceNavigationResponse(**nav)


@_nav.post("/{workspace_id}/default", responses=ERROR_RESPONSES)
def set_default(
    org_id: str,
    workspace_id: str,
    ctx: RequestContext = Depends(authenticate),
    app: Application = Depends(get_application),
) -> dict:
    return app.commands.dispatch(
        SetDefaultWorkspace(actor_user_id=ctx.actor(), org_id=org_id, workspace_id=workspace_id)
    )


@_nav.post("/{workspace_id}/favorite", responses=ERROR_RESPONSES)
def toggle_favorite(
    org_id: str,
    workspace_id: str,
    ctx: RequestContext = Depends(authenticate),
    app: Application = Depends(get_application),
) -> dict:
    return app.commands.dispatch(
        FavoriteWorkspace(actor_user_id=ctx.actor(), org_id=org_id, workspace_id=workspace_id)
    )


@_nav.post("/{workspace_id}/recent", responses=ERROR_RESPONSES)
def record_recent(
    org_id: str,
    workspace_id: str,
    ctx: RequestContext = Depends(authenticate),
    app: Application = Depends(get_application),
) -> dict:
    return app.commands.dispatch(
        RecordRecentWorkspace(
            actor_user_id=ctx.actor(), org_id=org_id, workspace_id=workspace_id
        )
    )


# The navigation sub-router is exported alongside the main router.
navigation_router = _nav
