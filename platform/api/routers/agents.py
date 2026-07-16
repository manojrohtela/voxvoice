"""Agents CRUD (SPEC §4). Every query is scoped to the caller's Org; an agent
belonging to another org is indistinguishable from a missing one (404)."""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, status
from sqlmodel import Session as DBSession
from sqlmodel import select

from ..db import get_session
from ..models import Agent, Org
from ..schemas import AgentCreate, AgentRead, AgentUpdate
from ..security import get_org

router = APIRouter(prefix="/v1/agents", tags=["agents"])


def _get_owned_agent(agent_id: str, org: Org, db: DBSession) -> Agent:
    agent = db.get(Agent, agent_id)
    if agent is None or agent.org_id != org.id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Agent not found")
    return agent


@router.post("", response_model=AgentRead, status_code=status.HTTP_201_CREATED)
def create_agent(
    body: AgentCreate,
    org: Org = Depends(get_org),
    db: DBSession = Depends(get_session),
) -> AgentRead:
    data = body.model_dump()
    meta = data.pop("metadata")
    agent = Agent(org_id=org.id, meta=meta, **data)
    db.add(agent)
    db.commit()
    db.refresh(agent)
    return AgentRead.from_model(agent)


@router.get("", response_model=list[AgentRead])
def list_agents(
    org: Org = Depends(get_org),
    db: DBSession = Depends(get_session),
) -> list[AgentRead]:
    agents = db.exec(select(Agent).where(Agent.org_id == org.id)).all()
    return [AgentRead.from_model(a) for a in agents]


@router.get("/{agent_id}", response_model=AgentRead)
def get_agent(
    agent_id: str,
    org: Org = Depends(get_org),
    db: DBSession = Depends(get_session),
) -> AgentRead:
    return AgentRead.from_model(_get_owned_agent(agent_id, org, db))


@router.patch("/{agent_id}", response_model=AgentRead)
def update_agent(
    agent_id: str,
    body: AgentUpdate,
    org: Org = Depends(get_org),
    db: DBSession = Depends(get_session),
) -> AgentRead:
    agent = _get_owned_agent(agent_id, org, db)
    updates = body.model_dump(exclude_unset=True)
    if "metadata" in updates:
        agent.meta = updates.pop("metadata")
    for field, value in updates.items():
        setattr(agent, field, value)
    agent.updated_at = datetime.now(UTC)
    db.add(agent)
    db.commit()
    db.refresh(agent)
    return AgentRead.from_model(agent)


@router.delete("/{agent_id}")
def delete_agent(
    agent_id: str,
    org: Org = Depends(get_org),
    db: DBSession = Depends(get_session),
) -> dict[str, str]:
    agent = _get_owned_agent(agent_id, org, db)
    db.delete(agent)
    db.commit()
    return {"deleted": agent_id}
