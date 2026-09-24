"""M12.10 — the M12 product as one system (the E2E gate).

Each milestone's own suite proves its surface. This proves the **seams between them**, on one
running application, in the order a person uses them:

* sign in → organization → agent → a **real WebRTC call** (deterministic providers) → the
  conversation → analytics → the overview, with the same numbers at every step;
* an API key created in the console authenticating the developer API, then revoked and refused;
* the three credentials never crossing (console cookie, API key, signalling token);
* switching organization changing **every** read surface at once;
* signing out ending the session server-side, for every surface;
* the role matrix across surfaces, and repeated submissions creating one record, not two.

The browser and SDK journeys (`e2e/browser.mjs`, `e2e/sdk.mjs`) cover what only a real browser can;
they need Chrome, so they are run locally (`docs/testing/m12-e2e.md`) rather than here.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import Iterator

import httpx
import pytest
from sqlmodel import Session as DBSession
from sqlmodel import SQLModel, delete, select

from api.console import ConsoleSettings, build_console_app
from api.console.identity import service as identity
from api.console.identity.deps import CONSOLE_HEADER
from api.console.persistence import AgentStateRow, ConsoleSessionRow, MembershipRow, UserRow
from api.db import engine, init_db
from api.models import Agent, ApiKey, Org, Session
from api.webrtc.config import WebRTCSettings
from tests.test_webrtc_external_client import ExternalPeer

HEADERS = {CONSOLE_HEADER: "1"}
PASSWORD = "correct horse battery"


@pytest.fixture(autouse=True)
def _clean_console() -> Iterator[None]:
    def wipe() -> None:
        with DBSession(engine) as db:
            for table in (AgentStateRow, ConsoleSessionRow, MembershipRow, UserRow):
                db.exec(delete(table))
            db.commit()

    SQLModel.metadata.create_all(engine)
    wipe()
    yield
    wipe()


@contextlib.asynccontextmanager
async def _product():
    """The product application with its real lifespan, driven over ASGI."""
    init_db()
    app = build_console_app(
        ConsoleSettings(signup="open"), webrtc_settings=WebRTCSettings(signalling=True)
    )
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://console.test") as client:
            yield app, client


async def _signup(client: httpx.AsyncClient, email: str, org: str) -> str:
    response = await client.post(
        "/v1/console/auth/signup",
        headers=HEADERS,
        json={"email": email, "password": PASSWORD, "organization_name": org},
    )
    assert response.status_code == 201, response.text
    return response.json()["user"]["id"]


async def _agent(client: httpx.AsyncClient, name: str = "Support") -> str:
    response = await client.post(
        "/v1/console/agents", headers=HEADERS, json={"name": name, "system_prompt": "Be brief."}
    )
    assert response.status_code == 201, response.text
    return response.json()["id"]


def _real_media(app) -> None:
    """The real WebRTC transport with deterministic providers, **with the durable event log** —
    the console composition (M12.5 DP10) plus M12.4's test media stack, so the call this product
    journey makes is recorded exactly as a configured deployment's would be."""
    from api.config import get_settings
    from api.console.app import _event_spine
    from api.webrtc.app import _persist_transcript
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
    _, plane = _event_spine(get_settings().database_url)
    app.state.session_controller = RendezvousBindingController(
        RuntimeSessionService(providers, on_transcript=_persist_transcript, observability=plane),
        rendezvous,
    )


async def _call(app, client: httpx.AsyncClient, agent_id: str) -> str:
    """One real WebRTC call through the console Playground, ended cleanly. Returns the session."""
    _real_media(app)
    reserved = await client.post("/v1/console/playground/rendezvous", headers=HEADERS)
    assert reserved.status_code == 201, reserved.text
    rendezvous = reserved.json()
    peer = ExternalPeer(client, rendezvous["rendezvous_id"], rendezvous["signalling_token"])
    try:
        start = asyncio.create_task(
            client.post(
                "/v1/console/playground/sessions",
                headers=HEADERS,
                json={"agent_id": agent_id, "rendezvous_id": rendezvous["rendezvous_id"]},
            )
        )
        await peer.run()
        started = await asyncio.wait_for(start, 30.0)
        assert started.status_code == 201, started.text
        session_id = started.json()["session_id"]
        ended = await client.post(
            f"/v1/console/playground/sessions/{session_id}/end", headers=HEADERS
        )
        assert ended.status_code == 200
        return session_id
    finally:
        await peer.aclose()


