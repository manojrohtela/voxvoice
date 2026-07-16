"""Sessions (SPEC §4). Org-scoping applies to every read.

At M1 the engine-adapter axis was removed (ADR-0001/0007): there is no engine to
start a call, and the Runtime Plane arrives at M2. Per the Constitution migration
plan, `POST /v1/sessions` therefore returns **503** for now (honest gap) after
validating agent ownership. The read/end endpoints operate on persisted sessions
and remain available. `session.ended` webhooks fan out via the decoupled
`events` module.
"""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, status
from sqlmodel import Session as DBSession
from sqlmodel import select

from events import emit_event

from ..config import get_settings
from ..db import get_session
from ..models import Agent, Org, Session, Transcript
from ..schemas import SessionRead, TranscriptItem
from ..security import get_org

router = APIRouter(prefix="/v1/sessions", tags=["sessions"])


def _get_owned_session(session_id: str, org: Org, db: DBSession) -> Session:
    session = db.get(Session, session_id)
    if session is None or session.org_id != org.id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Session not found"
        )
    return session


def _webhook_target(org: Org) -> tuple[str | None, str | None]:
    """Per-org webhook config, falling back to platform defaults (SPEC §10.4).
    Resolved in the Control Plane so the `events` infra never imports `api`."""
    settings = get_settings()
    return (
        org.webhook_url or settings.default_webhook_url,
        org.webhook_secret or settings.default_webhook_secret,
    )


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_session(
    body: dict,
    org: Org = Depends(get_org),
    db: DBSession = Depends(get_session),
) -> dict:
    # Validate agent ownership first so org-scoping is preserved (cross-org -> 404).
    agent = db.get(Agent, body.get("agent_id"))
    if agent is None or agent.org_id != org.id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Agent not found")

    # No engine exists after the M1 adapter-axis removal; the Runtime Plane that
    # starts live sessions lands at M2 (Constitution migration M1 — honest gap).
    raise HTTPException(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        detail="Session start is unavailable until the Runtime Plane lands (M2).",
    )


@router.post("/{session_id}/end")
async def end_session(
    session_id: str,
    org: Org = Depends(get_org),
    db: DBSession = Depends(get_session),
) -> dict[str, str]:
    session = _get_owned_session(session_id, org, db)
    if session.status != "ended":
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
