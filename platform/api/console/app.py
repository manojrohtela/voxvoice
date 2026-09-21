"""The Developer Platform application (M12.1, ADR-0055 DP3).

    uvicorn "api.console.app:build_console_app" --factory --host 127.0.0.1 --port 8000

## Why this wraps the M11 WebRTC application instead of being a new one

`build_webrtc_app` already owns everything a session needs: the lifespan, the runtime controller,
the rendezvous registry, `/health`, and the developer API (`/v1/agents`, `/v1/sessions`,
`/v1/webrtc/*`). A second application would need a second copy of that composition — and a
deployment running both could end up with two controllers or two registries. So the console app
**is** the WebRTC app, plus exactly three additions:

1. the console routers under `/v1/console/*`;
2. the console error handler, scoped to `ConsoleError` so M11's `{"detail": …}` bodies are
   untouched;
3. optionally, the built console bundle under `/console/`.

Nothing in `platform/api/` that existed at `v4.0.0` is modified (`E10-7`, DL-0226).
"""

from __future__ import annotations

from fastapi import FastAPI

from ..webrtc.app import build_webrtc_app, resolve_ice_policy
from ..webrtc.config import WebRTCSettings
from .bundle import mount_console_bundle
from .config import ConsoleSettings
from .errors import register_console_error_handler
from .routers import meta


def build_console_app(
    settings: ConsoleSettings | None = None,
    *,
    webrtc_settings: WebRTCSettings | None = None,
) -> FastAPI:
    """Assemble the Developer Platform application. Misconfiguration fails here, at startup."""
    settings = settings or ConsoleSettings.from_env()
    settings.validate()
    webrtc_settings = webrtc_settings or WebRTCSettings.from_env()

    # Validates the WebRTC settings and the ICE policy itself; an invalid runtime configuration
    # is refused before any console surface exists.
    app = build_webrtc_app(webrtc_settings)

    app.state.console_runtime = meta.RuntimeDescription(
        transport=webrtc_settings.transport,
        webrtc_signalling=webrtc_settings.signalling_enabled,
        ice_policy=resolve_ice_policy(webrtc_settings).describe(),
    )
    app.state.console_bundle = settings.dist_dir is not None

    register_console_error_handler(app)
    app.include_router(meta.router)
    app.include_router(meta.fallback_router)  # last among console routers

    if settings.dist_dir is not None:
        mount_console_bundle(app, settings.dist_dir)

    return app


__all__ = ["build_console_app"]
