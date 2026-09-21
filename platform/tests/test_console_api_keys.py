"""M12.7 — Developer API keys and the developer contract (ADR-0055 DP4, Amendment 3, LLD §8).

What is proven here:

* **one key model**: a console-created key is an M11 `apikey` row, authenticated by the unchanged
  `get_org` on the developer API. The secret appears in exactly one response and is stored only as
  its SHA-256;
* revocation is immediate and server-side; roles, CSRF and tenant isolation hold on every operation;
* the secret never reaches a list, an error, a log or the database;
* the three credentials stay separate: a key does not open the console, a console cookie does not
  open the developer API;
* the OpenAPI document states each operation's credential, and the developer contract document lists
  exactly the developer operations the server has — no more, no fewer;
* one deterministic developer journey end to end.
"""

from __future__ import annotations

import hashlib
import logging
import re
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session as DBSession
from sqlmodel import SQLModel, delete, select

from api.console import ConsoleSettings, build_console_app
from api.console.identity import service as identity
from api.console.identity.deps import CONSOLE_HEADER
from api.console.openapi import SURFACE
from api.console.persistence import AgentStateRow, ConsoleSessionRow, MembershipRow, UserRow
from api.db import engine
from api.models import ApiKey, Org
from api.webrtc.config import WebRTCSettings

HEADERS = {CONSOLE_HEADER: "1"}
PASSWORD = "correct horse battery"
REPO = Path(__file__).resolve().parents[2]
CONTRACT = REPO / "docs" / "apis" / "developer-api.md"
DOCS_PAGE = REPO / "console" / "src" / "features" / "developers" / "DeveloperDocsPage.tsx"


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


def _app(**webrtc):
    return build_console_app(
        ConsoleSettings(signup="open"), webrtc_settings=WebRTCSettings(**webrtc)
    )


def _signed_in(email: str, org: str) -> TestClient:
    client = TestClient(_app())
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


@pytest.fixture
def anonymous() -> Iterator[TestClient]:
    """No console cookie: how an external developer's backend reaches the API."""
    with TestClient(_app()) as client:
        yield client


def _org_id(client: TestClient) -> str:
    return client.get("/v1/console/organization").json()["id"]


def _create(client: TestClient, label: str = "backend") -> tuple[dict, str]:
    response = client.post("/v1/console/api-keys", headers=HEADERS, json={"label": label})
    assert response.status_code == 201, response.text
    body = response.json()
    return body, body.pop("key")


def _key(raw: str) -> dict[str, str]:
    return {"x-api-key": raw}


def _join(client: TestClient, org_id: str, role: str) -> None:
    """Make the signed-in user of `client` a `role` member of `org_id`, and switch to it."""
    with DBSession(engine) as db:
        email = "bob@example.com"
        identity.add_membership(
            db, org=db.get(Org, org_id), user=identity.find_user(db, email), role=role
        )
        db.commit()
    client.post(
        "/v1/console/session/organization", headers=HEADERS, json={"organization_id": org_id}
    )


# ══ lifecycle ═════════════════════════════════════════════════════════════════


def test_a_created_key_is_shown_once_stored_as_a_hash_and_authenticates(ada, anonymous):
    response = ada.post("/v1/console/api-keys", headers=HEADERS, json={"label": " billing "})
    assert response.status_code == 201
    assert response.headers["cache-control"] == "no-store"
    body = response.json()
    raw = body["key"]
    assert set(body) == {"id", "label", "created_at", "last_used_at", "key"}
    assert (body["label"], body["last_used_at"]) == ("billing", None)
    assert re.fullmatch(r"vf_[A-Za-z0-9_-]{43}", raw)

    # One M11 row, holding only the SHA-256 — no column holds the key or any part of it.
    with DBSession(engine) as db:
        row = db.get(ApiKey, body["id"])
        assert row is not None and row.org_id == _org_id(ada)
        assert row.key_hash == hashlib.sha256(raw.encode()).hexdigest()
        assert all(raw[3:12] not in str(value) for value in row.model_dump().values())

    assert anonymous.get("/v1/agents", headers=_key(raw)).status_code == 200


