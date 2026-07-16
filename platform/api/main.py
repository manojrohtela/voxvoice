"""VoxVoice Platform API — Phase 1 (API in front).

Consumers talk only to this API. All engine interaction is behind the adapter
selected by ENGINE; no route here references a concrete engine.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from sqlalchemy import text

from adapters import get_adapter

from .config import get_settings
from .db import engine as db_engine
from .db import init_db
from .ingest import register_transcript_ingest
from .routers import agents, engine_events, sessions


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    # Register the persist+webhook handler with whichever engine is active.
    await register_transcript_ingest(get_adapter())
    yield


app = FastAPI(title="VoxVoice Platform", version="0.1.0", lifespan=lifespan)

app.include_router(agents.router)
app.include_router(sessions.router)
app.include_router(engine_events.router)


_WEBCLIENT = Path(__file__).resolve().parent.parent / "webclient" / "index.html"


@app.get("/webclient", tags=["webclient"], include_in_schema=False)
def webclient() -> FileResponse:
    """Same-origin browser test harness (a consumer): start a session and place
    a real web call through the engine widget. Dev/test convenience only."""
    return FileResponse(_WEBCLIENT)


@app.get("/health", tags=["health"])
def health() -> dict[str, object]:
    settings = get_settings()
    db_ok = True
    try:
        with db_engine.connect() as conn:
            conn.execute(text("SELECT 1"))
    except Exception:
        db_ok = False
    return {"ok": db_ok, "engine": settings.engine, "db": db_ok}
