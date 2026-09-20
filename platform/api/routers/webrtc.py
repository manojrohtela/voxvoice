"""WebRTC signalling endpoints — the channel an external peer uses to join a session (M11.16.4).

Four operations, and deliberately no fifth:

| | | |
|---|---|---|
| `POST /v1/webrtc/rendezvous` | org API key | open a slot; returns a single-purpose token |
| `GET  /v1/webrtc/rendezvous/{id}/offer` | signalling token | bounded long-poll for the offer |
| `POST /v1/webrtc/rendezvous/{id}/answer` | signalling token | deliver the peer's answer |
| `POST /v1/webrtc/sessions` | org API key | start a session on a claimed rendezvous |

`POST /v1/webrtc/sessions` **is** the existing session endpoint: it calls
`api.routers.sessions.create_session` directly, with the rendezvous claim carried alongside in a
context variable (see `api.webrtc.binding`). Agent lookup, org scoping, persistence, the 502
mapping and the `session.started` webhook are the ones that already exist — not a second copy.
A separate path exists only because `E10-7` freezes `api/routers/sessions.py`, and an additive
module is what that freeze permits (ADR-0043 CD7).

There is no room, no roster, no renegotiation and no ICE-candidate endpoint: aiortc emits a
**complete** local description (non-trickle) during `setLocalDescription`, so the candidates ride
the SDP and a second channel would carry nothing (§6/§17).

The router is only mounted when signalling is configured, so a deployment that has not asked for
WebRTC does not expose an unauthenticated-looking surface it never intended (§10/§12).
"""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends, Header, HTTPException, Request, Response, status
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field
from sqlmodel import Session as DBSession

from ..db import get_session
from ..models import Org
from ..routers.sessions import create_session, get_controller
from ..schemas import SessionCreate, SessionCreateResponse
from ..security import get_org
from ..webrtc.binding import PENDING_RENDEZVOUS
from ..webrtc.rendezvous import RendezvousError, RendezvousRegistry

router = APIRouter(prefix="/v1/webrtc", tags=["webrtc"])

#: The header the peer presents. Deliberately **not** `x-api-key`: the browser must never hold
#: the org key, and a header of its own makes that impossible to confuse (§9).
SIGNALLING_TOKEN_HEADER = "x-signalling-token"


class RendezvousResponse(BaseModel):
    rendezvous_id: str
    signalling_token: str
    expires_in_s: float


class OfferResponse(BaseModel):
    sdp: str


class WebRTCSessionCreate(BaseModel):
    """`SessionCreate` plus the rendezvous the caller reserved. Same fields, same meanings."""

    agent_id: str
    rendezvous_id: str
    channel: str = "web"
    metadata: dict[str, object] = Field(default_factory=dict)


class AnswerRequest(BaseModel):
    # Bounded at the schema edge as well as in the registry, so an oversized body is refused
    # before it is parsed into a model that would hold it.
    sdp: str = Field(max_length=256 * 1024)


def get_registry(request: Request) -> RendezvousRegistry:
    """The signalling registry injected at the composition root, or 503 if WebRTC is not wired."""
    registry = getattr(request.app.state, "webrtc_rendezvous", None)
    if registry is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="WebRTC signalling is not configured",
        )
    return registry


def _fail(exc: RendezvousError) -> HTTPException:
    return HTTPException(status_code=exc.status_code, detail=exc.detail)


@router.post(
    "/rendezvous", response_model=RendezvousResponse, status_code=status.HTTP_201_CREATED
)
def reserve_rendezvous(
    org: Org = Depends(get_org),
    registry: RendezvousRegistry = Depends(get_registry),
) -> RendezvousResponse:
    """Open a signalling slot for this org and mint its single-purpose token.

    The token is the only credential the peer ever receives. It can exchange one SDP pair for one
    session and do nothing else — it cannot start a session, read a transcript or reach a
    provider."""
    try:
        rendezvous_id, token, ttl = registry.reserve(org.id)
    except RendezvousError as exc:
        raise _fail(exc) from exc
    return RendezvousResponse(
        rendezvous_id=rendezvous_id, signalling_token=token, expires_in_s=ttl
    )


