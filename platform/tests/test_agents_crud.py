"""Agents CRUD (SPEC §4)."""

from __future__ import annotations


def _h(key: str) -> dict[str, str]:
    return {"x-api-key": key}


def test_agent_crud_roundtrip(client, org_a):
    _, key = org_a
    h = _h(key)

    # Create
    resp = client.post(
        "/v1/agents",
        headers=h,
        json={
            "name": "Reschedule bot",
            "system_prompt": "You help reschedule interviews.",
            "language": "hinglish",
            "stt_provider": "sarvam",
            "tts_provider": "sarvam",
            "metadata": {"team": "voxhire"},
        },
    )
    assert resp.status_code == 201
    agent = resp.json()
    assert agent["id"].startswith("agt_")
    assert agent["language"] == "hinglish"
    assert agent["metadata"] == {"team": "voxhire"}
    assert agent["allow_interruptions"] is True  # default applied
    agent_id = agent["id"]

    # Read
    assert client.get(f"/v1/agents/{agent_id}", headers=h).json()["name"] == "Reschedule bot"

    # List
    assert agent_id in [a["id"] for a in client.get("/v1/agents", headers=h).json()]

    # Patch (partial) — only name + metadata change.
    patched = client.patch(
        f"/v1/agents/{agent_id}",
        headers=h,
        json={"name": "Renamed", "metadata": {"team": "sales"}},
    ).json()
    assert patched["name"] == "Renamed"
    assert patched["metadata"] == {"team": "sales"}
    assert patched["system_prompt"] == "You help reschedule interviews."  # untouched

    # Delete
    assert client.delete(f"/v1/agents/{agent_id}", headers=h).json() == {"deleted": agent_id}
    assert client.get(f"/v1/agents/{agent_id}", headers=h).status_code == 404
