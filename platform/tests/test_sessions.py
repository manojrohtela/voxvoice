"""End-to-end session flow (SPEC §7 Phase 1 acceptance): create agent -> start
web session -> simulate engine transcript events -> read transcript -> end."""

from __future__ import annotations


def _h(key: str) -> dict[str, str]:
    return {"x-api-key": key}


def _make_agent(client, key: str) -> str:
    return client.post(
        "/v1/agents",
        headers=_h(key),
        json={"name": "Bot", "system_prompt": "be helpful"},
    ).json()["id"]


def test_full_session_flow(client, org_a):
    _, key = org_a
    h = _h(key)
    agent_id = _make_agent(client, key)

    # Start a web session.
    resp = client.post(
        "/v1/sessions", headers=h, json={"agent_id": agent_id, "metadata": {"k": "v"}}
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["session_id"].startswith("ses_")
    assert body["engine"] == "dograh"
    assert body["channel"] == "web"
    assert body["web"]["token"] == "fake-token"
    # Superset web contract: §4 fields present; Dograh extras optional (None here).
    assert set(body["web"]) >= {"token", "url", "room", "embed_script", "turn_url", "run_id"}
    session_id = body["session_id"]

    # Session is active and carries engine_ref + metadata.
    detail = client.get(f"/v1/sessions/{session_id}", headers=h).json()
    assert detail["status"] == "active"
    assert detail["metadata"] == {"k": "v"}
    engine_ref = detail["engine_ref"]
    assert engine_ref

    # Engine pushes transcript items back to us (simulating Dograh).
    for role, txt, ts in [("user", "hello", 0.0), ("agent", "hi there", 1.2)]:
        ev = client.post(
            "/v1/engine/dograh/events",
            headers={"x-engine-secret": "test-engine-secret"},
            json={"engine_ref": engine_ref, "role": role, "text": txt, "ts": ts},
        )
        assert ev.status_code == 202

    # Consumer reads the transcript, ordered by ts.
    transcript = client.get(f"/v1/sessions/{session_id}/transcript", headers=h).json()
    assert [(t["role"], t["text"]) for t in transcript] == [
        ("user", "hello"),
        ("agent", "hi there"),
    ]

    # End the session (idempotent).
    assert client.post(f"/v1/sessions/{session_id}/end", headers=h).json() == {
        "session_id": session_id,
        "status": "ended",
    }
    assert client.get(f"/v1/sessions/{session_id}", headers=h).json()["status"] == "ended"


def test_engine_event_requires_secret(client, org_a):
    _, key = org_a
    agent_id = _make_agent(client, key)
    session = client.post(
        "/v1/sessions", headers=_h(key), json={"agent_id": agent_id}
    ).json()
    engine_ref = client.get(
        f"/v1/sessions/{session['session_id']}", headers=_h(key)
    ).json()["engine_ref"]

    bad = client.post(
        "/v1/engine/dograh/events",
        headers={"x-engine-secret": "wrong"},
        json={"engine_ref": engine_ref, "role": "user", "text": "x"},
    )
    assert bad.status_code == 401


def test_session_for_other_orgs_agent_is_404(client, org_a, org_b):
    _, key_a = org_a
    _, key_b = org_b
    agent_id = _make_agent(client, key_a)
    # Org B cannot start a session against org A's agent.
    resp = client.post("/v1/sessions", headers=_h(key_b), json={"agent_id": agent_id})
    assert resp.status_code == 404