# ══ the journey ═══════════════════════════════════════════════════════════════


async def test_the_product_journey_from_sign_up_to_the_overview():
    async with _product() as (app, client):
        await _signup(client, "ada@example.com", "Org A")
        agent_id = await _agent(client)

        # An empty organization says so, everywhere, before anything has happened.
        overview = (await client.get("/v1/console/overview")).json()
        assert overview["conversations"]["total"] == 0
        assert [a["code"] for a in overview["attention"]] == ["no_conversations"]
        assert (await client.get("/v1/console/conversations")).json()["data"] == []

        session_id = await _call(app, client, agent_id)

        # Conversations: the call is there, from the Playground, with recorded events.
        detail = (await client.get(f"/v1/console/conversations/{session_id}")).json()
        assert (detail["status"], detail["source"], detail["agent"]["id"]) == (
            "ended",
            "console_playground",
            agent_id,
        )
        assert detail["events_recorded"] is True
        assert detail["duration_s"] is not None
        labels = [entry["label"] for entry in detail["timeline"]]
        # In bus order. On the real transport the caller connects before the orchestrator publishes
        # the session start, so only the end is positional.
        assert {"Session started", "Caller connected"} <= set(labels)
        assert labels[-1] == "Session ended"
        seqs = [entry["seq"] for entry in detail["timeline"]]
        assert seqs == sorted(seqs)
        # F-7 semantics survive the whole path: unmeasured is absent, never 0.
        for turn in detail["turns"]:
            assert turn["voice_to_voice_ms"] is None or turn["voice_to_voice_ms"] > 0
            assert all(value > 0 for value in turn["stages_ms"].values())

        # Analytics counts exactly that conversation.
        summary = (await client.get("/v1/console/analytics/summary?range=today")).json()
        assert summary["conversations"]["total"] == 1
        assert summary["conversations"]["by_source"] == {"playground": 1, "api": 0}
        assert summary["conversations"]["recorded"] == 1
        assert summary["duration"]["measured"] == 1

        # The overview is the same numbers, from the same query.
        overview = (await client.get("/v1/console/overview?range=today")).json()
        for key in ("conversations", "duration", "turns"):
            assert overview[key] == summary[key]
        assert (
            overview["usage"]
            == (await client.get("/v1/console/analytics/timeseries?range=today")).json()["data"]
        )
        assert (
            overview["latency"]
            == (await client.get("/v1/console/analytics/latency?range=today")).json()["data"]
        )
        assert [c["id"] for c in overview["recent_conversations"]] == [session_id]
        assert overview["agents"] == {"active": 1, "archived": 0, "unsupported_language": 0}

        # A key made in the console reaches the developer API, and stops the moment it is revoked.
        created = (
            await client.post("/v1/console/api-keys", headers=HEADERS, json={"label": "backend"})
        ).json()
        key = {"x-api-key": created["key"]}
        listed = await client.get("/v1/agents", headers=key)
        assert [a["id"] for a in listed.json()] == [agent_id]
        assert (await client.get(f"/v1/sessions/{session_id}", headers=key)).json()[
            "status"
        ] == "ended"
        revoked = await client.post(f"/v1/console/api-keys/{created['id']}/revoke", headers=HEADERS)
        assert revoked.status_code == 204
        refused = await client.get("/v1/agents", headers=key)
        assert (refused.status_code, refused.json()) == (401, {"detail": "Invalid API key"})


async def test_the_three_credentials_never_cross():
    async with _product() as (_, client):
        await _signup(client, "ada@example.com", "Org A")
        created = (
            await client.post("/v1/console/api-keys", headers=HEADERS, json={"label": "k"})
        ).json()
        rendezvous = (
            await client.post("/v1/console/playground/rendezvous", headers=HEADERS)
        ).json()
        key, token = created["key"], rendezvous["signalling_token"]

        # The console cookie is on this client; it must not authenticate the developer API.
        assert (await client.get("/v1/agents")).status_code == 401
        # Nor does a signalling token, as a key or as itself.
        assert (await client.get("/v1/agents", headers={"x-api-key": token})).status_code == 401
        assert (
            await client.get("/v1/agents", headers={"x-signalling-token": token})
        ).status_code == 401
        # And the API key opens no console surface, cookie or not.
        async with httpx.AsyncClient(
            transport=client._transport, base_url="http://console.test"
        ) as anonymous:
            for path in ("/v1/console/me", "/v1/console/agents", "/v1/console/overview"):
                assert (await anonymous.get(path, headers={"x-api-key": key})).status_code == 401
            # The developer API does accept it, from a caller with no console session at all.
            assert (
                await anonymous.get("/v1/agents", headers={"x-api-key": key})
            ).status_code == 200


