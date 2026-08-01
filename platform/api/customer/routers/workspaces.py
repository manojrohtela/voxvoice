"""Workspace + environment routes (M8.5), nested under an organization."""

from __future__ import annotations

from fastapi import APIRouter, Depends

from api.application import (
    Application,
    CreateEnvironment,
    CreateWorkspace,
    GetWorkspace,
    ListEnvironments,
    ListWorkspaces,
)
from api.foundation import NotFoundError

from ..auth import authenticate
from ..container import get_application
from ..context import RequestContext
from ..schemas import (
    ERROR_RESPONSES,
    CreateEnvironmentBody,
    CreateWorkspaceBody,
    EnvironmentResponse,
    WorkspaceResponse,
)

router = APIRouter(prefix="/v1/organizations/{org_id}", tags=["workspaces"])


@router.post(
    "/workspaces", response_model=WorkspaceResponse, status_code=201, responses=ERROR_RESPONSES,
)
def create_workspace(
    org_id: str,
    body: CreateWorkspaceBody,
    ctx: RequestContext = Depends(authenticate),
    app: Application = Depends(get_application),
) -> WorkspaceResponse:
    view = app.commands.dispatch(
        CreateWorkspace(actor_user_id=ctx.actor(), org_id=org_id, name=body.name, slug=body.slug)
    )
    return WorkspaceResponse.from_view(view)


@router.get(
    "/workspaces", response_model=list[WorkspaceResponse], responses=ERROR_RESPONSES,
)
def list_workspaces(
    org_id: str,
    _ctx: RequestContext = Depends(authenticate),
    app: Application = Depends(get_application),
) -> list[WorkspaceResponse]:
    views = app.queries.dispatch(ListWorkspaces(org_id))
    return [WorkspaceResponse.from_view(v) for v in views]


@router.get(
    "/workspaces/{workspace_id}", response_model=WorkspaceResponse, responses=ERROR_RESPONSES,
)
def get_workspace(
    org_id: str,
    workspace_id: str,
    _ctx: RequestContext = Depends(authenticate),
    app: Application = Depends(get_application),
) -> WorkspaceResponse:
    view = app.queries.dispatch(GetWorkspace(org_id, workspace_id))
    if view is None:
        raise NotFoundError(f"workspace {workspace_id!r} not found")
    return WorkspaceResponse.from_view(view)


@router.post(
    "/workspaces/{workspace_id}/environments",
    response_model=EnvironmentResponse, status_code=201,
    responses=ERROR_RESPONSES, tags=["environments"],
)
def create_environment(
    org_id: str,
    workspace_id: str,
    body: CreateEnvironmentBody,
    ctx: RequestContext = Depends(authenticate),
    app: Application = Depends(get_application),
) -> EnvironmentResponse:
    view = app.commands.dispatch(
        CreateEnvironment(
            actor_user_id=ctx.actor(), org_id=org_id, workspace_id=workspace_id,
            name=body.name, kind=body.kind,
        )
    )
    return EnvironmentResponse.from_view(view)


@router.get(
    "/workspaces/{workspace_id}/environments",
    response_model=list[EnvironmentResponse], responses=ERROR_RESPONSES, tags=["environments"],
)
def list_environments(
    org_id: str,
    workspace_id: str,
    _ctx: RequestContext = Depends(authenticate),
    app: Application = Depends(get_application),
) -> list[EnvironmentResponse]:
    views = app.queries.dispatch(ListEnvironments(org_id, workspace_id))
    return [EnvironmentResponse.from_view(v) for v in views]
