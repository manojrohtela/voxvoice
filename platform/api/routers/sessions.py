"""Sessions (SPEC §4). Org-scoping applies to every read.

M2C.2: `POST /v1/sessions` is operational again — it persists the Session, then
provisions a real Runtime session via the injected `SessionController` (the
`api` → `runtime` seam, AR-14). The conversation runs in the Runtime Plane over
the transport; the Control Plane never touches audio or providers.
"""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlmodel import Session as DBSession
from sqlmodel import select

from core.session_control import SessionController, StartSessionRequest
from events import emit_event

from ..config import get_settings
from ..db import get_session
from ..models import Agent, Org, Session, Transcript
from ..schemas import (
    SessionCreate,
    SessionCreateResponse,
    SessionRead,
    TranscriptItem,
)
from ..security import get_org

router = APIRouter(prefix="/v1/sessions", tags=["sessions"])


def get_controller(request: Request) -> SessionController | None:
    """The runtime session controller injected at the composition root, or None
    if the runtime is not configured (then session start returns 503)."""
    return getattr(request.app.state, "session_controller", None)


def _get_owned_session(session_id: str, org: Org, db: DBSession) -> Session:
    session = db.get(Session, session_id)
    if session is None or session.org_id != org.id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Session not found"
        )
    return session


def _webhook_target(org: Org) -> tuple[str | None, str | None]:
    settings = get_settings()
    return (
        org.webhook_url or settings.default_webhook_url,
        org.webhook_secret or settings.default_webhook_secret,
    )


@router.post("", response_model=SessionCreateResponse, status_code=status.HTTP_201_CREATED)
async def create_session(
    body: SessionCreate,
    org: Org = Depends(get_org),
    db: DBSession = Depends(get_session),
    controller: SessionController | None = Depends(get_controller),
) -> SessionCreateResponse:
    agent = db.get(Agent, body.agent_id)
    if agent is None or agent.org_id != org.id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Agent not found")
    if controller is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Runtime Plane is not configured",
        )

    session = Session(
        org_id=org.id,
        agent_id=agent.id,
        engine="voxvoice",
        channel=body.channel,
        status="active",
        started_at=datetime.now(UTC),
        meta=body.metadata,
    )
    db.add(session)
    db.commit()
    db.refresh(session)

    request = StartSessionRequest(
        session_id=session.id,
        org_id=org.id,
        agent_id=agent.id,
        channel=body.channel,
        system_prompt=agent.system_prompt,
        first_message=agent.first_message,
        language=agent.language,
        stt_provider=agent.stt_provider,
        tts_provider=agent.tts_provider,
        tts_voice=agent.tts_voice,
        llm_model=agent.llm_model,
        metadata=body.metadata,
    )
    try:
        result = await controller.start(request)
    except Exception as exc:  # noqa: BLE001 — never leak a raw runtime error
        session.status = "failed"
        session.engine_ref = None
        db.add(session)
        db.commit()
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Runtime failed to start the session",
        ) from exc

    session.engine_ref = session.id
    db.add(session)
    db.commit()

    await emit_event(
        url=_webhook_target(org)[0],
        secret=_webhook_target(org)[1],
        org_id=org.id,
        event="session.started",
        data={"session_id": session.id, "channel": body.channel},
    )
    return SessionCreateResponse(
        session_id=session.id, engine=result.engine, channel=result.channel, web=None
    )


@router.post("/{session_id}/end")
async def end_session(
    session_id: str,
    org: Org = Depends(get_org),
    db: DBSession = Depends(get_session),
    controller: SessionController | None = Depends(get_controller),
) -> dict[str, str]:
    session = _get_owned_session(session_id, org, db)
    if session.status != "ended":
        if controller is not None:
            await controller.end(session_id)
        session.status = "ended"
        session.ended_at = datetime.now(UTC)
        db.add(session)
        db.commit()
        url, secret = _webhook_target(org)
        await emit_event(
            url=url,
            secret=secret,
            org_id=org.id,
            event="session.ended",
            data={"session_id": session.id, "status": "ended"},
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
