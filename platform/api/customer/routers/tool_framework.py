"""Tool Framework routes (M8.11) — additive, thin.

Two surfaces: the **tool catalog** under a Project (`…/projects/{pid}/tools`) — register/list/
search/get, update, enable/disable, archive/restore/soft-delete, activity — and the **agent↔tool
attachment** under an Agent (`…/agents/{agent_id}/tools`) — attach/list/detach, enable/disable,
configure, reorder. Every controller is transport-only: it maps the request to an Application
command/query and serializes the result — no business logic, no repository, no transaction, no
permission evaluation. **No tool is ever executed.**
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query

from api.application import (
    ActivityFilter,
    Application,
    ArchiveTool,
    AttachTool,
    ConfigureAgentTool,
    CreateTool,
    DeleteTool,
    DetachTool,
    DisableAgentTool,
    DisableTool,
    EnableAgentTool,
    EnableTool,
    GetTool,
    GetToolActivity,
    ListAgentTools,
    ListTools,
    ReorderAgentTools,
    RestoreTool,
    SearchTools,
    UpdateTool,
)

from ..activity_params import activity_filter
from ..auth import authenticate
from ..container import get_application
from ..context import RequestContext
from ..schemas import (
    ERROR_RESPONSES,
    ActivityFeedResponse,
    AgentToolResponse,
    AttachToolBody,
    ConfigureAgentToolBody,
    CreateToolBody,
    ReorderToolsBody,
    ToolResponse,
    ToolSummaryResponse,
    UpdateToolBody,
)

# ── tool catalog (within a project) ────────────────────────────────────────────────

router = APIRouter(
    prefix="/v1/organizations/{org_id}/workspaces/{workspace_id}/projects/{project_id}/tools",
    tags=["tools"],
)


@router.post("", response_model=ToolResponse, status_code=201, responses=ERROR_RESPONSES)
def create_tool(
    org_id: str,
    workspace_id: str,
    project_id: str,
    body: CreateToolBody,
    ctx: RequestContext = Depends(authenticate),
    app: Application = Depends(get_application),
) -> ToolResponse:
    view = app.commands.dispatch(
        CreateTool(
            actor_user_id=ctx.actor(), org_id=org_id, workspace_id=workspace_id,
            project_id=project_id, name=body.name, category=body.category,
            description=body.description, icon=body.icon, visibility=body.visibility,
            config=body.config,
        )
    )
    return ToolResponse.from_view(view)


@router.get("", response_model=list[ToolSummaryResponse], responses=ERROR_RESPONSES)
def list_tools(
    org_id: str,
    workspace_id: str,
    project_id: str,
    include_archived: bool = Query(default=True),
    _ctx: RequestContext = Depends(authenticate),
    app: Application = Depends(get_application),
) -> list[ToolSummaryResponse]:
    results = app.queries.dispatch(
        ListTools(org_id, project_id, include_archived=include_archived)
    )
    return [ToolSummaryResponse.from_dict(d) for d in results]


@router.get("/search", response_model=list[ToolSummaryResponse], responses=ERROR_RESPONSES)
def search_tools(
    org_id: str,
    workspace_id: str,
    project_id: str,
    q: str = Query(default="", description="case-insensitive name/description/category"),
    _ctx: RequestContext = Depends(authenticate),
    app: Application = Depends(get_application),
) -> list[ToolSummaryResponse]:
    results = app.queries.dispatch(SearchTools(org_id, project_id, q))
    return [ToolSummaryResponse.from_dict(d) for d in results]


@router.get("/{tool_id}", response_model=ToolResponse, responses=ERROR_RESPONSES)
def get_tool(
    org_id: str,
    workspace_id: str,
    project_id: str,
    tool_id: str,
    _ctx: RequestContext = Depends(authenticate),
    app: Application = Depends(get_application),
) -> ToolResponse:
    return ToolResponse.from_dict(app.queries.dispatch(GetTool(org_id, project_id, tool_id)))


@router.patch("/{tool_id}", response_model=ToolResponse, responses=ERROR_RESPONSES)
def update_tool(
    org_id: str,
    workspace_id: str,
    project_id: str,
    tool_id: str,
    body: UpdateToolBody,
    ctx: RequestContext = Depends(authenticate),
    app: Application = Depends(get_application),
) -> ToolResponse:
    view = app.commands.dispatch(
        UpdateTool(
            actor_user_id=ctx.actor(), org_id=org_id, workspace_id=workspace_id,
            project_id=project_id, tool_id=tool_id, name=body.name, category=body.category,
            description=body.description, icon=body.icon, visibility=body.visibility,
            config_patch=body.config_patch, metadata=body.metadata,
        )
    )
    return ToolResponse.from_view(view)


@router.delete("/{tool_id}", response_model=ToolResponse, responses=ERROR_RESPONSES)
def delete_tool(
    org_id: str,
    workspace_id: str,
    project_id: str,
    tool_id: str,
    ctx: RequestContext = Depends(authenticate),
    app: Application = Depends(get_application),
) -> ToolResponse:
    """Soft-delete — marks the tool DELETED (row retained for audit/isolation)."""
    view = app.commands.dispatch(
        DeleteTool(ctx.actor(), org_id, workspace_id, project_id, tool_id)
    )
    return ToolResponse.from_view(view)


@router.post("/{tool_id}/enable", response_model=ToolResponse, responses=ERROR_RESPONSES)
def enable_tool(
    org_id: str,
    workspace_id: str,
    project_id: str,
    tool_id: str,
    ctx: RequestContext = Depends(authenticate),
    app: Application = Depends(get_application),
) -> ToolResponse:
    view = app.commands.dispatch(
        EnableTool(ctx.actor(), org_id, workspace_id, project_id, tool_id)
    )
    return ToolResponse.from_view(view)


@router.post("/{tool_id}/disable", response_model=ToolResponse, responses=ERROR_RESPONSES)
def disable_tool(
    org_id: str,
    workspace_id: str,
    project_id: str,
    tool_id: str,
    ctx: RequestContext = Depends(authenticate),
    app: Application = Depends(get_application),
) -> ToolResponse:
    view = app.commands.dispatch(
        DisableTool(ctx.actor(), org_id, workspace_id, project_id, tool_id)
    )
    return ToolResponse.from_view(view)


@router.post("/{tool_id}/archive", response_model=ToolResponse, responses=ERROR_RESPONSES)
def archive_tool(
    org_id: str,
    workspace_id: str,
    project_id: str,
    tool_id: str,
    ctx: RequestContext = Depends(authenticate),
    app: Application = Depends(get_application),
) -> ToolResponse:
    view = app.commands.dispatch(
        ArchiveTool(ctx.actor(), org_id, workspace_id, project_id, tool_id)
    )
    return ToolResponse.from_view(view)


@router.post("/{tool_id}/restore", response_model=ToolResponse, responses=ERROR_RESPONSES)
def restore_tool(
    org_id: str,
    workspace_id: str,
    project_id: str,
    tool_id: str,
    ctx: RequestContext = Depends(authenticate),
    app: Application = Depends(get_application),
) -> ToolResponse:
    view = app.commands.dispatch(
        RestoreTool(ctx.actor(), org_id, workspace_id, project_id, tool_id)
    )
    return ToolResponse.from_view(view)


@router.get("/{tool_id}/activity", response_model=ActivityFeedResponse, responses=ERROR_RESPONSES)
def tool_activity(
    org_id: str,
    workspace_id: str,
    project_id: str,
    tool_id: str,
    _ctx: RequestContext = Depends(authenticate),
    filt: ActivityFilter = Depends(activity_filter),
    app: Application = Depends(get_application),
) -> ActivityFeedResponse:
    """A tool's own durable history (a projection over the M8.9 audit stream)."""
    return ActivityFeedResponse(**app.queries.dispatch(GetToolActivity(org_id, tool_id, filt)))