def test_the_list_never_contains_the_key_or_its_hash_and_shows_last_use(ada, anonymous):
    first, raw = _create(ada, "first")
    second, _ = _create(ada, "second")
    anonymous.get("/v1/agents", headers=_key(raw))

    listed = ada.get("/v1/console/api-keys")
    assert listed.status_code == 200
    data = listed.json()["data"]
    assert [k["id"] for k in data] == [second["id"], first["id"]]  # newest first
    assert all(set(k) == {"id", "label", "created_at", "last_used_at"} for k in data)
    assert data[1]["last_used_at"] is not None and data[0]["last_used_at"] is None
    with DBSession(engine) as db:
        stored_hash = db.get(ApiKey, first["id"]).key_hash
    assert raw not in listed.text and stored_hash not in listed.text


def test_revocation_is_immediate_on_the_server(ada, anonymous):
    created, raw = _create(ada)
    assert anonymous.get("/v1/agents", headers=_key(raw)).status_code == 200

    assert (
        ada.post(f"/v1/console/api-keys/{created['id']}/revoke", headers=HEADERS).status_code == 204
    )

    rejected = anonymous.get("/v1/agents", headers=_key(raw))
    assert (rejected.status_code, rejected.json()) == (401, {"detail": "Invalid API key"})
    assert ada.get("/v1/console/api-keys").json()["data"] == []
    # Revoking again — or revoking something that never existed — is the same 404.
    again = ada.post(f"/v1/console/api-keys/{created['id']}/revoke", headers=HEADERS)
    missing = ada.post("/v1/console/api-keys/key_nope/revoke", headers=HEADERS)
    assert again.status_code == missing.status_code == 404
    assert again.json()["error"]["message"] == missing.json()["error"]["message"]


def test_missing_and_malformed_keys_are_refused(anonymous):
    assert anonymous.get("/v1/agents").json() == {"detail": "Missing x-api-key"}
    for bad in ("vf_" + "A" * 43, "not-a-key", "key_0123456789abcdef01234567"):
        response = anonymous.get("/v1/agents", headers=_key(bad))
        assert (response.status_code, response.json()) == (401, {"detail": "Invalid API key"})


@pytest.mark.parametrize("label", ["", "   ", "x" * 101])
def test_a_label_is_required_and_bounded(ada, label):
    response = ada.post("/v1/console/api-keys", headers=HEADERS, json={"label": label})
    assert response.status_code == 422
    assert ada.get("/v1/console/api-keys").json()["data"] == []


# ══ roles and CSRF ════════════════════════════════════════════════════════════


@pytest.mark.parametrize("role,allowed", [("viewer", False), ("developer", False), ("admin", True)])
def test_only_owners_and_admins_manage_keys(ada, bob, role, allowed):
    existing, raw = _create(ada)  # ada is the owner
    _join(bob, _org_id(ada), role)

    listed = bob.get("/v1/console/api-keys")
    created = bob.post("/v1/console/api-keys", headers=HEADERS, json={"label": "x"})
    revoked = bob.post(f"/v1/console/api-keys/{existing['id']}/revoke", headers=HEADERS)
    expected = (200, 201, 204) if allowed else (403, 403, 403)
    assert (listed.status_code, created.status_code, revoked.status_code) == expected
    if not allowed:
        assert raw not in listed.text + created.text + revoked.text
        assert len(ada.get("/v1/console/api-keys").json()["data"]) == 1


def test_mutations_need_the_console_csrf_header(ada):
    created, _ = _create(ada)
    assert ada.post("/v1/console/api-keys", json={"label": "x"}).status_code == 403
    assert ada.post(f"/v1/console/api-keys/{created['id']}/revoke").status_code == 403
    assert len(ada.get("/v1/console/api-keys").json()["data"]) == 1


def test_key_management_needs_a_console_session(anonymous, ada):
    _, raw = _create(ada)
    for method, path in (("GET", ""), ("POST", ""), ("POST", "/key_x/revoke")):
        for headers in (HEADERS, {**HEADERS, **_key(raw)}):  # an API key is not a console session
            response = anonymous.request(method, f"/v1/console/api-keys{path}", headers=headers)
            assert response.status_code == 401
            assert response.json()["error"]["type"] == "unauthenticated"


# ══ tenancy ═══════════════════════════════════════════════════════════════════


