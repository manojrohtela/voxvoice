"""Project Lifecycle + Membership + Environment routes (M8.8) — additive, thin.

Extends the Project surface (whose create/list live in `projects.py`, M8.5) with settings/
rename (`PATCH`), archive/restore, activity, search, project membership, and per-user
environment selection/switching. Every controller is transport-only: it maps the request to
an Application command/query and serializes the result — no business logic, no repository, no
transaction, no permission evaluation.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query

from api.application import (
    ActivityFilter,
    AddProjectMember,
    Application,
    ArchiveProject,
    GetProjectActivity,
    GetProjectEnvironment,
    ListProjectMembers,
    RemoveProjectMember,
    RestoreProject,
    SearchProjects,
    SwitchProjectEnvironment,
    UpdateProject,
)

from ..activity_params import activity_filter
from ..auth import authenticate
from ..container import get_application
from ..context import RequestContext
from ..schemas import (
    ERROR_RESPONSES,
    AddProjectMemberBody,
    ProjectActivityResponse,
    ProjectEnvironmentResponse,
    ProjectMemberResponse,
    ProjectResponse,
    SwitchEnvironmentBody,
    UpdateProjectBody,
)

router = APIRouter(
    prefix="/v1/organizations/{org_id}/workspaces/{workspace_id}/projects",
    tags=["projects"],
)


@router.patch("/{project_id}", response_model=ProjectResponse, responses=ERROR_RESPONSES)
def update_project(
    org_id: str,
    workspace_id: str,
    project_id: str,
    body: UpdateProjectBody,
    ctx: RequestContext = Depends(authenticate),
    app: Application = Depends(get_application),
) -> ProjectResponse:
    view = app.commands.dispatch(
        UpdateProject(
            actor_user_id=ctx.actor(), org_id=org_id, workspace_id=workspace_id,
            project_id=project_id, name=body.name, description=body.description,
            icon=body.icon, metadata=body.metadata,
        )
    )
    return ProjectResponse.from_view(view)


@router.post(
    "/{project_id}/archive", response_model=ProjectResponse, responses=ERROR_RESPONSES
)
def archive_project(
    org_id: str,
    workspace_id: str,
    project_id: str,
    ctx: RequestContext = Depends(authenticate),
    app: Application = Depends(get_application),
) -> ProjectResponse:
    """Soft-archive — marks the project ARCHIVED (row retained; restorable)."""
    view = app.commands.dispatch(
        ArchiveProject(
            actor_user_id=ctx.actor(), org_id=org_id, workspace_id=workspace_id,
            project_id=project_id,
        )
    )
    return ProjectResponse.from_view(view)


@router.post(
    "/{project_id}/restore", response_model=ProjectResponse, responses=ERROR_RESPONSES
)
def restore_project(
    org_id: str,
    workspace_id: str,
    project_id: str,
    ctx: RequestContext = Depends(authenticate),
    app: Application = Depends(get_application),
) -> ProjectResponse:
    view = app.commands.dispatch(
        RestoreProject(
            actor_user_id=ctx.actor(), org_id=org_id, workspace_id=workspace_id,
            project_id=project_id,
        )
    )
    return ProjectResponse.from_view(view)


@router.get(
    "/{project_id}/activity",
    response_model=ProjectActivityResponse, responses=ERROR_RESPONSES,
)
def project_activity(
    org_id: str,
    workspace_id: str,
    project_id: str,
    _ctx: RequestContext = Depends(authenticate),
    filt: ActivityFilter = Depends(activity_filter),
    app: Application = Depends(get_application),
) -> ProjectActivityResponse:
    """Project activity — durable, paginated, filterable (M8.9)."""
    result = app.queries.dispatch(GetProjectActivity(org_id, workspace_id, project_id, filt))
    return ProjectActivityResponse(**result)


@router.get("/search", response_model=list[ProjectResponse], responses=ERROR_RESPONSES)
def search_projects(
    org_id: str,
    workspace_id: str,
    q: str = Query(default="", description="case-insensitive name/slug substring"),
    _ctx: RequestContext = Depends(authenticate),
    app: Application = Depends(get_application),
) -> list[ProjectResponse]:
    results = app.queries.dispatch(SearchProjects(org_id, workspace_id, q))
    return [ProjectResponse.from_dict(d) for d in results]


# ── project members ───────────────────────────────────────────────────────────────


@router.post(
    "/{project_id}/members",
    response_model=ProjectMemberResponse, status_code=201, responses=ERROR_RESPONSES,
)
def add_project_member(
    org_id: str,
    workspace_id: str,
    project_id: str,
    body: AddProjectMemberBody,
    ctx: RequestContext = Depends(authenticate),
    app: Application = Depends(get_application),
) -> ProjectMemberResponse:
    view = app.commands.dispatch(
        AddProjectMember(
            actor_user_id=ctx.actor(), org_id=org_id, workspace_id=workspace_id,
            project_id=project_id, user_id=body.user_id, role=body.role,
        )
    )
    return ProjectMemberResponse.from_view(view)


@router.get(
    "/{project_id}/members",
    response_model=list[ProjectMemberResponse], responses=ERROR_RESPONSES,
)
def list_project_members(
    org_id: str,
    workspace_id: str,
    project_id: str,
    _ctx: RequestContext = Depends(authenticate),
    app: Application = Depends(get_application),
) -> list[ProjectMemberResponse]:
    members = app.queries.dispatch(ListProjectMembers(org_id, project_id))
    return [ProjectMemberResponse.from_dict(d) for d in members]


@router.delete(
    "/{project_id}/members/{user_id}",
    response_model=ProjectMemberResponse, responses=ERROR_RESPONSES,
)
def remove_project_member(
    org_id: str,
    workspace_id: str,
    project_id: str,
    user_id: str,
    ctx: RequestContext = Depends(authenticate),
    app: Application = Depends(get_application),
) -> ProjectMemberResponse:
    view = app.commands.dispatch(
        RemoveProjectMember(
            actor_user_id=ctx.actor(), org_id=org_id, workspace_id=workspace_id,
            project_id=project_id, user_id=user_id,
        )
    )
    return ProjectMemberResponse.from_view(view)


# ── environment selector / switching (no deployment logic) ───────────────────────


@router.get(
    "/{project_id}/environment",
    response_model=ProjectEnvironmentResponse, responses=ERROR_RESPONSES,
    tags=["environments"],
)
def get_project_environment(
    org_id: str,
    workspace_id: str,
    project_id: str,
    ctx: RequestContext = Depends(authenticate),
    app: Application = Depends(get_application),
) -> ProjectEnvironmentResponse:
    """The environment selector for the current user (standard set + current selection)."""
    sel = app.queries.dispatch(GetProjectEnvironment(ctx.actor(), org_id, project_id))
    return ProjectEnvironmentResponse(**sel)


@router.post(
    "/{project_id}/environment", responses=ERROR_RESPONSES, tags=["environments"]
)
def switch_project_environment(
    org_id: str,
    workspace_id: str,
    project_id: str,
    body: SwitchEnvironmentBody,
    ctx: RequestContext = Depends(authenticate),
    app: Application = Depends(get_application),
) -> dict:
    """Switch the current user's active environment for this project (dev/staging/prod)."""
    return app.commands.dispatch(
        SwitchProjectEnvironment(
            actor_user_id=ctx.actor(), org_id=org_id, workspace_id=workspace_id,
            project_id=project_id, environment=body.environment.value,
        )
    )
