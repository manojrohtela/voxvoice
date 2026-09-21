"""`/v1/console/playground/*` — talk to an agent from the console (M12.4, LLD §5, ADR-0055 DP5).

**There is one voice runtime, and this is not a second one.** Each route resolves the console
identity to an organization and then calls the frozen M11 functions the WebRTC router already
composes:

    rendezvous  → RendezvousRegistry.reserve(org.id)              (as POST /v1/webrtc/rendezvous)
    sessions    → api.routers.webrtc.create_webrtc_session(…)     (→ create_session → runtime)
    end         → api.routers.sessions.end_session(…)

The browser then runs the **existing** offer/answer exchange on `/v1/webrtc/rendezvous/{id}/*`
with the single-purpose signalling token. It never holds an org API key or a provider credential
(security model S1). The organization comes from the server-side session; an agent id in the body
is checked against it, never trusted (S3/S4).
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlmodel import Session as DBSession

from ...db import get_session
from ...models import Org
from ...routers.sessions import end_session, get_controller
from ...routers.webrtc import WebRTCSessionCreate, create_webrtc_session
from ...webrtc.rendezvous import RendezvousError, RendezvousRegistry
from ..agents import service as agents
from ..agents.service import AgentOptions
from ..errors import InvalidRequest, RuntimeUnavailable, from_http_exception
from ..identity.deps import Member, require_console_header, require_role
from .agents import agent_options

router = APIRouter(
    prefix="/v1/console/playground",
    tags=["console"],
    dependencies=[Depends(require_console_header)],
)

#: Recorded on the session row (`session.metadata`) so Conversations can tell a Playground call
#: from an API one, and who placed it.
SOURCE = "console_playground"


def signalling_registry(request: Request) -> RendezvousRegistry:
    registry = getattr(request.app.state, "webrtc_rendezvous", None)
    if registry is None:
        raise RuntimeUnavailable(
            "Voice is not enabled on this server. Start it with RUNTIME_TRANSPORT=webrtc "
            "(see docs/runtime/local-run.md)."
        )
    return registry


class RendezvousView(BaseModel):
    rendezvous_id: str
    signalling_token: str
    expires_in_s: float


class PlaygroundSessionCreate(BaseModel):
    agent_id: str
    rendezvous_id: str


class PlaygroundSessionView(BaseModel):
    session_id: str
    agent_id: str


class PlaygroundSessionEnded(BaseModel):
    session_id: str
    status: str


def _org_row(db: DBSession, member: Member) -> Org:
    org = db.get(Org, member.org.id)
    assert org is not None  # the membership join guarantees it
    return org


@router.post("/rendezvous", response_model=RendezvousView, status_code=201)
def reserve(
    member: Member = Depends(require_role("developer")),
    registry: RendezvousRegistry = Depends(signalling_registry),
) -> RendezvousView:
    """Open a signalling slot for the caller's organization and mint its single-purpose token."""
    try:
        rendezvous_id, token, ttl = registry.reserve(member.org.id)
    except RendezvousError as exc:
        raise from_http_exception(HTTPException(exc.status_code, exc.detail)) from exc
    return RendezvousView(rendezvous_id=rendezvous_id, signalling_token=token, expires_in_s=ttl)


@router.post("/sessions", response_model=PlaygroundSessionView, status_code=201)
async def start(
    body: PlaygroundSessionCreate,
    request: Request,
    member: Member = Depends(require_role("developer")),
    db: DBSession = Depends(get_session),
    registry: RendezvousRegistry = Depends(signalling_registry),
    options: AgentOptions = Depends(agent_options),
) -> PlaygroundSessionView:
    """Start a real session for an agent on a reserved rendezvous.

    Like `POST /v1/webrtc/sessions`, this **blocks until the peer connection is usable**, so the
    browser answers the offer concurrently with this request (the M11 contract, unchanged)."""
    agent = agents.get_agent(db, member.org.id, body.agent_id)  # 404: other org or archived
    if agent.language not in options.languages:
        raise InvalidRequest(
            f"{agent.name} uses {agent.language}, which the speech recognizer does not support. "
            "Change its language first."
        )
    try:
        created = await create_webrtc_session(
            body=WebRTCSessionCreate(
                agent_id=agent.id,
                rendezvous_id=body.rendezvous_id,
                channel="web",
                metadata={"source": SOURCE, "started_by": member.user.id},
            ),
            request=request,
            org=_org_row(db, member),
            db=db,
            registry=registry,
        )
    except HTTPException as exc:
        raise from_http_exception(exc) from exc
    return PlaygroundSessionView(session_id=created.session_id, agent_id=agent.id)


@router.post("/sessions/{session_id}/end", response_model=PlaygroundSessionEnded)
async def end(
    session_id: str,
    request: Request,
    member: Member = Depends(require_role("developer")),
    db: DBSession = Depends(get_session),
) -> PlaygroundSessionEnded:
    """End a session of the caller's organization. Idempotent, as the M11 handler is."""
    try:
        ended = await end_session(
            session_id=session_id,
            org=_org_row(db, member),
            db=db,
            controller=get_controller(request),
        )
    except HTTPException as exc:
        raise from_http_exception(exc) from exc
    return PlaygroundSessionEnded(session_id=ended["session_id"], status=ended["status"])
