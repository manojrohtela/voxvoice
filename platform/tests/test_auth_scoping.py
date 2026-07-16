"""Auth + org-scoping (SPEC §4, §9). A key from org A must never see org B."""

from __future__ import annotations


def _h(key: str) -> dict[str, str]:
    return {"x-api-key": key}


def test_missing_key_is_401(client):
    assert client.get("/v1/agents").status_code == 401


def test_invalid_key_is_401(client):
    assert client.get("/v1/agents", headers=_h("vf_nope")).status_code == 401


def test_cross_org_agent_access_is_404(client, org_a, org_b):
    _, key_a = org_a
    _, key_b = org_b

    created = client.post(
        "/v1/agents",
        headers=_h(key_a),
        json={"name": "A's agent", "system_prompt": "hi"},
    ).json()
    agent_id = created["id"]

    # Org B cannot read, patch, or delete org A's agent.
    assert client.get(f"/v1/agents/{agent_id}", headers=_h(key_b)).status_code == 404
    assert (
        client.patch(
            f"/v1/agents/{agent_id}", headers=_h(key_b), json={"name": "x"}
        ).status_code
        == 404
    )
    assert client.delete(f"/v1/agents/{agent_id}", headers=_h(key_b)).status_code == 404

    # Org B's list does not contain org A's agent.
    b_ids = [a["id"] for a in client.get("/v1/agents", headers=_h(key_b)).json()]
    assert agent_id not in b_ids

    # Org A still sees it.
    assert client.get(f"/v1/agents/{agent_id}", headers=_h(key_a)).status_code == 200
