"""Agent Builder routes (M8.10) — additive, thin.

The Agent surface under a Project: create / list / search / get, update, archive / restore /
duplicate / soft-delete, publish / unpublish / rollback, and the immutable version history +
comparison + durable per-agent activity. Every controller is transport-only: it maps the
request to an Application command/query and serializes the result — no business logic, no
repository, no transaction, no permission evaluation (all of that lives below this layer).
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query

from api.application import (
    ActivityFilter,
    Application,
    ArchiveAgent,
    CompareAgentVersions,
    CreateAgent,
    DeleteAgent,
    DuplicateAgent,
    GetAgent,
    GetAgentActivity,
    GetAgentVersion,
    ListAgents,
    ListAgentVersions,
    PublishAgent,
    RestoreAgent,
    RollbackAgent,
    SearchAgents,
    UnpublishAgent,
    UpdateAgent,
)

from ..activity_params import activity_filter
from ..auth import authenticate
from ..container import get_application
from ..context import RequestContext
from ..schemas import (
    ERROR_RESPONSES,
    ActivityFeedResponse,
    AgentResponse,
    AgentSummaryResponse,
    AgentVersionDiffResponse,
    AgentVersionResponse,
    CreateAgentBody,
    DuplicateAgentBody,
    PublishAgentBody,
    RollbackAgentBody,
    UpdateAgentBody,
)

router = APIRouter(
    prefix="/v1/organizations/{org_id}/workspaces/{workspace_id}/projects/{project_id}/agents",
    tags=["agents"],
)


@router.post("", response_model=AgentResponse, status_code=201, responses=ERROR_RESPONSES)
def create_agent(
    org_id: str,
    workspace_id: str,
    project_id: str,
    body: CreateAgentBody,
    ctx: RequestContext = Depends(authenticate),
    app: Application = Depends(get_application),
) -> AgentResponse:
    view = app.commands.dispatch(
        CreateAgent(
            actor_user_id=ctx.actor(), org_id=org_id, workspace_id=workspace_id,
            project_id=project_id, name=body.name, description=body.description,
            icon=body.icon, category=body.category, tags=tuple(body.tags),
            language=body.language, greeting=body.greeting, config=body.config,
        )
    )
    return AgentResponse.from_view(view)


@router.get("", response_model=list[AgentSummaryResponse], responses=ERROR_RESPONSES)
def list_agents(
    org_id: str,
    workspace_id: str,
    project_id: str,
    include_archived: bool = Query(default=True),
    _ctx: RequestContext = Depends(authenticate),
    app: Application = Depends(get_application),
) -> list[AgentSummaryResponse]:
    results = app.queries.dispatch(
        ListAgents(org_id, project_id, include_archived=include_archived)
    )
    return [AgentSummaryResponse.from_dict(d) for d in results]


@router.get("/search", response_model=list[AgentSummaryResponse], responses=ERROR_RESPONSES)
def search_agents(
    org_id: str,
    workspace_id: str,
    project_id: str,
    q: str = Query(default="", description="case-insensitive name/description/tag substring"),
    _ctx: RequestContext = Depends(authenticate),
    app: Application = Depends(get_application),
) -> list[AgentSummaryResponse]:
    results = app.queries.dispatch(SearchAgents(org_id, project_id, q))
    return [AgentSummaryResponse.from_dict(d) for d in results]


@router.get("/{agent_id}", response_model=AgentResponse, responses=ERROR_RESPONSES)
def get_agent(
    org_id: str,
    workspace_id: str,
    project_id: str,
    agent_id: str,
    _ctx: RequestContext = Depends(authenticate),
    app: Application = Depends(get_application),
) -> AgentResponse:
    return AgentResponse.from_dict(app.queries.dispatch(GetAgent(org_id, project_id, agent_id)))


@router.patch("/{agent_id}", response_model=AgentResponse, responses=ERROR_RESPONSES)
def update_agent(
    org_id: str,
    workspace_id: str,
    project_id: str,
    agent_id: str,
    body: UpdateAgentBody,
    ctx: RequestContext = Depends(authenticate),
    app: Application = Depends(get_application),
) -> AgentResponse:
    view = app.commands.dispatch(
        UpdateAgent(
            actor_user_id=ctx.actor(), org_id=org_id, workspace_id=workspace_id,
            project_id=project_id, agent_id=agent_id, name=body.name,
            description=body.description, icon=body.icon, category=body.category,
            tags=tuple(body.tags) if body.tags is not None else None,
            language=body.language, greeting=body.greeting, config_patch=body.config_patch,
            metadata=body.metadata,
        )
    )
    return AgentResponse.from_view(view)


@router.delete("/{agent_id}", response_model=AgentResponse, responses=ERROR_RESPONSES)
def delete_agent(
    org_id: str,
    workspace_id: str,
    project_id: str,
    agent_id: str,
    ctx: RequestContext = Depends(authenticate),
    app: Application = Depends(get_application),
) -> AgentResponse:
    """Soft-delete — marks the agent DELETED (row retained for audit/isolation)."""
    view = app.commands.dispatch(
        DeleteAgent(ctx.actor(), org_id, workspace_id, project_id, agent_id)
    )
    return AgentResponse.from_view(view)


@router.post("/{agent_id}/archive", response_model=AgentResponse, responses=ERROR_RESPONSES)
def archive_agent(
    org_id: str,
    workspace_id: str,
    project_id: str,
    agent_id: str,
    ctx: RequestContext = Depends(authenticate),
    app: Application = Depends(get_application),
) -> AgentResponse:
    view = app.commands.dispatch(
        ArchiveAgent(ctx.actor(), org_id, workspace_id, project_id, agent_id)
    )
    return AgentResponse.from_view(view)


@router.post("/{agent_id}/restore", response_model=AgentResponse, responses=ERROR_RESPONSES)
def restore_agent(
    org_id: str,
    workspace_id: str,
    project_id: str,
    agent_id: str,
    ctx: RequestContext = Depends(authenticate),
    app: Application = Depends(get_application),
) -> AgentResponse:
    view = app.commands.dispatch(
        RestoreAgent(ctx.actor(), org_id, workspace_id, project_id, agent_id)
    )
    return AgentResponse.from_view(view)


@router.post("/{agent_id}/duplicate", response_model=AgentResponse, status_code=201,
             responses=ERROR_RESPONSES)
def duplicate_agent(
    org_id: str,
    workspace_id: str,
    project_id: str,
    agent_id: str,
    body: DuplicateAgentBody,
    ctx: RequestContext = Depends(authenticate),
    app: Application = Depends(get_application),
) -> AgentResponse:
    view = app.commands.dispatch(
        DuplicateAgent(ctx.actor(), org_id, workspace_id, project_id, agent_id, name=body.name)
    )
    return AgentResponse.from_view(view)


@router.post("/{agent_id}/publish", response_model=AgentResponse, responses=ERROR_RESPONSES)
def publish_agent(
    org_id: str,
    workspace_id: str,
    project_id: str,
    agent_id: str,
    body: PublishAgentBody,
    ctx: RequestContext = Depends(authenticate),
    app: Application = Depends(get_application),
) -> AgentResponse:
    """Publish — creates an immutable version and marks the agent PUBLISHED."""
    view = app.commands.dispatch(
        PublishAgent(
            ctx.actor(), org_id, workspace_id, project_id, agent_id,
            description=body.description,
        )
    )
    return AgentResponse.from_view(view)


@router.post("/{agent_id}/unpublish", response_model=AgentResponse, responses=ERROR_RESPONSES)
def unpublish_agent(
    org_id: str,
    workspace_id: str,
    project_id: str,
    agent_id: str,
    ctx: RequestContext = Depends(authenticate),
    app: Application = Depends(get_application),
) -> AgentResponse:
    view = app.commands.dispatch(
        UnpublishAgent(ctx.actor(), org_id, workspace_id, project_id, agent_id)
    )
    return AgentResponse.from_view(view)


@router.post("/{agent_id}/rollback", response_model=AgentResponse, responses=ERROR_RESPONSES)
def rollback_agent(
    org_id: str,
    workspace_id: str,
    project_id: str,
    agent_id: str,
    body: RollbackAgentBody,
    ctx: RequestContext = Depends(authenticate),
    app: Application = Depends(get_application),
) -> AgentResponse:
    """Roll back to a previous version — publishes a NEW version restored from the target."""
    view = app.commands.dispatch(
        RollbackAgent(
            ctx.actor(), org_id, workspace_id, project_id, agent_id,
            target_number=body.target_number,
        )
    )
    return AgentResponse.from_view(view)


# ── versioning (reuses the durable Audit Platform for the activity trail) ──────────


@router.get(
    "/{agent_id}/versions",
    response_model=list[AgentVersionResponse], responses=ERROR_RESPONSES,
)
def list_agent_versions(
    org_id: str,
    workspace_id: str,
    project_id: str,
    agent_id: str,
    _ctx: RequestContext = Depends(authenticate),
    app: Application = Depends(get_application),
) -> list[AgentVersionResponse]:
    versions = app.queries.dispatch(ListAgentVersions(org_id, agent_id))
    return [AgentVersionResponse.from_dict(d) for d in versions]


@router.get(
    "/{agent_id}/versions/compare",
    response_model=AgentVersionDiffResponse, responses=ERROR_RESPONSES,
)
def compare_agent_versions(
    org_id: str,
    workspace_id: str,
    project_id: str,
    agent_id: str,
    from_version: int = Query(alias="from"),
    to_version: int = Query(alias="to"),
    _ctx: RequestContext = Depends(authenticate),
    app: Application = Depends(get_application),
) -> AgentVersionDiffResponse:
    result = app.queries.dispatch(
        CompareAgentVersions(org_id, agent_id, from_version, to_version)
    )
    return AgentVersionDiffResponse(**result)


@router.get(
    "/{agent_id}/versions/{number}",
    response_model=AgentVersionResponse, responses=ERROR_RESPONSES,
)
def get_agent_version(
    org_id: str,
    workspace_id: str,
    project_id: str,
    agent_id: str,
    number: int,
    _ctx: RequestContext = Depends(authenticate),
    app: Application = Depends(get_application),
) -> AgentVersionResponse:
    return AgentVersionResponse.from_dict(
        app.queries.dispatch(GetAgentVersion(org_id, agent_id, number))
    )


@router.get(
    "/{agent_id}/activity",
    response_model=ActivityFeedResponse, responses=ERROR_RESPONSES,
)
def agent_activity(
    org_id: str,
    workspace_id: str,
    project_id: str,
    agent_id: str,
    _ctx: RequestContext = Depends(authenticate),
    filt: ActivityFilter = Depends(activity_filter),
    app: Application = Depends(get_application),
) -> ActivityFeedResponse:
    """An agent's own durable history (a projection over the M8.9 audit stream)."""
    return ActivityFeedResponse(**app.queries.dispatch(GetAgentActivity(org_id, agent_id, filt)))
