"""`/v1/console/agents` — list, create, read, edit and archive agents (M12.3, LLD §4).

Reads need `viewer`; writes need `developer` (the role matrix). Every query is scoped by the
organization on the server-side session (S3). Another organization's agent, and an archived one,
are 404 (S4). Writes require the console header (CSRF).
"""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, Request, Response
from pydantic import BaseModel
from sqlmodel import Session as DBSession

from ...db import get_session
from ...models import Agent
from ..agents import service
from ..agents.service import AgentOptions
from ..identity.deps import Member, require_console_header, require_role

router = APIRouter(
    prefix="/v1/console/agents", tags=["console"], dependencies=[Depends(require_console_header)]
)


def agent_options(request: Request) -> AgentOptions:
    return request.app.state.console_agent_options


class AgentView(BaseModel):
    id: str
    name: str
    system_prompt: str
    first_message: str | None
    language: str
    #: False when the agent's language is not one the speech recognizer declares — typically an
    #: agent created through the developer API with the `en-IN` default (finding F-4). Such an
    #: agent would drop its call after the greeting.
    language_supported: bool
    llm_model: str | None
    tts_voice: str | None
    created_at: datetime
    updated_at: datetime


class AgentList(BaseModel):
    data: list[AgentView]


class AgentOptionsView(BaseModel):
    languages: list[str]
    default_language: str


class AgentCreate(BaseModel):
    name: str
    system_prompt: str
    first_message: str | None = None
    language: str | None = None
    llm_model: str | None = None
    tts_voice: str | None = None


class AgentUpdate(BaseModel):
    """Partial: only the fields present are changed."""

    name: str | None = None
    system_prompt: str | None = None
    first_message: str | None = None
    language: str | None = None
    llm_model: str | None = None
    tts_voice: str | None = None


def _view(agent: Agent, options: AgentOptions) -> AgentView:
    return AgentView(
        id=agent.id,
        name=agent.name,
        system_prompt=agent.system_prompt,
        first_message=agent.first_message,
        language=agent.language,
        language_supported=agent.language in options.languages,
        llm_model=agent.llm_model,
        tts_voice=agent.tts_voice,
        created_at=agent.created_at,
        updated_at=agent.updated_at,
    )


@router.get("", response_model=AgentList)
def list_agents(
    member: Member = Depends(require_role("viewer")),
    db: DBSession = Depends(get_session),
    options: AgentOptions = Depends(agent_options),
) -> AgentList:
    return AgentList(data=[_view(a, options) for a in service.list_agents(db, member.org.id)])


@router.get("/options", response_model=AgentOptionsView)
def get_options(
    _: Member = Depends(require_role("viewer")),
    options: AgentOptions = Depends(agent_options),
) -> AgentOptionsView:
    return AgentOptionsView(
        languages=list(options.languages), default_language=options.default_language
    )


@router.post("", response_model=AgentView, status_code=201)
def create_agent(
    body: AgentCreate,
    member: Member = Depends(require_role("developer")),
    db: DBSession = Depends(get_session),
    options: AgentOptions = Depends(agent_options),
) -> AgentView:
    fields = body.model_dump(exclude_none=True)
    agent = service.create_agent(
        db, org_id=member.org.id, user_id=member.user.id, fields=fields, options=options
    )
    db.commit()
    db.refresh(agent)
    return _view(agent, options)


@router.get("/{agent_id}", response_model=AgentView)
def get_agent(
    agent_id: str,
    member: Member = Depends(require_role("viewer")),
    db: DBSession = Depends(get_session),
    options: AgentOptions = Depends(agent_options),
) -> AgentView:
    return _view(service.get_agent(db, member.org.id, agent_id), options)


@router.patch("/{agent_id}", response_model=AgentView)
def update_agent(
    agent_id: str,
    body: AgentUpdate,
    member: Member = Depends(require_role("developer")),
    db: DBSession = Depends(get_session),
    options: AgentOptions = Depends(agent_options),
) -> AgentView:
    # `exclude_unset` keeps "not sent" apart from "sent as null" (clear an optional field).
    agent = service.update_agent(
        db,
        org_id=member.org.id,
        agent_id=agent_id,
        fields=body.model_dump(exclude_unset=True),
        options=options,
    )
    db.commit()
    db.refresh(agent)
    return _view(agent, options)


@router.post("/{agent_id}/archive", status_code=204)
def archive_agent(
    agent_id: str,
    member: Member = Depends(require_role("developer")),
    db: DBSession = Depends(get_session),
) -> Response:
    service.archive_agent(db, org_id=member.org.id, agent_id=agent_id, user_id=member.user.id)
    db.commit()
    return Response(status_code=204)