@router.get("/rendezvous/{rendezvous_id}/offer", response_model=None)
async def get_offer(
    rendezvous_id: str,
    token: str | None = Header(default=None, alias=SIGNALLING_TOKEN_HEADER),
    registry: RendezvousRegistry = Depends(get_registry),
) -> OfferResponse | Response:
    """Bounded long-poll for the runtime's offer.

    `204` means *not yet* — the session start that produces the offer is still in flight, and the
    peer should ask again. It is not an error, and it is not a spin: each call waits on the
    rendezvous itself and returns on a predictable bound (§29)."""
    try:
        sdp = await registry.await_offer(rendezvous_id, token)
    except RendezvousError as exc:
        raise _fail(exc) from exc
    if sdp is None:
        return Response(status_code=status.HTTP_204_NO_CONTENT)
    return OfferResponse(sdp=sdp)


@router.post("/rendezvous/{rendezvous_id}/answer", status_code=status.HTTP_204_NO_CONTENT)
def post_answer(
    rendezvous_id: str,
    body: AnswerRequest,
    token: str | None = Header(default=None, alias=SIGNALLING_TOKEN_HEADER),
    registry: RendezvousRegistry = Depends(get_registry),
) -> Response:
    """Deliver the peer's answer. Accepted once, and only while an offer is awaiting one."""
    try:
        registry.submit_answer(rendezvous_id, token, body.sdp)
    except RendezvousError as exc:
        raise _fail(exc) from exc
    return Response(status_code=status.HTTP_204_NO_CONTENT)


#: The minimal browser client, served from the API so it runs on `http://localhost`, which is a
#: secure context and therefore allowed to request a microphone. A `file://` page is not, which
#: is the whole reason this route exists rather than "open the HTML file".
_CLIENT_PAGE = Path(__file__).resolve().parent.parent / "webrtc" / "client.html"


@router.get("/client", response_class=HTMLResponse, include_in_schema=False)
def client_page(
    registry: RendezvousRegistry = Depends(get_registry),  # noqa: ARG001 — gates on configuration
) -> HTMLResponse:
    """The reference browser client. Static, credential-free, and same-origin by construction.

    It holds no provider credential and never can: the only secrets it ever sees are the org API
    key its operator types in and the single-purpose signalling token the API mints for it (§9)."""
    return HTMLResponse(_CLIENT_PAGE.read_text(encoding="utf-8"))


@router.post(
    "/sessions", response_model=SessionCreateResponse, status_code=status.HTTP_201_CREATED
)
async def create_webrtc_session(
    body: WebRTCSessionCreate,
    request: Request,
    org: Org = Depends(get_org),
    db: DBSession = Depends(get_session),
    registry: RendezvousRegistry = Depends(get_registry),
) -> SessionCreateResponse:
    """Start a session whose media rides a rendezvous the caller already reserved.

    **This call blocks until the peer connection is usable**, because that is what
    `MediaEndpoint.connect()` returning means (AR-6). The connection cannot become usable until
    the caller has answered the offer this very call produces, so the caller issues its offer poll
    and its answer **concurrently with** this request. That overlap is forced by the contract, not
    chosen for convenience."""
    try:
        registry.check_bindable(body.rendezvous_id, org.id)
    except RendezvousError as exc:
        raise _fail(exc) from exc
    token = PENDING_RENDEZVOUS.set((body.rendezvous_id, org.id))
    try:
        return await create_session(
            body=SessionCreate(
                agent_id=body.agent_id, channel=body.channel, metadata=dict(body.metadata)
            ),
            org=org,
            db=db,
            controller=get_controller(request),
        )
    finally:
        PENDING_RENDEZVOUS.reset(token)
