"""`GET /v1/console/meta` — what this process is and what the console can do now (M12.1).

Unauthenticated and secret-free. The runtime section repeats only what `/health` already
publishes (transport, signalling, ICE description); it never names a provider, a voice id or a
credential (security model S1/S2, `E11-3`).

`capabilities` lists **implemented** capabilities only. The console renders an absent capability
as "not yet available" and never fakes it; each Epic 12 milestone appends what it delivers.
"""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Request
from pydantic import BaseModel

from ..errors import NotFound

#: The console API revision. The console compares it with the one it was built for.
CONSOLE_API_REVISION = "m12.1"

#: Capabilities the console API implements at this revision.
CAPABILITIES: tuple[str, ...] = ("runtime.status",)

router = APIRouter(prefix="/v1/console", tags=["console"])


class RuntimeDescription(BaseModel):
    transport: str
    webrtc_signalling: bool
    ice_policy: str


class ConsoleMeta(BaseModel):
    api_version: Literal["v1"] = "v1"
    console_api_revision: str
    runtime: RuntimeDescription
    capabilities: list[str]
    console_bundle: bool


@router.get("/meta", response_model=ConsoleMeta)
def get_meta(request: Request) -> ConsoleMeta:
    state = request.app.state
    return ConsoleMeta(
        console_api_revision=CONSOLE_API_REVISION,
        runtime=state.console_runtime,
        capabilities=list(CAPABILITIES),
        console_bundle=state.console_bundle,
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
    "fallback_router",
    "router",
]
