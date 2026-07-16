"""Engine -> platform event ingestion.

Dograh POSTs transcript/call events here. This endpoint is NOT org-scoped (it
is called by the engine, not a consumer); it is protected by a shared secret in
the `x-engine-secret` header (ENGINE_CALLBACK_SECRET). It hands each item to the
active adapter, which dispatches to the registered persist+webhook handler.

TODO(dograh): align the request body with Dograh's real event payload schema.
"""

from __future__ import annotations

from fastapi import APIRouter, Header, HTTPException, status

from adapters import get_adapter

from ..config import get_settings
from ..schemas import EngineTranscriptEvent

router = APIRouter(prefix="/v1/engine", tags=["engine"])


@router.post("/dograh/events", status_code=status.HTTP_202_ACCEPTED)
async def dograh_events(
    event: EngineTranscriptEvent,
    x_engine_secret: str | None = Header(default=None, alias="x-engine-secret"),
) -> dict[str, str]:
    settings = get_settings()
    if x_engine_secret != settings.engine_callback_secret:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid engine secret"
        )

    await get_adapter().dispatch_transcript(
        event.engine_ref, event.role, event.text, event.ts
    )
    return {"status": "accepted"}