# ── agent↔tool attachment (within an agent) ─────────────────────────────────────────

attach_router = APIRouter(
    prefix=(
        "/v1/organizations/{org_id}/workspaces/{workspace_id}/projects/{project_id}"
        "/agents/{agent_id}/tools"
    ),
    tags=["agent-tools"],
)


@attach_router.post("", response_model=AgentToolResponse, status_code=201,
                    responses=ERROR_RESPONSES)
def attach_tool(
    org_id: str,
    workspace_id: str,
    project_id: str,
    agent_id: str,
    body: AttachToolBody,
    ctx: RequestContext = Depends(authenticate),
    app: Application = Depends(get_application),
) -> AgentToolResponse:
    view = app.commands.dispatch(
        AttachTool(
            actor_user_id=ctx.actor(), org_id=org_id, workspace_id=workspace_id,
            project_id=project_id, agent_id=agent_id, tool_id=body.tool_id,
            position=body.position,
        )
    )
    return AgentToolResponse.from_view(view)


@attach_router.get("", response_model=list[AgentToolResponse], responses=ERROR_RESPONSES)
def list_agent_tools(
    org_id: str,
    workspace_id: str,
    project_id: str,
    agent_id: str,
    _ctx: RequestContext = Depends(authenticate),
    app: Application = Depends(get_application),
) -> list[AgentToolResponse]:
    results = app.queries.dispatch(ListAgentTools(org_id, agent_id))
    return [AgentToolResponse.from_dict(d) for d in results]