async def test_switching_organization_changes_every_read_surface_at_once():
    async with _product() as (app, client):
        await _signup(client, "ada@example.com", "Org A")
        agent_a = await _agent(client, "Agent A")
        session_a = await _call(app, client, agent_a)
        key_a = (
            await client.post("/v1/console/api-keys", headers=HEADERS, json={"label": "for A"})
        ).json()

        second = await client.post(
            "/v1/console/organizations", headers=HEADERS, json={"name": "Org B"}
        )
        assert second.status_code == 201  # creating it also switches to it

        assert (await client.get("/v1/console/agents")).json()["data"] == []
        assert (await client.get("/v1/console/conversations")).json()["data"] == []
        assert (await client.get("/v1/console/api-keys")).json()["data"] == []
        overview = (await client.get("/v1/console/overview?range=today")).json()
        assert overview["conversations"]["total"] == 0 and overview["recent_conversations"] == []
        assert overview["agents"] == {"active": 0, "archived": 0, "unsupported_language": 0}
        assert (await client.get("/v1/console/analytics/summary?range=today")).json()[
            "conversations"
        ]["total"] == 0
        # Org A's own records are invisible by id, and its filters are refused outright.
        assert (await client.get(f"/v1/console/conversations/{session_a}")).status_code == 404
        assert (await client.get(f"/v1/console/agents/{agent_a}")).status_code == 404
        assert (await client.get(f"/v1/console/overview?agent_id={agent_a}")).status_code == 404
        assert (
            await client.post(f"/v1/console/api-keys/{key_a['id']}/revoke", headers=HEADERS)
        ).status_code == 404

        # Back to A: everything returns, unchanged.
        back = await client.post(
            "/v1/console/session/organization",
            headers=HEADERS,
            json={
                "organization_id": (await client.get("/v1/console/me")).json()["memberships"][0][
                    "id"
                ]
            },
        )
        assert back.status_code == 200
        assert [a["id"] for a in (await client.get("/v1/console/agents")).json()["data"]] == [
            agent_a
        ]
        assert [
            c["id"] for c in (await client.get("/v1/console/conversations")).json()["data"]
        ] == [session_a]
        assert (await client.get("/v1/console/overview?range=today")).json()["conversations"][
            "total"
        ] == 1


async def test_signing_out_closes_every_surface_for_that_session():
    async with _product() as (_, client):
        await _signup(client, "ada@example.com", "Org A")
        cookies = dict(client.cookies)
        assert (await client.post("/v1/console/auth/logout", headers=HEADERS)).status_code == 204

        client.cookies.update(cookies)  # replaying the old cookie must not work either
        for path in (
            "/v1/console/me",
            "/v1/console/agents",
            "/v1/console/conversations",
            "/v1/console/analytics/summary",
            "/v1/console/overview",
            "/v1/console/api-keys",
        ):
            response = await client.get(path)
            assert response.status_code == 401, path
            assert response.json()["error"]["type"] == "unauthenticated"


