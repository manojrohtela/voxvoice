"""The WebRTC deployment's application (M11.16.4).

## Why this is a separate app rather than a flag on the existing one

`E10-7` freezes every pre-existing `platform/api/` file (ADR-0043 CD7), and `api/main.py` is one
of them. That constraint turned out to produce the better design: the default application is
untouched **by construction** rather than by care, so no deployment can acquire a signalling
surface, a CORS policy or a real transport because of a setting it did not know about.

Both applications mount the **same** `agents` and `sessions` routers, unmodified. The difference
between them is exactly three things: the signalling router, the CORS policy, and which controller
is injected.

## Run it

```
RUNTIME_TRANSPORT=webrtc VOXVOICE_TTS_VOICE_ID=… \\
  uvicorn "api.webrtc.app:build_webrtc_app" --factory --host 0.0.0.0 --port 8000
```

Selecting `webrtc` without the credentials or the voice id is a **startup error**, never a quiet
downgrade to the loopback transport.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Any

from fastapi import FastAPI
from sqlalchemy import text
from sqlmodel import Session as DBSession

from ..db import engine as db_engine
from ..db import init_db
from ..models import Session, Transcript
from ..routers import agents, sessions, webrtc
from .binding import RendezvousBindingController
from .call_slot import CallSlot, capacity_from_env
from .config import WebRTCConfigError, WebRTCSettings
from .process_lock import (
    acquire_single_process_lock,
    lock_path_from_env,
    release_single_process_lock,
)
from .rendezvous import RendezvousRegistry
from .single_call import SingleCallController


def _persist_transcript(session_id: str, role: str, text_value: str, is_final: bool) -> None:
    """The Control-Plane transcript sink: FINAL items only.

    `ts` is **seconds since the session started** — what the `Transcript.ts` column has always
    documented ("seconds offset within the call"). Until M12.5 this sink wrote `0.0` for every
    item, so a transcript had no recoverable order (finding F-3): ids are random and SQL gives no
    insertion order. The default app (`api/main.py`) is frozen and still writes `0.0`; its rows are
    reported as unordered rather than guessed at."""
    if not is_final:
        return
    with DBSession(db_engine) as db:
        session = db.get(Session, session_id)
        offset = 0.0
        if session is not None and session.started_at is not None:
            started = session.started_at
            if started.tzinfo is None:  # SQLite returns naive values for the stored UTC instant
                started = started.replace(tzinfo=UTC)
            offset = max(0.0, (datetime.now(UTC) - started).total_seconds())
        db.add(Transcript(session_id=session_id, role=role, text=text_value, ts=offset))
        db.commit()


def build_controller(
    settings: WebRTCSettings,
    registry: RendezvousRegistry,
    ice_policy: Any = None,
    observability: Any = None,
    call_slot: CallSlot | None = None,
) -> Any:
    """Assemble the runtime controller the configured transport calls for.

    Deferred import so `api` never statically depends on `runtime` (AR-14); `bootstrap` is the one
    sanctioned wiring point, exactly as in `api/main.py`."""
    from bootstrap import build_session_controller

    # `observability` (M12.5) is the Epic 10 plane a composition root may attach. It is passed
    # only when given, so `None` leaves each builder's own default (`UNWIRED`) — the pre-M12.5
    # composition exactly — and `api` names no `runtime` type (AR-14).
    wired = {} if observability is None else {"observability": observability}

    if not settings.webrtc_enabled:
        inner = build_session_controller(transcript_sink=_persist_transcript, **wired)
    else:
        # The real stack. The signalling channel is this app's own registry; the credentials come
        # from the ADR-0049 provider and nowhere else, and a missing one is a startup failure
        # rather than a quiet fall back to a local provider (CR3/CR4).
        from bootstrap import build_real_session_controller, build_webrtc_media_endpoint
        from providers.transport.ice import NO_ICE_SERVERS

        from ..adapters.secrets import EnvCredentialProvider

        if ice_policy is None:
            ice_policy = NO_ICE_SERVERS

        assert settings.voice_id  # guaranteed by WebRTCSettings.validate
        inner = build_real_session_controller(
            credentials=EnvCredentialProvider(),
            media_endpoint=build_webrtc_media_endpoint(registry, ice_policy=ice_policy),
            tts_voice=settings.voice_id,
            transcript_sink=_persist_transcript,
            **wired,
        )
    controller = RendezvousBindingController(inner, registry)
    if call_slot is not None and not call_slot.unlimited:
        # Wrapped **only** when a cap was configured, so an unconfigured deployment's controller
        # chain is the one it had before M13.1 — not a chain with a disabled layer in it.
        controller = SingleCallController(controller, call_slot)
    return controller


def resolve_ice_policy(settings: WebRTCSettings) -> Any:
    """Translate the deployment's stated STUN URLs into an `IcePolicy`, validating them.

    Done **here**, at the composition root, because this is the layer allowed to know the provider
    plane exists (AR-14) — and because §4 of M11.16.6 requires the ICE policy to be stated where
    the deployment is assembled rather than inherited from a library three layers down (R-71)."""
    from providers.transport.ice import IcePolicy, IcePolicyError

    try:
        return IcePolicy.from_urls(settings.ice_stun_urls)
    except IcePolicyError as exc:
        raise WebRTCConfigError(f"VOXVOICE_ICE_STUN_URLS: {exc}") from exc


def build_webrtc_app(
    settings: WebRTCSettings | None = None, *, observability: Any = None
) -> FastAPI:
    """Assemble the WebRTC-capable Control Plane application.

    `observability` (M12.5) is passed to the runtime controller unchanged; omitted, the app is
    composed exactly as before."""
    settings = settings or WebRTCSettings.from_env()
    settings.validate()
    ice_policy = resolve_ice_policy(settings)  # refused at startup, never at call setup
    # Read here so a malformed cap fails startup alongside every other configuration refusal,
    # rather than at the first call (M13.1). `None` is unlimited: the pre-M13.1 behaviour.
    capacity = capacity_from_env()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        init_db()
        registry = RendezvousRegistry()
        app.state.webrtc_rendezvous = registry if settings.signalling_enabled else None
        # The probe resolves `session_controller` **at call time**, not now: a composition root
        # above this one may wrap the controller after this lifespan has run (the console adds
        # `AudioEndpointingController`), and the slot must ask whoever is outermost then.
        slot = CallSlot(
            capacity,
            probe=lambda session_id: app.state.session_controller.conversation_state(session_id),
        )
        app.state.webrtc_call_slot = slot
        app.state.session_controller = build_controller(
            settings, registry, observability=observability, call_slot=slot
        )
        # Taken **last**, so a startup that fails earlier never held it, and released in a
        # `finally`, so one that fails later does not keep it. The slot above and the rendezvous
        # registry are both per process, which is the precondition this enforces (M13.1).
        lock_path = lock_path_from_env()
        lock_fd = acquire_single_process_lock(lock_path) if lock_path else None
        app.state.single_process_lock_fd = lock_fd
        try:
            yield
        finally:
            if lock_fd is not None:
                release_single_process_lock(lock_fd)
                app.state.single_process_lock_fd = None

    app = FastAPI(title="VoxVoice Platform (WebRTC)", version="0.1.0", lifespan=lifespan)

    if settings.allowed_origins:
        # Added only when origins are configured, so a deployment that never asked for browser
        # access has no CORS middleware at all rather than a permissive one (§10). Credentials are
        # not allowed: the peer authenticates with an explicit header, never a cookie, so the
        # `*`-with-credentials trap cannot arise.
        from fastapi.middleware.cors import CORSMiddleware

        app.add_middleware(
            CORSMiddleware,
            allow_origins=list(settings.allowed_origins),
            allow_credentials=False,
            allow_methods=["GET", "POST", "OPTIONS"],
            allow_headers=["content-type", "x-api-key", webrtc.SIGNALLING_TOKEN_HEADER],
        )

    app.include_router(agents.router)
    app.include_router(sessions.router)
    if settings.signalling_enabled:
        app.include_router(webrtc.router)

    @app.get("/health", tags=["health"])
    def health() -> dict[str, object]:
        db_ok = True
        try:
            with db_engine.connect() as conn:
                conn.execute(text("SELECT 1"))
        except Exception:
            db_ok = False
        return {
            "ok": db_ok,
            "db": db_ok,
            "transport": settings.transport,
            "webrtc_signalling": settings.signalling_enabled,
            "ice_policy": ice_policy.describe(),
        }

    return app


__all__ = ["build_controller", "build_webrtc_app", "resolve_ice_policy"]
