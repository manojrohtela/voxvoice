"""Provider execution routes (M8.14) — additive, thin.

Real (provider-backed) execution over a playground conversation: execute a turn, **stream** it,
read the durable messages, and inspect provider diagnostics. Prompt/variable/tool-plan
inspection continues to reuse the existing runtime/playground endpoints (unchanged). Every
controller is transport-only: it maps the request to an Application command/query and serializes
the result — no business logic, no repository, no transaction, no permission evaluation.
"""

from __future__ import annotations

import json

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse

from api.application import (
    Application,
    ExecuteTurn,
    GetConversationMessages,
    GetProviderDiagnostics,
)

from ..auth import authenticate
from ..container import get_application
from ..context import RequestContext
from ..schemas import (
    ERROR_RESPONSES,
    ExecuteTurnBody,
    ExecutionResultResponse,
    MessageResponse,
)

router = APIRouter(
    prefix=(
        "/v1/organizations/{org_id}/workspaces/{workspace_id}/projects/{project_id}"
        "/agents/{agent_id}/playground/conversations/{session_id}"
    ),
    tags=["execution"],
)


@router.post("/execute", response_model=ExecutionResultResponse, responses=ERROR_RESPONSES)
def execute_turn(
    org_id: str,
    workspace_id: str,
    project_id: str,
    agent_id: str,
    session_id: str,
    body: ExecuteTurnBody,
    ctx: RequestContext = Depends(authenticate),
    app: Application = Depends(get_application),
) -> ExecutionResultResponse:
    """Execute one turn through the **real provider** engine (published version only)."""
    view = app.commands.dispatch(
        ExecuteTurn(
            actor_user_id=ctx.actor(), org_id=org_id, workspace_id=workspace_id,
            project_id=project_id, session_id=session_id, user_input=body.user_input,
            stream=False,
        )
    )
    return ExecutionResultResponse.from_view(view)


@router.post("/stream", responses=ERROR_RESPONSES)
def stream_turn(
    org_id: str,
    workspace_id: str,
    project_id: str,
    agent_id: str,
    session_id: str,
    body: ExecuteTurnBody,
    ctx: RequestContext = Depends(authenticate),
    app: Application = Depends(get_application),
) -> StreamingResponse:
    """Stream one turn's provider events as NDJSON (token / tool / voice / done)."""
    view = app.commands.dispatch(
        ExecuteTurn(
            actor_user_id=ctx.actor(), org_id=org_id, workspace_id=workspace_id,
            project_id=project_id, session_id=session_id, user_input=body.user_input,
            stream=True,
        )
    )

    def _events():
        for event in view.stream:
            yield json.dumps(event) + "\n"

    return StreamingResponse(_events(), media_type="application/x-ndjson")


@router.get("/messages", response_model=list[MessageResponse], responses=ERROR_RESPONSES)
def list_messages(
    org_id: str,
    workspace_id: str,
    project_id: str,
    agent_id: str,
    session_id: str,
    _ctx: RequestContext = Depends(authenticate),
    app: Application = Depends(get_application),
) -> list[MessageResponse]:
    """The durable conversation message store (memory adapter)."""
    results = app.queries.dispatch(GetConversationMessages(org_id, session_id))
    return [MessageResponse.from_dict(d) for d in results]


@router.get("/provider", responses=ERROR_RESPONSES)
def provider_diagnostics(
    org_id: str,
    workspace_id: str,
    project_id: str,
    agent_id: str,
    session_id: str,
    _ctx: RequestContext = Depends(authenticate),
    app: Application = Depends(get_application),
) -> dict:
    """Provider diagnostics (selected LLM/voice provider + circuit-breaker health)."""
    return app.queries.dispatch(GetProviderDiagnostics(org_id, session_id))
