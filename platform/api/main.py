"""VoxVoice Control Plane API (Constitution §A4.1, ADR-0003).

Consumers talk to this API: auth, configuration (agents), session admission,
control commands, and event delivery. It never touches audio or providers, and
it does not import the Runtime Plane internals — it reaches the runtime only via
a session-control interface and the Event Bus (AR-14).

At M1 the engine-adapter axis, its transcript-ingest/poller, and the Dograh
webclient harness were removed (ADR-0001/0007). Session *start* returns 503 until
the Runtime Plane lands at M2 (Constitution migration M1 — honest gap).
"""

from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI
from sqlalchemy import text

from .config import get_settings
from .db import engine as db_engine
from .db import init_db
from .routers import agents, sessions


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
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