@attach_router.post("/reorder", response_model=list[AgentToolResponse],
                    responses=ERROR_RESPONSES)
def reorder_agent_tools(
    org_id: str,
    workspace_id: str,
    project_id: str,
    agent_id: str,
    body: ReorderToolsBody,
    ctx: RequestContext = Depends(authenticate),
    app: Application = Depends(get_application),
) -> list[AgentToolResponse]:
    results = app.commands.dispatch(
        ReorderAgentTools(
            actor_user_id=ctx.actor(), org_id=org_id, workspace_id=workspace_id,
            project_id=project_id, agent_id=agent_id,
            ordered_tool_ids=tuple(body.ordered_tool_ids),
        )
    )
    return [AgentToolResponse.from_dict(d) for d in results]


@attach_router.delete("/{tool_id}", responses=ERROR_RESPONSES)
def detach_tool(
    org_id: str,
    workspace_id: str,
    project_id: str,
    agent_id: str,
    tool_id: str,
    ctx: RequestContext = Depends(authenticate),
    app: Application = Depends(get_application),
) -> dict:
    return app.commands.dispatch(
        DetachTool(ctx.actor(), org_id, workspace_id, project_id, agent_id, tool_id)
    )


@attach_router.post("/{tool_id}/enable", response_model=AgentToolResponse,
                    responses=ERROR_RESPONSES)
def enable_agent_tool(
    org_id: str,
    workspace_id: str,
    project_id: str,
    agent_id: str,
    tool_id: str,
    ctx: RequestContext = Depends(authenticate),
    app: Application = Depends(get_application),
) -> AgentToolResponse:
    view = app.commands.dispatch(
        EnableAgentTool(ctx.actor(), org_id, workspace_id, project_id, agent_id, tool_id)
    )
    return AgentToolResponse.from_view(view)


@attach_router.post("/{tool_id}/disable", response_model=AgentToolResponse,
                    responses=ERROR_RESPONSES)
def disable_agent_tool(
    org_id: str,
    workspace_id: str,
    project_id: str,
    agent_id: str,
    tool_id: str,
    ctx: RequestContext = Depends(authenticate),
    app: Application = Depends(get_application),
) -> AgentToolResponse:
    view = app.commands.dispatch(
        DisableAgentTool(ctx.actor(), org_id, workspace_id, project_id, agent_id, tool_id)
    )
    return AgentToolResponse.from_view(view)


@attach_router.patch("/{tool_id}", response_model=AgentToolResponse, responses=ERROR_RESPONSES)
def configure_agent_tool(
    org_id: str,
    workspace_id: str,
    project_id: str,
    agent_id: str,
    tool_id: str,
    body: ConfigureAgentToolBody,
    ctx: RequestContext = Depends(authenticate),
    app: Application = Depends(get_application),
) -> AgentToolResponse:
    view = app.commands.dispatch(
        ConfigureAgentTool(
            ctx.actor(), org_id, workspace_id, project_id, agent_id, tool_id,
            config_override=body.config_override,
        )
    )
    return AgentToolResponse.from_view(view)
