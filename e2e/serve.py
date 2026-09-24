"""E2E harness server (M12.10): the real console application, with a real WebRTC media path and
**deterministic** speech providers, for driving the product from a real browser.

This is test infrastructure, not a product profile. It is the M12.4 test composition
(`tests/test_console_playground._real_media_controller`) run as a server:

* the console application, built by the unchanged `build_console_app` (auth, agents, playground,
  conversations, analytics, overview, API keys, docs, bundle);
* the frozen signalling endpoints, the frozen `create_session`/`end_session` path;
* the **real** WebRTC transport (aiortc), so a browser's `RTCPeerConnection` really connects;
* **deterministic** STT/LLM/TTS (a scripted PCM16 TTS), so no provider credential is needed or read;
* Epic 10's durable event log attached (as the console composition does), so conversations,
  analytics and the overview see real recorded events.

Because the media path is real WebRTC, the console is told `transport=webrtc` — the Playground's
precondition. The speech is **not** real: nothing this harness proves is real-provider evidence.

Run from the repository root (see `e2e/README.md`):

    PYTHONPATH=platform DATABASE_URL=sqlite:///<scratch>/e2e.db \\
      platform/.venv/bin/python e2e/serve.py --port 8790 --dist console/dist
"""

from __future__ import annotations

import argparse
from contextlib import asynccontextmanager
from pathlib import Path

import uvicorn


def build(dist: Path | None):
    from api.config import get_settings
    from api.console import ConsoleSettings, build_console_app
    from api.console.app import _event_spine
    from api.console.routers.meta import RuntimeDescription
    from api.webrtc.app import _persist_transcript
    from api.webrtc.binding import RendezvousBindingController
    from api.webrtc.config import WebRTCSettings
    from bootstrap import TRANSPORT_WEBRTC, build_registry, build_webrtc_media_endpoint
    from runtime.registry import CapabilityKind
    from runtime.service import RuntimeSessionService
    from tests.fake_speech import ScriptedTTS

    app = build_console_app(
        ConsoleSettings(signup="open", dist_dir=dist, cookie_secure=False),
        webrtc_settings=WebRTCSettings(signalling=True),
    )
    database, plane = _event_spine(get_settings().database_url)
    inner_lifespan = app.router.lifespan_context

    @asynccontextmanager
    async def lifespan(inner_app):
        async with inner_lifespan(inner_app):
            rendezvous = inner_app.state.webrtc_rendezvous
            providers = build_registry(
                transport=TRANSPORT_WEBRTC,
                media_endpoint=build_webrtc_media_endpoint(rendezvous, negotiation_timeout_s=20.0),
            )
            providers.unregister(CapabilityKind.TTS, "local")
            providers.register(CapabilityKind.TTS, ScriptedTTS(frames_per_call=2), default=True)
            inner_app.state.session_controller = RendezvousBindingController(
                RuntimeSessionService(
                    providers, on_transcript=_persist_transcript, observability=plane
                ),
                rendezvous,
            )
            described = inner_app.state.console_runtime
            inner_app.state.console_runtime = RuntimeDescription(
                transport="webrtc",
                webrtc_signalling=True,
                ice_policy=f"{described.ice_policy} (E2E harness: deterministic providers)",
            )
            yield
        database.close()

    app.router.lifespan_context = lifespan
    return app


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument("--port", type=int, default=8790)
    parser.add_argument("--dist", type=Path, default=None, help="built console (console/dist)")
    args = parser.parse_args()
    dist = args.dist.resolve() if args.dist else None
    uvicorn.run(build(dist), host="127.0.0.1", port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
