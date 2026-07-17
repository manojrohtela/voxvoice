"""VoxVoice Control Plane API (Constitution §A4.1, ADR-0003).

Consumers talk to this API: auth, configuration (agents), session admission,
control commands, and event delivery. It never touches audio or providers, and
it does not import the Runtime Plane internals — it reaches the runtime only via
the `core.session_control.SessionController` interface (AR-14), a concrete
instance of which is assembled by the top-level composition root (`bootstrap`)
and injected here at startup.

M2C.2: `POST /v1/sessions` is operational again — it provisions a real Runtime
session through the Runtime Pipeline.
"""

from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI
from sqlalchemy import text
from sqlmodel import Session as DBSession

from .config import get_settings
from .db import engine as db_engine
from .db import init_db
from .models import Transcript
from .routers import agents, sessions


def _persist_transcript(session_id: str, role: str, text_value: str, is_final: bool) -> None:
    """Control-Plane transcript sink handed to the Runtime. Persists FINAL items
    (partials are for live/SDK use, not storage). This is the integration point
    the Event Bus (M4) will subsume."""
    if not is_final:
        return
    with DBSession(db_engine) as db:
        db.add(Transcript(session_id=session_id, role=role, text=text_value, ts=0.0))
        db.commit()


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    # Composition root: assemble the runtime session controller (local provider
    # profile) and inject it. Deferred import so `api` never statically depends
    # on `runtime` (AR-14); `bootstrap` is the one sanctioned wiring point.
    from bootstrap import build_session_controller

    app.state.session_controller = build_session_controller(
        transcript_sink=_persist_transcript
    )
    yield


app = FastAPI(title="VoxVoice Platform", version="0.1.0", lifespan=lifespan)

app.include_router(agents.router)
app.include_router(sessions.router)


@app.get("/health", tags=["health"])
def health() -> dict[str, object]:
    get_settings()
    db_ok = True
    try:
        with db_engine.connect() as conn:
            conn.execute(text("SELECT 1"))
    except Exception:
        db_ok = False
    return {"ok": db_ok, "db": db_ok}
