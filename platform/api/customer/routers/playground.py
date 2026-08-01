"""Conversation Playground routes (M8.13) — additive, thin.

The developer playground surface under a published Agent: start / continue (send a turn) /
inspect / reset / close a conversation, all driven by the **mock runtime**. Prompt / variable /
tool-plan inspection **reuses the M8.12 runtime query** (`GetExecutionPlan`). Every controller is
transport-only: it maps the request to an Application command/query and serializes the result —
no business logic, no repository, no transaction, no permission evaluation. **No real provider,
no real LLM, no real tool execution, no audio synthesis, no deployment.**
"""

from __future__ import annotations

from fastapi import APIRouter, Depends

from api.application import (
    Application,
    CloseConversation,
    ContinueConversation,
    GetAdapterOutputs,
    GetConversation,
    GetExecutionPlan,
    ListConversationTurns,
    ResetConversation,
    StartConversation,
)

from ..auth import authenticate
from ..container import get_application
from ..context import RequestContext
from ..schemas import (
    ERROR_RESPONSES,
    ContinueTurnBody,
    ConversationResponse,
    SessionResponse,
    StartConversationBody,
    TurnResponse,
)

router = APIRouter(
    prefix=(
        "/v1/organizations/{org_id}/workspaces/{workspace_id}/projects/{project_id}"
        "/agents/{agent_id}/playground/conversations"
    ),
    tags=["playground"],
)


@router.post("", response_model=SessionResponse, status_code=201, responses=ERROR_RESPONSES)
def start_conversation(
    org_id: str,
    workspace_id: str,
    project_id: str,
    agent_id: str,
    body: StartConversationBody,
    ctx: RequestContext = Depends(authenticate),
    app: Application = Depends(get_application),
) -> SessionResponse:
    """Start a playground conversation for the agent's **published** version (mock runtime)."""
    view = app.commands.dispatch(
        StartConversation(
            actor_user_id=ctx.actor(), org_id=org_id, workspace_id=workspace_id,
            project_id=project_id, agent_id=agent_id,
            runtime_variables=body.runtime_variables, metadata=body.metadata,
        )
    )
    return SessionResponse.from_view(view)


@router.get("/{session_id}", response_model=ConversationResponse, responses=ERROR_RESPONSES)
def get_conversation(
    org_id: str,
    workspace_id: str,
    project_id: str,
    agent_id: str,
    session_id: str,
    _ctx: RequestContext = Depends(authenticate),
    app: Application = Depends(get_application),
) -> ConversationResponse:
    return ConversationResponse.from_dict(app.queries.dispatch(GetConversation(org_id, session_id)))


@router.post("/{session_id}/turns", response_model=TurnResponse, status_code=201,
             responses=ERROR_RESPONSES)
def continue_conversation(
    org_id: str,
    workspace_id: str,
    project_id: str,
    agent_id: str,
    session_id: str,
    body: ContinueTurnBody,
    ctx: RequestContext = Depends(authenticate),
    app: Application = Depends(get_application),
) -> TurnResponse:
    """Send one turn — run the mock adapters over the resolved plan and append the turn."""
    view = app.commands.dispatch(
        ContinueConversation(
            actor_user_id=ctx.actor(), org_id=org_id, workspace_id=workspace_id,
            project_id=project_id, session_id=session_id, user_input=body.user_input,
        )
    )
    return TurnResponse.from_view(view)


@router.get("/{session_id}/turns", response_model=list[TurnResponse], responses=ERROR_RESPONSES)
def list_turns(
    org_id: str,
    workspace_id: str,
    project_id: str,
    agent_id: str,
    session_id: str,
    _ctx: RequestContext = Depends(authenticate),
    app: Application = Depends(get_application),
) -> list[TurnResponse]:
    results = app.queries.dispatch(ListConversationTurns(org_id, session_id))
    return [TurnResponse.from_dict(d) for d in results]


@router.get("/{session_id}/plan", responses=ERROR_RESPONSES)
def inspect_plan(
    org_id: str,
    workspace_id: str,
    project_id: str,
    agent_id: str,
    session_id: str,
    _ctx: RequestContext = Depends(authenticate),
    app: Application = Depends(get_application),
) -> dict:
    """Prompt / variable / tool-plan inspection — reuses the M8.12 runtime query."""
    return app.queries.dispatch(GetExecutionPlan(org_id, session_id))


@router.get("/{session_id}/adapters", responses=ERROR_RESPONSES)
def inspect_adapters(
    org_id: str,
    workspace_id: str,
    project_id: str,
    agent_id: str,
    session_id: str,
    _ctx: RequestContext = Depends(authenticate),
    app: Application = Depends(get_application),
) -> dict:
    """The bound mock adapters + the latest turn's adapter outputs."""
    return app.queries.dispatch(GetAdapterOutputs(org_id, session_id))


@router.post("/{session_id}/reset", response_model=SessionResponse, responses=ERROR_RESPONSES)
def reset_conversation(
    org_id: str,
    workspace_id: str,
    project_id: str,
    agent_id: str,
    session_id: str,
    ctx: RequestContext = Depends(authenticate),
    app: Application = Depends(get_application),
) -> SessionResponse:
    view = app.commands.dispatch(
        ResetConversation(ctx.actor(), org_id, workspace_id, project_id, session_id)
    )
    return SessionResponse.from_view(view)


@router.post("/{session_id}/close", response_model=SessionResponse, responses=ERROR_RESPONSES)
def close_conversation(
    org_id: str,
    workspace_id: str,
    project_id: str,
    agent_id: str,
    session_id: str,
    ctx: RequestContext = Depends(authenticate),
    app: Application = Depends(get_application),
) -> SessionResponse:
    view = app.commands.dispatch(
        CloseConversation(ctx.actor(), org_id, workspace_id, project_id, session_id)
    )
    return SessionResponse.from_view(view)
