"""Conversation Runtime routes (M8.12) — additive, thin.

The runtime session surface under a published Agent: create / list / get, resume, close, and
inspect the resolved runtime **state** and **execution plan**. Every controller is transport-
only: it maps the request to an Application command/query and serializes the result — no
business logic, no repository, no transaction, no permission evaluation. **No LLM call, no tool
execution, no voice synthesis** — the runtime only plans (deterministically) and the inspector
reads that plan.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends

from api.application import (
    Application,
    CloseSession,
    CreateSession,
    GetExecutionPlan,
    GetRuntimeState,
    GetSession,
    ListSessions,
    ResumeSession,
)

from ..auth import authenticate
from ..container import get_application
from ..context import RequestContext
from ..schemas import (
    ERROR_RESPONSES,
    CreateSessionBody,
    SessionResponse,
    SessionSummaryResponse,
)

router = APIRouter(
    prefix=(
        "/v1/organizations/{org_id}/workspaces/{workspace_id}/projects/{project_id}"
        "/agents/{agent_id}/sessions"
    ),
    tags=["runtime"],
)


@router.post("", response_model=SessionResponse, status_code=201, responses=ERROR_RESPONSES)
def create_session(
    org_id: str,
    workspace_id: str,
    project_id: str,
    agent_id: str,
    body: CreateSessionBody,
    ctx: RequestContext = Depends(authenticate),
    app: Application = Depends(get_application),
) -> SessionResponse:
    """Create a runtime session for the agent's **published** version (a draft never runs)."""
    view = app.commands.dispatch(
        CreateSession(
            actor_user_id=ctx.actor(), org_id=org_id, workspace_id=workspace_id,
            project_id=project_id, agent_id=agent_id, runtime_variables=body.runtime_variables,
            correlation_id=body.correlation_id, metadata=body.metadata,
        )
    )
    return SessionResponse.from_view(view)


@router.get("", response_model=list[SessionSummaryResponse], responses=ERROR_RESPONSES)
def list_sessions(
    org_id: str,
    workspace_id: str,
    project_id: str,
    agent_id: str,
    _ctx: RequestContext = Depends(authenticate),
    app: Application = Depends(get_application),
) -> list[SessionSummaryResponse]:
    results = app.queries.dispatch(ListSessions(org_id, agent_id))
    return [SessionSummaryResponse.from_dict(d) for d in results]


@router.get("/{session_id}", response_model=SessionResponse, responses=ERROR_RESPONSES)
def get_session(
    org_id: str,
    workspace_id: str,
    project_id: str,
    agent_id: str,
    session_id: str,
    _ctx: RequestContext = Depends(authenticate),
    app: Application = Depends(get_application),
) -> SessionResponse:
    return SessionResponse.from_dict(app.queries.dispatch(GetSession(org_id, session_id)))


@router.post("/{session_id}/resume", response_model=SessionResponse, responses=ERROR_RESPONSES)
def resume_session(
    org_id: str,
    workspace_id: str,
    project_id: str,
    agent_id: str,
    session_id: str,
    ctx: RequestContext = Depends(authenticate),
    app: Application = Depends(get_application),
) -> SessionResponse:
    view = app.commands.dispatch(
        ResumeSession(ctx.actor(), org_id, workspace_id, project_id, session_id)
    )
    return SessionResponse.from_view(view)


@router.post("/{session_id}/close", response_model=SessionResponse, responses=ERROR_RESPONSES)
def close_session(
    org_id: str,
    workspace_id: str,
    project_id: str,
    agent_id: str,
    session_id: str,
    ctx: RequestContext = Depends(authenticate),
    app: Application = Depends(get_application),
) -> SessionResponse:
    view = app.commands.dispatch(
        CloseSession(ctx.actor(), org_id, workspace_id, project_id, session_id)
    )
    return SessionResponse.from_view(view)


@router.get("/{session_id}/state", responses=ERROR_RESPONSES)
def get_runtime_state(
    org_id: str,
    workspace_id: str,
    project_id: str,
    agent_id: str,
    session_id: str,
    _ctx: RequestContext = Depends(authenticate),
    app: Application = Depends(get_application),
) -> dict:
    """The resolved runtime state (prompt / variables / tool plan / metadata), secrets redacted."""
    return app.queries.dispatch(GetRuntimeState(org_id, session_id))


@router.get("/{session_id}/plan", responses=ERROR_RESPONSES)
def get_execution_plan(
    org_id: str,
    workspace_id: str,
    project_id: str,
    agent_id: str,
    session_id: str,
    _ctx: RequestContext = Depends(authenticate),
    app: Application = Depends(get_application),
) -> dict:
    """The execution plan: resolved prompt, resolved variables, tool plan, timing, diagnostics."""
    return app.queries.dispatch(GetExecutionPlan(org_id, session_id))
