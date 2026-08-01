"""Project + preferences routes (M8.5)."""

from __future__ import annotations

from fastapi import APIRouter, Depends

from api.application import (
    Application,
    CreateProject,
    GetPreferences,
    ListProjects,
    UpdatePreferences,
)

from ..auth import authenticate
from ..container import get_application
from ..context import RequestContext
from ..schemas import (
    ERROR_RESPONSES,
    CreateProjectBody,
    PreferencesResponse,
    ProjectResponse,
    UpdatePreferencesBody,
)

router = APIRouter(prefix="/v1/organizations/{org_id}", tags=["projects"])


@router.post(
    "/workspaces/{workspace_id}/projects",
    response_model=ProjectResponse, status_code=201, responses=ERROR_RESPONSES,
)
def create_project(
    org_id: str,
    workspace_id: str,
    body: CreateProjectBody,
    ctx: RequestContext = Depends(authenticate),
    app: Application = Depends(get_application),
) -> ProjectResponse:
    view = app.commands.dispatch(
        CreateProject(
            actor_user_id=ctx.actor(), org_id=org_id, workspace_id=workspace_id,
            name=body.name, slug=body.slug,
        )
    )
    return ProjectResponse.from_view(view)


@router.get(
    "/workspaces/{workspace_id}/projects",
    response_model=list[ProjectResponse], responses=ERROR_RESPONSES,
)
def list_projects(
    org_id: str,
    workspace_id: str,
    _ctx: RequestContext = Depends(authenticate),
    app: Application = Depends(get_application),
) -> list[ProjectResponse]:
    views = app.queries.dispatch(ListProjects(org_id, workspace_id))
    return [ProjectResponse.from_view(v) for v in views]


@router.get(
    "/preferences", response_model=PreferencesResponse, responses=ERROR_RESPONSES,
    tags=["preferences"],
)
def get_preferences(
    org_id: str,
    ctx: RequestContext = Depends(authenticate),
    app: Application = Depends(get_application),
) -> PreferencesResponse:
    view = app.queries.dispatch(GetPreferences(org_id, ctx.actor()))
    return PreferencesResponse.from_view(view)


@router.put(
    "/preferences", response_model=PreferencesResponse, responses=ERROR_RESPONSES,
    tags=["preferences"],
)
def update_preferences(
    org_id: str,
    body: UpdatePreferencesBody,
    ctx: RequestContext = Depends(authenticate),
    app: Application = Depends(get_application),
) -> PreferencesResponse:
    view = app.commands.dispatch(
        UpdatePreferences(
            actor_user_id=ctx.actor(), org_id=org_id, theme=body.theme, locale=body.locale,
            keyboard_shortcuts_enabled=body.keyboard_shortcuts_enabled, favorites=body.favorites,
        )
    )
    return PreferencesResponse.from_view(view)
