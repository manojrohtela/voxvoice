"""M12.4 — the console Playground over the one M11 voice path (ADR-0055 DP5, LLD §5).

What is proven here:

* a **real** call negotiates through the console: cookie-authenticated routes reserve the
  rendezvous and start the session through the frozen `create_webrtc_session`; a genuine
  `RTCPeerConnection` (the M11 harness's browser stand-in, reused — not copied) answers over the
  existing signalling endpoints with only the single-purpose token, real media crosses into the
  runtime, and ending through the console releases everything;
* the session is recorded as a Playground call, by whom;
* authorization is server-side: membership, role, organization, archived and unsupported agents,
  foreign rendezvous and foreign sessions are all refused, before the runtime is touched;
* nothing the browser receives is a long-lived or provider credential;
* with voice not enabled, the Playground says so instead of pretending.

**Not live-provider evidence.** The providers are the deterministic ones (a PCM16 scripted TTS,
exactly as M11's own end-to-end test composes), the peer is aiortc in-process, and no human speaks.
The live check is the manual run in `docs/runtime/local-run.md`.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import Iterator

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session as DBSession
from sqlmodel import SQLModel, delete, select

from api.console import ConsoleSettings, build_console_app
from api.console.identity import service as identity
from api.console.identity.deps import CONSOLE_HEADER
from api.console.persistence import AgentStateRow, ConsoleSessionRow, MembershipRow, UserRow
from api.db import engine
from api.models import Agent, Org, Session
from api.webrtc.config import WebRTCSettings
from tests.test_webrtc_external_client import ExternalPeer, _frames_flow

HEADERS = {CONSOLE_HEADER: "1"}
PASSWORD = "correct horse battery"


@pytest.fixture(autouse=True)
def _no_console_accounts() -> Iterator[None]:
    def wipe() -> None:
        with DBSession(engine) as db:
            for table in (AgentStateRow, ConsoleSessionRow, MembershipRow, UserRow):
                db.exec(delete(table))
            db.commit()

    SQLModel.metadata.create_all(engine)
    wipe()
    yield
    wipe()


# ── helpers ───────────────────────────────────────────────────────────────────


def _client(**webrtc: object) -> TestClient:
    client = TestClient(
        build_console_app(ConsoleSettings(signup="open"), webrtc_settings=WebRTCSettings(**webrtc))
    )
    client.__enter__()
    return client


def _signup(client, email: str, org: str) -> None:
    response = client.post(
        "/v1/console/auth/signup",
        headers=HEADERS,
        json={"email": email, "password": PASSWORD, "organization_name": org},
    )
    assert response.status_code == 201


def _agent(client, **overrides) -> str:
    body = {"name": "Bot", "system_prompt": "hi", **overrides}
    response = client.post("/v1/console/agents", headers=HEADERS, json=body)
    assert response.status_code == 201, response.text
    return response.json()["id"]


@pytest.fixture
def ada() -> Iterator[TestClient]:
    client = _client(signalling=True)
    _signup(client, "ada@example.com", "Org A")
    yield client
    client.__exit__(None, None, None)


@pytest.fixture
def bob() -> Iterator[TestClient]:
    client = _client(signalling=True)
    _signup(client, "bob@example.com", "Org B")
    yield client
    client.__exit__(None, None, None)


def _sessions_for(agent_id: str) -> list[Session]:
    with DBSession(engine) as db:
        return list(db.exec(select(Session).where(Session.agent_id == agent_id)).all())


# ── voice not enabled ─────────────────────────────────────────────────────────


def test_without_voice_the_playground_says_so():
    client = _client()  # default: loopback, no signalling surface
    try:
        _signup(client, "ada@example.com", "Org A")
        response = client.post("/v1/console/playground/rendezvous", headers=HEADERS)
        assert response.status_code == 503
        error = response.json()["error"]
        assert error["type"] == "runtime_unavailable"
        assert "RUNTIME_TRANSPORT=webrtc" in error["message"]
    finally:
        client.__exit__(None, None, None)


# ── what the browser receives ─────────────────────────────────────────────────


def test_the_browser_receives_only_a_single_purpose_signalling_token(ada, monkeypatch):
    for name, value in {
        "VOXVOICE_SECRET_DEEPGRAM_API_KEY": "sentinel-stt",
        "VOXVOICE_SECRET_GROQ_API_KEY": "sentinel-llm",
        "VOXVOICE_SECRET_CARTESIA_API_KEY": "sentinel-tts",
    }.items():
        monkeypatch.setenv(name, value)

    response = ada.post("/v1/console/playground/rendezvous", headers=HEADERS)

    assert response.status_code == 201
    body = response.json()
    assert set(body) == {"rendezvous_id", "signalling_token", "expires_in_s"}
    assert body["rendezvous_id"].startswith("rdv_") and body["expires_in_s"] > 0
    for forbidden in ("sentinel-", "vf_", "x-api-key", "api_key"):
        assert forbidden not in response.text

    # The token is the M11 one: it opens the offer poll and nothing else.
    offer = ada.get(
        f"/v1/webrtc/rendezvous/{body['rendezvous_id']}/offer",
        headers={"x-signalling-token": body["signalling_token"]},
    )
    assert offer.status_code in (200, 204)
    assert ada.get("/v1/agents", headers={"x-api-key": body["signalling_token"]}).status_code == 401


# ── authorization, before the runtime is touched ──────────────────────────────


def test_the_playground_needs_a_session_a_role_and_the_console_header(ada, bob):
    anonymous = _client(signalling=True)
    try:
        refused = anonymous.post("/v1/console/playground/rendezvous", headers=HEADERS)
        assert refused.status_code == 401
    finally:
        anonymous.__exit__(None, None, None)

    assert ada.post("/v1/console/playground/rendezvous").status_code == 403  # no CSRF header

    org_id = ada.get("/v1/console/organization").json()["id"]
    with DBSession(engine) as db:  # bob becomes a viewer in Ada's organization
        identity.add_membership(
            db,
            org=db.get(Org, org_id),
            user=identity.find_user(db, "bob@example.com"),
            role="viewer",
        )
        db.commit()
    bob.post("/v1/console/session/organization", headers=HEADERS, json={"organization_id": org_id})
    refused = bob.post("/v1/console/playground/rendezvous", headers=HEADERS)
    assert refused.status_code == 403
    assert refused.json()["error"]["type"] == "forbidden"


@pytest.mark.parametrize("case", ["other_org", "archived", "missing"])
def test_only_an_active_agent_of_the_callers_organization_can_be_called(ada, bob, case):
    agent_id = {
        "other_org": lambda: _agent(bob),
        "archived": lambda: _archived(ada),
        "missing": lambda: "agt_doesnotexist",
    }[case]()
    rendezvous = ada.post("/v1/console/playground/rendezvous", headers=HEADERS).json()

    response = ada.post(
        "/v1/console/playground/sessions",
        headers=HEADERS,
        json={"agent_id": agent_id, "rendezvous_id": rendezvous["rendezvous_id"]},
    )
    assert response.status_code == 404
    assert response.json()["error"]["type"] == "not_found"
    assert _sessions_for(agent_id) == []  # refused before the runtime was touched


def _archived(client) -> str:
    agent_id = _agent(client)
    client.post(f"/v1/console/agents/{agent_id}/archive", headers=HEADERS)
    return agent_id


def test_an_agent_the_recognizer_cannot_hear_is_refused_with_a_reason(ada):
    agent_id = _agent(ada)
    with DBSession(engine) as db:  # e.g. an agent created through the developer API default
        agent = db.get(Agent, agent_id)
        agent.language = "en-IN"
        db.add(agent)
        db.commit()
    rendezvous = ada.post("/v1/console/playground/rendezvous", headers=HEADERS).json()

    response = ada.post(
        "/v1/console/playground/sessions",
        headers=HEADERS,
        json={"agent_id": agent_id, "rendezvous_id": rendezvous["rendezvous_id"]},
    )
    assert response.status_code == 400
    assert "en-IN" in response.json()["error"]["message"]
    assert _sessions_for(agent_id) == []


def test_another_organizations_rendezvous_cannot_be_used(ada, bob):
    agent_id = _agent(bob)
    foreign = ada.post("/v1/console/playground/rendezvous", headers=HEADERS).json()

    response = bob.post(
        "/v1/console/playground/sessions",
        headers=HEADERS,
        json={"agent_id": agent_id, "rendezvous_id": foreign["rendezvous_id"]},
    )
    assert response.status_code == 404
    assert "error" in response.json()  # the frozen handler's refusal, in the console envelope
    assert _sessions_for(agent_id) == []


def test_another_organizations_session_cannot_be_ended(ada, bob):
    org_a = ada.get("/v1/console/organization").json()["id"]
    agent_id = _agent(ada)
    with DBSession(engine) as db:
        session = Session(org_id=org_a, agent_id=agent_id, engine="voxvoice", status="active")
        db.add(session)
        db.commit()
        session_id = session.id

    response = bob.post(f"/v1/console/playground/sessions/{session_id}/end", headers=HEADERS)
    assert response.status_code == 404
    with DBSession(engine) as db:
        assert db.get(Session, session_id).status == "active"


# ── a real call through the console ───────────────────────────────────────────


@contextlib.asynccontextmanager
async def _running_console():
    """The console app with its full lifespan, driven over ASGI with a cookie-carrying client."""
    from api.db import init_db

    init_db()
    app = build_console_app(
        ConsoleSettings(signup="open"), webrtc_settings=WebRTCSettings(signalling=True)
    )
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://console.test") as client:
            yield app, client


def _real_media_controller(app) -> None:
    """The real WebRTC transport with deterministic providers — composed exactly as M11's own
    end-to-end test does (a PCM16 scripted TTS, because `LocalTTS` output cannot ride a real
    media path). Explicit here; never a default anywhere."""
    from api.webrtc.binding import RendezvousBindingController
    from bootstrap import TRANSPORT_WEBRTC, build_registry, build_webrtc_media_endpoint
    from runtime.registry import CapabilityKind
    from runtime.service import RuntimeSessionService

    from .fake_speech import ScriptedTTS

    rendezvous = app.state.webrtc_rendezvous
    providers = build_registry(
        transport=TRANSPORT_WEBRTC,
        media_endpoint=build_webrtc_media_endpoint(rendezvous, negotiation_timeout_s=20.0),
    )
    providers.unregister(CapabilityKind.TTS, "local")
    providers.register(CapabilityKind.TTS, ScriptedTTS(frames_per_call=2), default=True)
    app.state.session_controller = RendezvousBindingController(
        RuntimeSessionService(providers), rendezvous
    )


async def test_a_real_call_through_the_console_reaches_the_runtime_and_ends_cleanly():
    async with _running_console() as (app, client):
        signup = await client.post(
            "/v1/console/auth/signup",
            headers=HEADERS,
            json={"email": "ada@example.com", "password": PASSWORD, "organization_name": "Org A"},
        )
        assert signup.status_code == 201
        user_id = signup.json()["user"]["id"]
        created = await client.post(
            "/v1/console/agents", headers=HEADERS, json={"name": "Bot", "system_prompt": "hi"}
        )
        agent_id = created.json()["id"]
        _real_media_controller(app)
        registry = app.state.webrtc_rendezvous

        reserved = await client.post("/v1/console/playground/rendezvous", headers=HEADERS)
        rendezvous = reserved.json()
        # The browser's half: only the signalling token, on the existing M11 endpoints.
        peer = ExternalPeer(client, rendezvous["rendezvous_id"], rendezvous["signalling_token"])
        session_id = None
        try:
            start = asyncio.create_task(
                client.post(
                    "/v1/console/playground/sessions",
                    headers=HEADERS,
                    json={"agent_id": agent_id, "rendezvous_id": rendezvous["rendezvous_id"]},
                )
            )
            await peer.run()
            response = await asyncio.wait_for(start, 30.0)
            assert response.status_code == 201, response.text
            session_id = response.json()["session_id"]
            assert response.json()["agent_id"] == agent_id

            # Real media is crossing into the runtime pipeline.
            await asyncio.wait_for(_frames_flow(peer), 30.0)

            with DBSession(engine) as db:
                session = db.get(Session, session_id)
                assert (session.agent_id, session.status) == (agent_id, "active")
                assert session.meta == {"source": "console_playground", "started_by": user_id}
        finally:
            if session_id is not None:
                ended = await client.post(
                    f"/v1/console/playground/sessions/{session_id}/end", headers=HEADERS
                )
                assert ended.status_code == 200
                assert ended.json() == {"session_id": session_id, "status": "ended"}
            await peer.aclose()

        assert registry.open_count == 0  # ending released the signalling slot
        with DBSession(engine) as db:
            assert db.get(Session, session_id).status == "ended"