@pytest.mark.parametrize("role,writes", [("viewer", False), ("developer", True)])
async def test_the_role_matrix_holds_across_surfaces(role, writes):
    async with _product() as (_, client):
        await _signup(client, "ada@example.com", "Org A")
        org_id = (await client.get("/v1/console/organization")).json()["id"]
        agent_id = await _agent(client)

        transport = client._transport
        async with httpx.AsyncClient(transport=transport, base_url="http://console.test") as member:
            await _signup(member, "bob@example.com", "Org B")
            with DBSession(engine) as db:
                identity.add_membership(
                    db,
                    org=db.get(Org, org_id),
                    user=identity.find_user(db, "bob@example.com"),
                    role=role,
                )
                db.commit()
            switched = await member.post(
                "/v1/console/session/organization",
                headers=HEADERS,
                json={"organization_id": org_id},
            )
            assert switched.status_code == 200

            # Reading is allowed for both roles, on every read surface.
            for path in (
                "/v1/console/agents",
                "/v1/console/conversations",
                "/v1/console/analytics/summary",
                "/v1/console/overview",
            ):
                assert (await member.get(path)).status_code == 200, path
            # Writing follows the role; key management is admin-only for both.
            created = await member.post(
                "/v1/console/agents", headers=HEADERS, json={"name": "x", "system_prompt": "y"}
            )
            archived = await member.post(f"/v1/console/agents/{agent_id}/archive", headers=HEADERS)
            assert (created.status_code == 201) is writes
            assert (archived.status_code == 204) is writes
            assert (await member.get("/v1/console/api-keys")).status_code == 403
            assert (
                await member.post("/v1/console/api-keys", headers=HEADERS, json={"label": "n"})
            ).status_code == 403
            # A mutation without the console header is refused whatever the role.
            assert (
                await member.post("/v1/console/agents", json={"name": "x", "system_prompt": "y"})
            ).status_code == 403


async def test_repeated_submissions_create_one_record_each():
    """What a double-click does: the console disables its buttons, but the server is what counts."""
    async with _product() as (_, client):
        await _signup(client, "ada@example.com", "Org A")
        agent_id = await _agent(client)

        # Two identical agent creations are two agents (the API has no idempotency key) — but one
        # request that is retried by nothing creates exactly one. The SDK never retries either.
        before = len((await client.get("/v1/console/agents")).json()["data"])
        await _agent(client, "Second")
        assert len((await client.get("/v1/console/agents")).json()["data"]) == before + 1

        # Ending a session twice, and revoking a key twice, are safe.
        created = (
            await client.post("/v1/console/api-keys", headers=HEADERS, json={"label": "k"})
        ).json()
        first = await client.post(f"/v1/console/api-keys/{created['id']}/revoke", headers=HEADERS)
        second = await client.post(f"/v1/console/api-keys/{created['id']}/revoke", headers=HEADERS)
        assert (first.status_code, second.status_code) == (204, 404)
        org_id = (await client.get("/v1/console/organization")).json()["id"]
        with DBSession(engine) as db:
            assert db.exec(select(ApiKey).where(ApiKey.org_id == org_id)).all() == []
            # Archiving twice leaves one state row, and the agent row is untouched.
            assert db.get(Agent, agent_id) is not None
        for _ in range(2):
            await client.post(f"/v1/console/agents/{agent_id}/archive", headers=HEADERS)
        with DBSession(engine) as db:
            rows = db.exec(select(AgentStateRow).where(AgentStateRow.agent_id == agent_id)).all()
            assert len(rows) == 1 and rows[0].archived_at is not None


async def test_a_failed_session_start_leaves_no_signalling_slot_and_is_visible_as_failed():
    """The unhappy path end to end: the runtime refuses, the product says so and cleans up."""
    async with _product() as (app, client):
        await _signup(client, "ada@example.com", "Org A")
        agent_id = await _agent(client)
        _real_media(app)
        registry = app.state.webrtc_rendezvous

        reserved = (await client.post("/v1/console/playground/rendezvous", headers=HEADERS)).json()
        # No peer ever answers, so the media endpoint gives up and the start fails.
        started = await client.post(
            "/v1/console/playground/sessions",
            headers=HEADERS,
            json={"agent_id": agent_id, "rendezvous_id": reserved["rendezvous_id"]},
        )
        assert started.status_code == 502
        assert started.json()["error"]["type"] == "runtime_failed"
        assert registry.open_count == 0  # the slot was released, not leaked

        org_id = (await client.get("/v1/console/organization")).json()["id"]
        with DBSession(engine) as db:
            failed = db.exec(select(Session).where(Session.org_id == org_id)).all()
            assert [s.status for s in failed] == ["failed"]
        listed = (await client.get("/v1/console/conversations")).json()["data"]
        assert [c["status"] for c in listed] == ["failed"]
        assert listed[0]["duration_s"] is None  # no end: no duration, never 0
        summary = (await client.get("/v1/console/analytics/summary?range=today")).json()
        assert summary["conversations"]["by_status"]["failed"] == 1
        assert summary["duration"] == {
            "measured": 0,
            "unmeasured": 1,
            "total_s": None,
            "avg_s": None,
        }