def test_keys_are_scoped_to_their_organization(ada, bob, anonymous):
    a_key, a_raw = _create(ada)
    b_key, b_raw = _create(bob)
    assert [k["id"] for k in ada.get("/v1/console/api-keys").json()["data"]] == [a_key["id"]]

    theirs = ada.post(f"/v1/console/api-keys/{b_key['id']}/revoke", headers=HEADERS)
    assert theirs.status_code == 404  # indistinguishable from a missing key
    assert anonymous.get("/v1/agents", headers=_key(b_raw)).status_code == 200

    a_agent = anonymous.post(
        "/v1/agents", headers=_key(a_raw), json={"name": "A", "system_prompt": "a"}
    ).json()["id"]
    b_agent = anonymous.post(
        "/v1/agents", headers=_key(b_raw), json={"name": "B", "system_prompt": "b"}
    ).json()["id"]
    b_session = anonymous.post(
        "/v1/sessions", headers=_key(b_raw), json={"agent_id": b_agent}
    ).json()["session_id"]

    assert [a["id"] for a in anonymous.get("/v1/agents", headers=_key(a_raw)).json()] == [a_agent]
    for method, path, body in (
        ("GET", f"/v1/agents/{b_agent}", None),
        ("PATCH", f"/v1/agents/{b_agent}", {"name": "x"}),
        ("DELETE", f"/v1/agents/{b_agent}", None),
        ("POST", "/v1/sessions", {"agent_id": b_agent}),
        ("GET", f"/v1/sessions/{b_session}", None),
        ("GET", f"/v1/sessions/{b_session}/transcript", None),
        ("POST", f"/v1/sessions/{b_session}/end", None),
    ):
        response = anonymous.request(method, path, headers=_key(a_raw), json=body)
        assert response.status_code == 404, (method, path)
    # And the other way round.
    assert anonymous.get(f"/v1/agents/{a_agent}", headers=_key(b_raw)).status_code == 404
    assert (
        anonymous.get(f"/v1/sessions/{b_session}", headers=_key(b_raw)).json()["status"] == "active"
    )
    anonymous.post(f"/v1/sessions/{b_session}/end", headers=_key(b_raw))


def test_the_console_cookie_does_not_open_the_developer_api(ada):
    _create(ada)
    assert ada.get("/v1/agents").status_code == 401  # signed in, but no x-api-key


# ══ leaks ═════════════════════════════════════════════════════════════════════


def test_the_key_never_reaches_logs_errors_or_other_responses(ada, anonymous, caplog):
    caplog.set_level(logging.DEBUG)
    created, raw = _create(ada)
    responses = [
        ada.get("/v1/console/api-keys"),
        ada.post("/v1/console/api-keys", headers=HEADERS, json={"label": "another"}),
        anonymous.get("/v1/agents", headers=_key(raw)),
        anonymous.get("/v1/agents/agt_nope", headers=_key(raw)),
        anonymous.post("/v1/agents", headers=_key(raw), json={"name": raw}),  # 422 echoes input
        ada.post(f"/v1/console/api-keys/{created['id']}/revoke", headers=HEADERS),
        anonymous.get("/v1/agents", headers=_key(raw)),  # revoked: 401
        ada.get("/v1/console/api-keys"),
    ]
    # The 422 echoes the *body* the caller sent (FastAPI); nothing echoes the header credential.
    assert all(raw not in r.text for i, r in enumerate(responses) if i != 4)
    assert raw not in caplog.text
    assert f"api key created id={created['id']}" in caplog.text
    assert f"api key revoked id={created['id']}" in caplog.text


# ══ the documented contract ═══════════════════════════════════════════════════


def _spec(**webrtc) -> dict:
    with TestClient(_app(**webrtc)) as client:
        return client.get("/openapi.json").json()


def _operations(spec: dict) -> dict[tuple[str, str], dict]:
    return {
        (method.upper(), path): op
        for path, ops in spec["paths"].items()
        for method, op in ops.items()
    }


