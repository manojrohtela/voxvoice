"""Sessions endpoints at M1 (SPEC §4; Constitution migration M1).

The engine-adapter axis was removed (ADR-0001/0007), so live session *start* is
unavailable until the Runtime Plane lands at M2 and returns 503 — after the
org-scoping ownership check, which is preserved. Read/end endpoints operate on
persisted sessions and enforce org-scoping (cross-org -> 404).
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


def test_session_start_returns_503_until_runtime(client, org_a):
    """Owned agent: start is a deliberate, honest 503 until M2 (not a 5xx crash)."""
    _, key = org_a
    agent_id = _make_agent(client, key)
    resp = client.post("/v1/sessions", headers=_h(key), json={"agent_id": agent_id})
    assert resp.status_code == 503
    assert "Runtime Plane" in resp.json()["detail"]


def test_session_start_cross_org_agent_is_404(client, org_a, org_b):
    """Org-scoping is checked before the 503: org B cannot even probe org A's agent."""
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
    assert (
        client.get("/v1/sessions/ses_missing/transcript", headers=_h(key)).status_code == 404
    )
