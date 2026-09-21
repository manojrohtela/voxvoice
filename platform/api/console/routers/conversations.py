"""`/v1/console/conversations` — the organization's call history (M12.5, LLD §6).

Read-only. `viewer` or higher. Another organization's conversation is 404 (S4). What is returned is
a product view: the session's facts, its transcript in spoken order, per-turn latency from
`metric.turn`, and an allow-listed lifecycle timeline — never raw event payloads.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlmodel import Session as DBSession

from ...db import get_session
from ...models import Session
from ..conversations import service
from ..identity.deps import Member, require_role

router = APIRouter(prefix="/v1/console/conversations", tags=["console"])

Status = Literal["created", "active", "ended", "failed"]


class AgentRef(BaseModel):
    id: str
    #: None when the agent row no longer exists (deleted through the developer API).
    name: str | None


class ConversationSummary(BaseModel):
    id: str
    agent: AgentRef
    status: str
    channel: str
    #: `console_playground` or `api`
    source: str
    started_at: datetime | None
    ended_at: datetime | None
    duration_s: float | None
    message_count: int


class ConversationPage(BaseModel):
    data: list[ConversationSummary]
    next_cursor: str | None


class TranscriptItemView(BaseModel):
    role: str
    text: str
    offset_s: float


class TurnView(BaseModel):
    turn: int | None
    status: str | None
    voice_to_voice_ms: int | None
    stages_ms: dict[str, int]
    user_speech_ms: int | None
    unmeasured: list[str]


class TimelineView(BaseModel):
    seq: int
    type: str
    label: str
    turn: int | None
    offset_s: float


class ConversationDetail(ConversationSummary):
    transcript: list[TranscriptItemView]
    #: False for sessions recorded before M12.5 (or by the frozen default app): their transcript
    #: rows carry no timing, so their order cannot be recovered and is not guessed.
    transcript_ordered: bool
    turns: list[TurnView]
    timeline: list[TimelineView]
    #: False when the durable log holds nothing for this session — e.g. a session from before the
    #: log was attached (M12.5). Turns and timeline are then unknown, not empty.
    events_recorded: bool


def _summary(session: Session, agent_name: str | None, message_count: int) -> ConversationSummary:
    return ConversationSummary(
        id=session.id,
        agent=AgentRef(id=session.agent_id, name=agent_name),
        status=session.status,
        channel=session.channel,
        source=service.source_of(session),
        started_at=session.started_at,
        ended_at=session.ended_at,
        duration_s=service.duration_s(session),
        message_count=message_count,
    )


@router.get("", response_model=ConversationPage)
def list_conversations(
    agent_id: str | None = Query(default=None, max_length=64),
    status: Status | None = None,
    cursor: str | None = Query(default=None, max_length=512),
    limit: int = Query(default=25, ge=1, le=service.MAX_PAGE),
    member: Member = Depends(require_role("viewer")),
    db: DBSession = Depends(get_session),
) -> ConversationPage:
    rows, next_cursor = service.list_conversations(
        db, member.org.id, agent_id=agent_id, status=status, cursor=cursor, limit=limit
    )
    return ConversationPage(
        data=[_summary(r.session, r.agent_name, r.message_count) for r in rows],
        next_cursor=next_cursor,
    )


@router.get("/{session_id}", response_model=ConversationDetail)
def get_conversation(
    session_id: str,
    member: Member = Depends(require_role("viewer")),
    db: DBSession = Depends(get_session),
) -> ConversationDetail:
    session, agent_name = service.get_conversation(db, member.org.id, session_id)
    items, ordered = service.transcript(db, session.id)
    event_log = service.events(db, member.org.id, session.id)
    summary = _summary(session, agent_name, len(items))
    return ConversationDetail(
        **summary.model_dump(),
        transcript=[TranscriptItemView(**vars(i)) for i in items],
        transcript_ordered=ordered,
        turns=[TurnView(**vars(t)) for t in service.turns(event_log)],
        timeline=[TimelineView(**vars(t)) for t in service.timeline(event_log)],
        events_recorded=bool(event_log),
    )