def test_openapi_states_each_operations_credential():
    spec = _spec(signalling=True)
    schemes = spec["components"]["securitySchemes"]
    assert schemes["ApiKeyAuth"] == {
        **schemes["ApiKeyAuth"],
        "type": "apiKey",
        "in": "header",
        "name": "x-api-key",
    }
    assert schemes["SignallingToken"]["name"] == "x-signalling-token"
    assert (schemes["ConsoleSession"]["in"], schemes["ConsoleSession"]["name"]) == (
        "cookie",
        "vv_console",
    )

    ops = _operations(spec)
    assert {op[SURFACE] for op in ops.values()} == {"developer", "signalling", "console", "public"}
    for (method, path), op in ops.items():
        expected = {
            "developer": [{"ApiKeyAuth": []}],
            "signalling": [{"SignallingToken": []}],
            "console": [{"ConsoleSession": []}],
            "public": [],
        }[op[SURFACE]]
        assert op["security"] == expected, (method, path)
        # Credentials are declared once, as schemes — not again as ordinary parameters.
        names = {p["name"] for p in op.get("parameters", []) if p["in"] == "header"}
        assert not names & {"x-api-key", "x-signalling-token"}, (method, path)
        # Nothing on /v1/console is developer-callable, and nothing outside it is cookie-only.
        assert (op[SURFACE] == "console") == (
            path.startswith("/v1/console/")
            and (method, path)
            not in {
                ("GET", "/v1/console/meta"),
                ("POST", "/v1/console/auth/signup"),
                ("POST", "/v1/console/auth/login"),
            }
        )
    assert ops[("GET", "/v1/agents")][SURFACE] == "developer"
    assert ops[("GET", "/v1/webrtc/rendezvous/{rendezvous_id}/offer")][SURFACE] == "signalling"
    assert ops[("GET", "/v1/console/api-keys")][SURFACE] == "console"
    assert ops[("GET", "/health")][SURFACE] == "public"


def _developer_operations() -> set[tuple[str, str]]:
    ops = _operations(_spec(signalling=True))
    return {k for k, op in ops.items() if op[SURFACE] in ("developer", "signalling")}


def _documented(text: str) -> set[tuple[str, str]]:
    return set(re.findall(r"\|\s*`(GET|POST|PATCH|DELETE)`\s*\|\s*`(/v1/[^`]+)`", text))


def test_the_contract_document_lists_exactly_the_developer_operations():
    assert _documented(CONTRACT.read_text(encoding="utf-8")) == _developer_operations()


def test_the_console_docs_page_names_only_real_developer_operations():
    named = set(re.findall(r'"(GET|POST|PATCH|DELETE) (/v1/[^"]+)"', DOCS_PAGE.read_text("utf-8")))
    assert named, "the docs page names no endpoints"
    assert named <= _developer_operations()


def test_the_contract_document_holds_no_credentials():
    text = CONTRACT.read_text(encoding="utf-8") + DOCS_PAGE.read_text(encoding="utf-8")
    assert not re.search(r"vf_[A-Za-z0-9_-]{20,}", text)
    assert "<YOUR_API_KEY>" in text


# ══ one developer journey, end to end ═════════════════════════════════════════


def test_a_developer_journey_from_key_to_revocation(ada, bob, anonymous):
    # 1–3. An owner creates a key and receives it once.
    created, raw = _create(ada, "journey")
    with DBSession(engine) as db:
        assert db.exec(select(ApiKey).where(ApiKey.id == created["id"])).one().key_hash != raw
    # 4–5. The developer API accepts it: create, list, get an agent; start and read a session.
    agent = anonymous.post(
        "/v1/agents",
        headers=_key(raw),
        json={"name": "Support", "system_prompt": "Be brief.", "language": "en"},
    )
    assert agent.status_code == 201
    agent_id = agent.json()["id"]
    assert [a["id"] for a in anonymous.get("/v1/agents", headers=_key(raw)).json()] == [agent_id]
    assert anonymous.get(f"/v1/agents/{agent_id}", headers=_key(raw)).json()["name"] == "Support"
    session = anonymous.post("/v1/sessions", headers=_key(raw), json={"agent_id": agent_id})
    assert session.status_code == 201
    session_id = session.json()["session_id"]
    assert (
        anonymous.get(f"/v1/sessions/{session_id}", headers=_key(raw)).json()["status"] == "active"
    )
    # The session is the same record the console shows.
    assert ada.get(f"/v1/console/conversations/{session_id}").json()["source"] == "api"
    # 6–7. Another organization's agent is invisible to it.
    foreign = _agent_of(bob)
    assert anonymous.get(f"/v1/agents/{foreign}", headers=_key(raw)).status_code == 404
    ended = anonymous.post(f"/v1/sessions/{session_id}/end", headers=_key(raw))
    assert ended.json() == {"session_id": session_id, "status": "ended"}
    # 8–9. Revoked: the same key no longer works.
    assert (
        ada.post(f"/v1/console/api-keys/{created['id']}/revoke", headers=HEADERS).status_code == 204
    )
    assert anonymous.get(f"/v1/agents/{agent_id}", headers=_key(raw)).status_code == 401


def _agent_of(client: TestClient) -> str:
    return client.post(
        "/v1/console/agents", headers=HEADERS, json={"name": "Theirs", "system_prompt": "x"}
    ).json()["id"]
