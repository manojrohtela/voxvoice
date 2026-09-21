"""M12.3 — console agent management over the M11 `agent` row (ADR-0055 DP1, LLD §4).

What is proven here:

* the console and the developer API manage **one** agent row — no second agent model;
* every field the console lets you edit reaches `StartSessionRequest` through the frozen
  `create_session`, and fields that do not reach the runtime are neither shown nor written (AR-25);
* agent languages come from what the configured speech recognizer declares (finding F-4), and an
  agent whose language it would refuse is flagged;
* archive replaces delete (finding F-5); archived agents vanish from the console only;
* tenant isolation (404 across organizations), the role matrix and CSRF hold for every route.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session as DBSession
from sqlmodel import SQLModel, delete, select

import bootstrap
from api.console import ConsoleSettings, build_console_app
from api.console.app import _declared_agent_languages
from api.console.identity import service as identity
from api.console.identity.deps import CONSOLE_HEADER
from api.console.persistence import AgentStateRow, ConsoleSessionRow, MembershipRow, UserRow
from api.db import engine
from api.models import Agent, ApiKey, Org, Session
from api.routers.sessions import create_session
from api.schemas import SessionCreate
from api.security import generate_api_key, hash_key
from api.webrtc.config import WebRTCSettings
from core.session_control import SessionController, StartSessionRequest, StartSessionResult

HEADERS = {CONSOLE_HEADER: "1"}
PASSWORD = "correct horse battery"
AGENT = {
    "name": "Support",
    "system_prompt": "You are a concise support agent.",
    "first_message": "Hi, how can I help?",
    "llm_model": "openai/gpt-oss-20b",
    "tts_voice": "voice-1234",
}


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


def _signed_in(email: str, org: str) -> TestClient:
    client = TestClient(
        build_console_app(ConsoleSettings(signup="open"), webrtc_settings=WebRTCSettings())
    )
    client.__enter__()
    response = client.post(
        "/v1/console/auth/signup",
        headers=HEADERS,
        json={"email": email, "password": PASSWORD, "organization_name": org},
    )
    assert response.status_code == 201
    return client


@pytest.fixture
def ada() -> Iterator[TestClient]:
    client = _signed_in("ada@example.com", "Org A")
    yield client
    client.__exit__(None, None, None)


@pytest.fixture
def bob() -> Iterator[TestClient]:
    client = _signed_in("bob@example.com", "Org B")
    yield client
    client.__exit__(None, None, None)


def _org_id(client: TestClient) -> str:
    return client.get("/v1/console/organization").json()["id"]


def _create(client: TestClient, **overrides: object):
    return client.post("/v1/console/agents", headers=HEADERS, json={**AGENT, **overrides})


def _api_key_for(org_id: str) -> str:
    raw = generate_api_key()
    with DBSession(engine) as db:
        db.add(ApiKey(org_id=org_id, key_hash=hash_key(raw), label="test"))
        db.commit()
    return raw


# ── options: what the running speech recognizer declares (F-4) ───────────────


def test_options_are_the_languages_the_speech_recognizer_declares(ada):
    response = ada.get("/v1/console/agents/options")
    assert response.status_code == 200
    assert response.json() == {
        "languages": list(bootstrap.declared_stt_languages(bootstrap.STT_LOCAL)),
        "default_language": bootstrap.declared_stt_languages(bootstrap.STT_LOCAL)[0],
    }


def test_the_real_profile_asks_the_real_recognizer():
    settings = WebRTCSettings(transport="webrtc", voice_id="voice")
    assert _declared_agent_languages(settings) == bootstrap.declared_stt_languages(
        bootstrap.REAL_PROVIDER_PROFILE["stt"]
    )


@pytest.mark.parametrize("profile", [bootstrap.STT_LOCAL, bootstrap.STT_DEEPGRAM])
def test_declared_languages_are_read_from_the_plugin_not_restated(profile):
    """The helper must agree with the plugin exactly as `build_registry` constructs it."""

    class _Client:  # the DeepgramSTT test seam (a `TranscriptionClient`), never opened here
        async def open(self, spec):  # pragma: no cover - not called
            raise AssertionError("no stream is opened to read declared capabilities")

    plugin = (
        bootstrap.LocalSTT()
        if profile == bootstrap.STT_LOCAL
        else bootstrap.DeepgramSTT(_Client())  # type: ignore[arg-type]
    )
    assert bootstrap.declared_stt_languages(profile) == tuple(plugin.capabilities.languages)
    with pytest.raises(ValueError):
        bootstrap.declared_stt_languages("nonexistent")


# ── create / read / list ──────────────────────────────────────────────────────


def test_creating_an_agent_writes_the_one_m11_agent_row(ada):
    response = _create(ada)

    assert response.status_code == 201
    body = response.json()
    assert body["language"] == "en" and body["language_supported"] is True
    assert {k: body[k] for k in AGENT} == AGENT
    with DBSession(engine) as db:
        agent = db.get(Agent, body["id"])
        assert agent is not None and agent.org_id == _org_id(ada)
        state = db.get(AgentStateRow, body["id"])
        assert state.created_by == identity.find_user(db, "ada@example.com").id

    # The developer API serves the very same row.
    key = _api_key_for(_org_id(ada))
    served = ada.get(f"/v1/agents/{body['id']}", headers={"x-api-key": key}).json()
    assert (served["name"], served["system_prompt"], served["language"]) == (
        AGENT["name"],
        AGENT["system_prompt"],
        "en",
    )


def test_the_organization_comes_from_the_session_not_the_body(ada, bob):
    response = _create(ada, org_id=_org_id(bob))
    assert response.status_code == 201
    with DBSession(engine) as db:
        assert db.get(Agent, response.json()["id"]).org_id == _org_id(ada)


def test_list_shows_the_organizations_agents_newest_first(ada):
    first = _create(ada, name="First").json()["id"]
    second = _create(ada, name="Second").json()["id"]

    listed = ada.get("/v1/console/agents").json()["data"]
    assert [a["id"] for a in listed] == [second, first]


def test_an_empty_organization_lists_nothing(ada):
    assert ada.get("/v1/console/agents").json() == {"data": []}


def test_an_agent_created_by_the_developer_api_with_an_unsupported_language_is_flagged(ada):
    key = _api_key_for(_org_id(ada))
    created = ada.post(
        "/v1/agents",
        headers={"x-api-key": key},
        json={"name": "Legacy", "system_prompt": "x"},  # the M11 default language is en-IN
    ).json()

    view = ada.get(f"/v1/console/agents/{created['id']}").json()
    assert view["language"] == "en-IN"
    assert view["language_supported"] is False


# ── validation ────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "overrides,message_fragment",
    [
        ({"name": "  "}, "Name"),
        ({"system_prompt": ""}, "Instructions"),
        ({"language": "en-IN"}, "language"),
        ({"system_prompt": "x" * 20_001}, "Instructions"),
        ({"llm_model": "two words"}, "Model"),
        ({"tts_voice": "voice;drop"}, "Voice"),
        ({"name": "n" * 101}, "Name"),
    ],
)
def test_invalid_agents_are_refused_with_a_useful_message(ada, overrides, message_fragment):
    response = _create(ada, **overrides)
    assert response.status_code == 400
    assert response.json()["error"]["type"] == "invalid_request"
    assert message_fragment in response.json()["error"]["message"]
    assert ada.get("/v1/console/agents").json()["data"] == []


def test_a_missing_required_field_uses_the_console_envelope(ada):
    response = ada.post("/v1/console/agents", headers=HEADERS, json={"name": "No prompt"})
    assert response.status_code == 422
    assert response.json()["error"]["type"] == "invalid_request"


def test_blank_optional_fields_are_stored_as_absent(ada):
    body = _create(ada, first_message="   ", llm_model="", tts_voice=None).json()
    assert (body["first_message"], body["llm_model"], body["tts_voice"]) == (None, None, None)


# ── update ────────────────────────────────────────────────────────────────────


def test_patch_changes_only_what_was_sent(ada):
    agent = _create(ada).json()

    updated = ada.patch(
        f"/v1/console/agents/{agent['id']}", headers=HEADERS, json={"name": "Renamed"}
    )
    assert updated.status_code == 200
    body = updated.json()
    assert body["name"] == "Renamed"
    assert body["system_prompt"] == AGENT["system_prompt"]
    assert body["updated_at"] > agent["updated_at"]


def test_patch_with_null_clears_an_optional_field(ada):
    agent_id = _create(ada).json()["id"]
    body = ada.patch(
        f"/v1/console/agents/{agent_id}", headers=HEADERS, json={"first_message": None}
    ).json()
    assert body["first_message"] is None


def test_the_console_never_writes_fields_that_do_not_reach_the_runtime(ada):
    """F-1: `allow_interruptions`, endpointing and `tools` do not act, so the console neither
    shows nor writes them — a value set through the developer API survives a console edit."""
    agent_id = _create(ada).json()["id"]
    with DBSession(engine) as db:
        agent = db.get(Agent, agent_id)
        agent.allow_interruptions, agent.tools = False, [{"name": "lookup"}]
        agent.max_endpointing_delay = 3.0
        db.add(agent)
        db.commit()

    response = ada.patch(
        f"/v1/console/agents/{agent_id}",
        headers=HEADERS,
        json={"name": "Edited", "allow_interruptions": True, "tools": []},
    )
    assert response.status_code == 200
    assert "allow_interruptions" not in response.json()
    assert "tools" not in response.json()
    with DBSession(engine) as db:
        agent = db.get(Agent, agent_id)
        assert agent.allow_interruptions is False
        assert agent.tools == [{"name": "lookup"}]
        assert agent.max_endpointing_delay == 3.0


# ── archive (F-5) ─────────────────────────────────────────────────────────────


def test_archiving_hides_the_agent_from_the_console_and_keeps_the_row(ada):
    agent_id = _create(ada).json()["id"]

    assert ada.post(f"/v1/console/agents/{agent_id}/archive", headers=HEADERS).status_code == 204
    assert ada.get("/v1/console/agents").json()["data"] == []
    assert ada.get(f"/v1/console/agents/{agent_id}").status_code == 404
    assert ada.patch(
        f"/v1/console/agents/{agent_id}", headers=HEADERS, json={"name": "x"}
    ).status_code == 404
    with DBSession(engine) as db:
        assert db.get(Agent, agent_id) is not None  # not deleted: sessions keep their agent
        state = db.get(AgentStateRow, agent_id)
        assert state.archived_at is not None
        assert state.archived_by == identity.find_user(db, "ada@example.com").id


def test_an_agent_created_by_the_developer_api_can_be_archived(ada):
    key = _api_key_for(_org_id(ada))
    created = ada.post(
        "/v1/agents", headers={"x-api-key": key}, json={"name": "API", "system_prompt": "x"}
    ).json()
    archived = ada.post(f"/v1/console/agents/{created['id']}/archive", headers=HEADERS)
    assert archived.status_code == 204
    assert ada.get("/v1/console/agents").json()["data"] == []


def test_the_state_row_never_blocks_a_developer_api_delete():
    fk = next(iter(AgentStateRow.__table__.c.agent_id.foreign_keys))
    assert fk.ondelete == "CASCADE"


# ── tenant isolation ──────────────────────────────────────────────────────────


def test_another_organizations_agent_is_not_found(ada, bob):
    agent_id = _create(ada).json()["id"]

    assert bob.get(f"/v1/console/agents/{agent_id}").status_code == 404
    patched = bob.patch(f"/v1/console/agents/{agent_id}", headers=HEADERS, json={"name": "Stolen"})
    assert patched.status_code == 404
    archived = bob.post(f"/v1/console/agents/{agent_id}/archive", headers=HEADERS)
    assert archived.status_code == 404
    assert bob.get("/v1/console/agents").json()["data"] == []
    missing = bob.get("/v1/console/agents/agt_doesnotexist")
    assert missing.json()["error"]["message"] == patched.json()["error"]["message"]

    with DBSession(engine) as db:
        agent = db.get(Agent, agent_id)
        assert agent.name == AGENT["name"]
        assert db.get(AgentStateRow, agent_id).archived_at is None


def test_switching_organization_switches_the_agent_list(ada):
    _create(ada, name="In A")
    ada.post("/v1/console/organizations", headers=HEADERS, json={"name": "Second"})
    assert ada.get("/v1/console/agents").json()["data"] == []


# ── roles, CSRF, authentication ───────────────────────────────────────────────


@pytest.mark.parametrize(
    "role,can_write", [("viewer", False), ("developer", True), ("admin", True)]
)
def test_the_role_matrix(ada, bob, role, can_write):
    agent_id = _create(ada).json()["id"]
    with DBSession(engine) as db:
        identity.add_membership(
            db,
            org=db.get(Org, _org_id(ada)),
            user=identity.find_user(db, "bob@example.com"),
            role=role,
        )
        db.commit()
    bob.post(
        "/v1/console/session/organization", headers=HEADERS, json={"organization_id": _org_id(ada)}
    )

    assert bob.get("/v1/console/agents").status_code == 200
    assert bob.get(f"/v1/console/agents/{agent_id}").status_code == 200
    expected = (201, 200, 204) if can_write else (403, 403, 403)
    patched = bob.patch(f"/v1/console/agents/{agent_id}", headers=HEADERS, json={"name": "x"})
    archived = bob.post(f"/v1/console/agents/{agent_id}/archive", headers=HEADERS)
    assert (_create(bob).status_code, patched.status_code, archived.status_code) == expected


def test_writes_without_the_console_header_are_refused(ada):
    agent_id = _create(ada).json()["id"]
    assert ada.post("/v1/console/agents", json=AGENT).status_code == 403
    assert ada.patch(f"/v1/console/agents/{agent_id}", json={"name": "x"}).status_code == 403
    assert ada.post(f"/v1/console/agents/{agent_id}/archive").status_code == 403


def test_agents_need_a_session():
    with TestClient(build_console_app(ConsoleSettings(), webrtc_settings=WebRTCSettings())) as c:
        for path in ("/v1/console/agents", "/v1/console/agents/options"):
            assert c.get(path).status_code == 401


# ── every editable field reaches the runtime ──────────────────────────────────


class _RecordingController(SessionController):
    def __init__(self) -> None:
        self.requests: list[StartSessionRequest] = []

    async def start(self, request: StartSessionRequest) -> StartSessionResult:
        self.requests.append(request)
        return StartSessionResult(session_id=request.session_id, engine="test", channel="web")

    async def end(self, session_id: str) -> None:
        return None

    def conversation_state(self, session_id: str) -> str | None:
        return None


async def test_every_console_field_reaches_the_start_session_request(ada):
    """The fields the console edits are exactly the ones the frozen `create_session` carries into
    the runtime seam. Driven through the real M11 handler, not a re-statement of it."""
    agent_id = _create(ada, language="en").json()["id"]
    controller = _RecordingController()

    with DBSession(engine) as db:
        org = db.get(Org, _org_id(ada))
        await create_session(
            body=SessionCreate(agent_id=agent_id), org=org, db=db, controller=controller
        )

    (request,) = controller.requests
    assert (
        request.system_prompt,
        request.first_message,
        request.language,
        request.llm_model,
        request.tts_voice,
    ) == (
        AGENT["system_prompt"],
        AGENT["first_message"],
        "en",
        AGENT["llm_model"],
        AGENT["tts_voice"],
    )
    with DBSession(engine) as db:  # the frozen handler recorded the session for this agent
        recorded = db.exec(select(Session).where(Session.id == request.session_id)).one()
        assert (recorded.agent_id, recorded.org_id) == (agent_id, request.org_id)
