"""The Developer Platform application (M12.1–M12.2, ADR-0055 DP3).

    uvicorn "api.console.app:build_console_app" --factory --host 127.0.0.1 --port 8000

## Why this wraps the M11 WebRTC application instead of being a new one

`build_webrtc_app` already owns everything a session needs: the lifespan, the runtime controller,
the rendezvous registry, `/health`, and the developer API (`/v1/agents`, `/v1/sessions`,
`/v1/webrtc/*`). A second application would need a second copy of that composition — and a
deployment running both could end up with two controllers or two registries. So the console app
**is** the WebRTC app, plus these additions:

1. the console routers under `/v1/console/*` (meta, identity, organizations, agents,
   playground);
2. the console error handler, scoped to `ConsoleError` so M11's `{"detail": …}` bodies are
   untouched;
3. a startup step, run *after* the M11 lifespan has initialised the database, that applies the
   developer-platform migrations (`dp_*`, from `0037`) and purges dead console sessions;
3a. (M12.5) the Epic 10 durable event log attached to the runtime controller, and — on the real
   profile — audio-driven endpointing (findings F-2 and F-7);
4. optionally, the built console bundle under `/console/`.

Nothing in `platform/api/` that existed at `v4.0.0` is modified (`E10-7`, DL-0226).
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from sqlmodel import Session as DBSession

from ..config import get_settings as get_api_settings
from ..db import engine as db_engine
from ..webrtc.app import build_webrtc_app, resolve_ice_policy
from ..webrtc.config import WebRTCSettings
from .agents.service import AgentOptions
from .bundle import mount_console_bundle
from .config import ConsoleSettings
from .errors import register_console_error_handler
from .identity.service import purge_sessions
from .identity.throttle import LoginThrottle
from .persistence.migrations import upgrade_all
from .routers import agents, auth, conversations, meta, organizations, playground
from .session_defaults import AudioEndpointingController


def _declared_agent_languages(webrtc_settings: WebRTCSettings) -> tuple[str, ...]:
    """The agent languages the running speech recognizer declares (finding F-4).

    Asked of the composition root, the one layer allowed to know which provider profile is
    selected (AR-14) — the same choice `api.webrtc.app.build_controller` makes: the real profile
    when WebRTC is enabled, the default (local) profile otherwise. Deferred import, as there."""
    from bootstrap import REAL_PROVIDER_PROFILE, STT_LOCAL, declared_stt_languages

    stt = REAL_PROVIDER_PROFILE["stt"] if webrtc_settings.webrtc_enabled else STT_LOCAL
    return declared_stt_languages(stt)


def _event_spine(database_url: str):
    """The Epic 10 attachments for the console's runtime (M12.5, finding F-2; ADR-0050 SW2).

    `SqlDurableEventLog` — Epic 10's pure append-only log, whose own contract is *"a caller that
    wants history without delivery state should not be forced to acquire an outbox"*. That is this
    caller: Conversations read history, and nothing in this deployment delivers an outbox, so the
    outbox-writing `SqlDurableEventSpine` would only accumulate undeliverable rows. `metric.turn`
    is produced by the existing `TurnLatencyTap` (the sole producer, AR-2). No metric buffer is
    attached: nothing pulls one here, and it is not durable anyway (SW11).

    Deferred imports, as everywhere this layer composes another plane (AR-14)."""
    from bootstrap import build_observability_plane
    from events.persistence import EventDatabase, SqlDurableEventLog

    database = EventDatabase(database_url)
    plane = build_observability_plane(durable=SqlDurableEventLog(database), telemetry=True)
    return database, plane


def apply_session_defaults(controller, webrtc_settings: WebRTCSettings, settings: ConsoleSettings):
    """Audio-driven endpointing for real-voice sessions (finding F-7), unless switched off.

    Only the real profile registers the voice-activity provider audio endpointing needs; applying
    it to the loopback profile would make validation refuse every session (AR-25)."""
    if webrtc_settings.webrtc_enabled and settings.audio_endpointing:
        return AudioEndpointingController(controller)
    return controller


def build_console_app(
    settings: ConsoleSettings | None = None,
    *,
    webrtc_settings: WebRTCSettings | None = None,
) -> FastAPI:
    """Assemble the Developer Platform application. Misconfiguration fails here, at startup."""
    settings = settings or ConsoleSettings.from_env()
    settings.validate()
    webrtc_settings = webrtc_settings or WebRTCSettings.from_env()

    event_database, plane = _event_spine(get_api_settings().database_url)

    # Validates the WebRTC settings and the ICE policy itself; an invalid runtime configuration
    # is refused before any console surface exists.
    app = build_webrtc_app(webrtc_settings, observability=plane)

    runtime_lifespan = app.router.lifespan_context

    @asynccontextmanager
    async def lifespan(inner_app: FastAPI) -> AsyncIterator[None]:
        # The durable log's tables exist before the first session can publish into them.
        event_database.create_all()
        # The M11 lifespan runs next and unchanged: it creates the M11 tables and the runtime
        # controller. The console's own schema and housekeeping follow it.
        async with runtime_lifespan(inner_app):
            upgrade_all(db_engine)
            with DBSession(db_engine) as db:
                purge_sessions(db)
                db.commit()
            inner_app.state.session_controller = apply_session_defaults(
                inner_app.state.session_controller, webrtc_settings, settings
            )
            yield
        event_database.close()

    app.router.lifespan_context = lifespan

    app.state.console_settings = settings
    app.state.console_app_env = webrtc_settings.app_env
    app.state.console_login_throttle = LoginThrottle(
        settings.login_max_failures, settings.login_failure_window_minutes * 60.0
    )
    app.state.console_runtime = meta.RuntimeDescription(
        transport=webrtc_settings.transport,
        webrtc_signalling=webrtc_settings.signalling_enabled,
        ice_policy=resolve_ice_policy(webrtc_settings).describe(),
    )
    app.state.console_bundle = settings.dist_dir is not None
    app.state.console_agent_options = AgentOptions(
        languages=_declared_agent_languages(webrtc_settings)
    )

    register_console_error_handler(app)
    app.include_router(meta.router)
    app.include_router(auth.router)
    app.include_router(organizations.router)
    app.include_router(agents.router)
    app.include_router(playground.router)
    app.include_router(conversations.router)
    app.include_router(meta.fallback_router)  # last among console routers

    if settings.dist_dir is not None:
        mount_console_bundle(app, settings.dist_dir)

    return app


__all__ = ["build_console_app"]
