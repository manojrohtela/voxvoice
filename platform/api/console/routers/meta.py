"""`GET /v1/console/meta` — what this process is and what the console can do now (M12.1).

Unauthenticated and secret-free. The runtime section repeats only what `/health` already
publishes (transport, signalling, ICE description); it never names a provider, a voice id or a
credential (security model S1/S2, `E11-3`).

`capabilities` lists **implemented** capabilities only. The console renders an absent capability
as "not yet available" and never fakes it; each Epic 12 milestone appends what it delivers.
"""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel
from sqlmodel import Session as DBSession

from ...db import get_session
from ..config import ConsoleSettings
from ..errors import NotFound
from ..identity.deps import console_settings
from .auth import signup_available

#: The console API revision. The console compares it with the one it was built for.
CONSOLE_API_REVISION = "m12.2"

#: Capabilities the console API implements at this revision.
CAPABILITIES: tuple[str, ...] = ("runtime.status", "identity")

router = APIRouter(prefix="/v1/console", tags=["console"])


class RuntimeDescription(BaseModel):
    transport: str
    webrtc_signalling: bool
    ice_policy: str


class SignupDescription(BaseModel):
    #: `first_run` | `open` | `closed` (the configured policy)
    mode: str
    #: whether a sign-up would be accepted right now
    available: bool


class ConsoleMeta(BaseModel):
    api_version: Literal["v1"] = "v1"
    console_api_revision: str
    runtime: RuntimeDescription
    capabilities: list[str]
    console_bundle: bool
    signup: SignupDescription


@router.get("/meta", response_model=ConsoleMeta)
def get_meta(
    request: Request,
    db: DBSession = Depends(get_session),
    settings: ConsoleSettings = Depends(console_settings),
) -> ConsoleMeta:
    state = request.app.state
    return ConsoleMeta(
        console_api_revision=CONSOLE_API_REVISION,
        runtime=state.console_runtime,
        capabilities=list(CAPABILITIES),
        console_bundle=state.console_bundle,
        signup=SignupDescription(
            mode=settings.signup, available=signup_available(db, settings)
        ),
    )


#: Registered after every other console router, so an unknown `/v1/console/*` path answers in
#: the console's own error envelope rather than FastAPI's `{"detail": …}`.
fallback_router = APIRouter(prefix="/v1/console", include_in_schema=False)


@fallback_router.api_route(
    "/{path:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE"]
)
def unknown_console_route(path: str) -> None:  # noqa: ARG001 — the path is only matched
    raise NotFound("No such console endpoint")


__all__ = [
    "CAPABILITIES",
    "CONSOLE_API_REVISION",
    "ConsoleMeta",
    "RuntimeDescription",
    "SignupDescription",
    "fallback_router",
    "router",
]
