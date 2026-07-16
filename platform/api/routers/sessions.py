"""Sessions (SPEC §4). Starting a session loads the (org-scoped) agent, asks
the engine adapter to create the call/room, persists the Session, and returns
the engine connection details. Org-scoping applies to every read."""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, status
from sqlmodel import Session as DBSession
from sqlmodel import select

from adapters import EngineError, get_adapter
from webhooks import emit_event

from ..db import get_session
from ..ingest import maybe_start_transcript_poller
from ..models import Agent, Org, Session, Transcript
from ..schemas import (
    PhoneConn,
    SessionCreate,
    SessionCreateResponse,
    SessionRead,
    TranscriptItem,
    WebConn,
)
from ..security import get_org

router = APIRouter(prefix="/v1/sessions", tags=["sessions"])


def _get_owned_session(session_id: str, org: Org, db: DBSession) -> Session:
    session = db.get(Session, session_id)
    if session is None or session.org_id != org.id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Session not found"
        )
    return session


@router.post("", response_model=SessionCreateResponse, status_code=status.HTTP_201_CREATED)
async def create_session(
    body: SessionCreate,
    org: Org = Depends(get_org),
    db: DBSession = Depends(get_session),
) -> SessionCreateResponse:
    agent = db.get(Agent, body.agent_id)
    if agent is None or agent.org_id != org.id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Agent not found")

    adapter = get_adapter()
    session = Session(
        org_id=org.id,
        agent_id=agent.id,
        engine=adapter.name,
        channel=body.channel,
        status="created",
        meta=body.metadata,
    )

    try:
        engine_session = await adapter.start_session(agent, session)
    except EngineError as exc:
        # Persist the failure for observability; never leak the raw engine error.
        session.status = "failed"
        db.add(session)
        db.commit()
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)
        ) from exc

    session.engine_ref = engine_session.engine_ref
    session.status = "active"
    session.started_at = datetime.now(UTC)
    db.add(session)
    # The adapter may have lazily created the engine workflow and stamped its id
    # onto the agent; persist that so we reuse the same workflow next time.
    db.add(agent)
    db.commit()
    db.refresh(session)

    await emit_event(org, "session.started", SessionRead.from_model(session).model_dump())

    # Dograh has no transcript push, so drain the run report in the background.
    maybe_start_transcript_poller(adapter, session.engine_ref)

    resp = SessionCreateResponse(
        session_id=session.id, engine=session.engine, channel=session.channel
    )
    if engine_session.web is not None:
        resp.web = WebConn(**engine_session.web.__dict__)
    if engine_session.phone is not None:
        resp.phone = PhoneConn(**engine_session.phone.__dict__)
    return resp


@router.post("/{session_id}/end")
async def end_session(
    session_id: str,
    org: Org = Depends(get_org),
    db: DBSession = Depends(get_session),
) -> dict[str, str]:
    session = _get_owned_session(session_id, org, db)
    if session.status != "ended":
        try:
            await get_adapter().end_session(session)
        except EngineError as exc:
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)
            ) from exc
        session.status = "ended"
        session.ended_at = datetime.now(UTC)
        db.add(session)
        db.commit()
        await emit_event(
            org, "session.ended", {"session_id": session.id, "status": "ended"}
        )
    return {"session_id": session.id, "status": "ended"}


@router.get("/{session_id}", response_model=SessionRead)
def get_session_detail(
    session_id: str,
    org: Org = Depends(get_org),
    db: DBSession = Depends(get_session),
) -> SessionRead:
    return SessionRead.from_model(_get_owned_session(session_id, org, db))


@router.get("/{session_id}/transcript", response_model=list[TranscriptItem])
def get_transcript(
    session_id: str,
    org: Org = Depends(get_org),
    db: DBSession = Depends(get_session),
) -> list[TranscriptItem]:
    session = _get_owned_session(session_id, org, db)
    rows = db.exec(
        select(Transcript).where(Transcript.session_id == session.id).order_by(Transcript.ts)
    ).all()
    return [
        TranscriptItem(session_id=session.id, role=r.role, text=r.text, ts=r.ts)
        for r in rows
    ]
