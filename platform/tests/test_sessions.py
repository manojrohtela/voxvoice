"""Sessions endpoints (SPEC §4; M2C.2 — Control Plane restored).

`POST /v1/sessions` provisions a real Runtime session through the injected
SessionController (local provider profile). Org-scoping is enforced on every
operation (cross-org / unknown → 404).
"""

from __future__ import annotations


def _h(key: str) -> dict[str, str]:
    return {"x-api-key": key}


def _make_agent(client, key: str) -> str:
    return client.post(
        "/v1/agents",
        headers=_h(key),
        json={"name": "Bot", "system_prompt": "be helpful"},
    ).json()["id"]


def test_session_start_provisions_runtime_and_ends(client, org_a):
    _, key = org_a
    h = _h(key)
    agent_id = _make_agent(client, key)

    resp = client.post(
        "/v1/sessions", headers=h, json={"agent_id": agent_id, "metadata": {"k": "v"}}
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["session_id"].startswith("ses_")
    assert body["engine"] == "voxvoice"
    session_id = body["session_id"]

    # The session is active (a real runtime conversation is running over the transport).
    detail = client.get(f"/v1/sessions/{session_id}", headers=h).json()
    assert detail["status"] == "active"
    assert detail["metadata"] == {"k": "v"}

    # Ending it tears down the runtime session cleanly.
    ended = client.post(f"/v1/sessions/{session_id}/end", headers=h).json()
    assert ended == {"session_id": session_id, "status": "ended"}
    assert client.get(f"/v1/sessions/{session_id}", headers=h).json()["status"] == "ended"


def test_session_start_cross_org_agent_is_404(client, org_a, org_b):
    _, key_a = org_a
    _, key_b = org_b
    agent_id = _make_agent(client, key_a)
    resp = client.post("/v1/sessions", headers=_h(key_b), json={"agent_id": agent_id})
    assert resp.status_code == 404


def test_session_start_unknown_agent_is_404(client, org_a):
    _, key = org_a
    resp = client.post("/v1/sessions", headers=_h(key), json={"agent_id": "agt_missing"})
    assert resp.status_code == 404


def test_get_and_end_unknown_session_is_404(client, org_a):
    _, key = org_a
    assert client.get("/v1/sessions/ses_missing", headers=_h(key)).status_code == 404
    assert client.post("/v1/sessions/ses_missing/end", headers=_h(key)).status_code == 404
    assert client.get("/v1/sessions/ses_missing/transcript", headers=_h(key)).status_code == 404


def test_session_start_runtime_failure_is_502(client, org_a, monkeypatch):
    """A runtime startup failure surfaces as a clean 502 (no raw error leaked),
    and the persisted Session is marked failed."""
    from runtime.orchestrator.orchestrator import ConversationOrchestrator

    async def boom(self) -> None:
        raise RuntimeError("provider open boom")

    monkeypatch.setattr(ConversationOrchestrator, "_open_providers", boom)
    _, key = org_a
    agent_id = _make_agent(client, key)
    resp = client.post("/v1/sessions", headers=_h(key), json={"agent_id": agent_id})
    assert resp.status_code == 502
