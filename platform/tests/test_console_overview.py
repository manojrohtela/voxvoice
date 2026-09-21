"""M12.8 — the Overview: a composition of Agents, Conversations and Analytics, not a new source.

What is proven here:

* **consistency** (the reason this file exists): for the same query, every figure the Overview
  shows is *identical* to what `/v1/console/analytics/*` and `/v1/console/conversations` return —
  summary, daily usage, latency and agent activity, over a population built to be awkward
  (archived agent, several sources and days, unmeasured latency, unfinished and unrecorded
  conversations);
* the agent inventory (active / archived / unsupported language) and the deterministic attention
  items;
* the recent-conversation and agent-activity limits;
* an empty organization, tenant isolation, forged organization ids, roles, sign-in, and a refused
  `source` filter (no panel may silently differ from another).
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session as DBSession

from api.console import ConsoleSettings, build_console_app
from api.console.identity import service as identity
from api.db import engine
from api.models import Agent, Org
from api.webrtc.config import WebRTCSettings
from tests import test_console_analytics as fx
from tests.test_console_analytics import HEADERS, _agent, _at, _org_id, _recorded, _session, _turn

# The M12.6 fixtures (a signed-in owner per organization, a clean console, a pinned "today").
_no_console_accounts = fx._no_console_accounts
ada = fx.ada
bob = fx.bob


def _overview(client: TestClient, **params) -> dict:
    response = client.get("/v1/console/overview", params=params)
    assert response.status_code == 200, response.text
    return response.json()


def _analytics(client: TestClient, endpoint: str, **params) -> dict:
    response = client.get(f"/v1/console/analytics/{endpoint}", params=params)
    assert response.status_code == 200, response.text
    return response.json()


def _populate(client: TestClient) -> dict[str, str]:
    """An awkward population: two live agents and one archived, playground and API sources,
    several days, measured and unmeasured latency, an active and an unrecorded conversation."""
    org = _org_id(client)
    support, sales, old = _agent(client, "Support"), _agent(client, "Sales"), _agent(client, "Old")
    a = _session(org, support, started=_at(21, 9), seconds=65, source="console_playground")
    _recorded(a, org, _turn(820, llm=300, tts=90), _turn(None, "interrupted", llm=250), noise=2)
    b = _session(org, sales, started=_at(20, 23, 59), seconds=240)  # runs past midnight
    _recorded(b, org, _turn(1100, stt=40), _turn(0, endpoint=0))
    c = _session(org, old, started=_at(16), seconds=30)
    _recorded(c, org, _turn(700))
    _session(org, sales, started=_at(21, 10), status="active")  # no end yet
    _session(org, support, started=_at(19), status="failed", seconds=None)  # never recorded
    _session(org, support, started=datetime(2026, 8, 30, 12, tzinfo=UTC))  # outside 7d, inside 30d
    assert client.post(f"/v1/console/agents/{old}/archive", headers=HEADERS).status_code == 204
    return {"support": support, "sales": sales, "old": old}


# ══ consistency with Analytics ════════════════════════════════════════════════


@pytest.mark.parametrize(
    "params",
    [{}, {"range": "today"}, {"range": "30d"}, {"from": "2026-09-16", "to": "2026-09-20"}],
)
def test_every_figure_is_exactly_what_analytics_reports(ada, params):
    agents = _populate(ada)
    for query in (
        params,
        {**params, "agent_id": agents["support"]},
        {**params, "agent_id": agents["old"]},
    ):
        overview = _overview(ada, **query)
        summary = _analytics(ada, "summary", **query)
        assert overview["query"] == summary["query"]
        for key in ("conversations", "duration", "turns"):
            assert overview[key] == summary[key], (query, key)
        assert overview["usage"] == _analytics(ada, "timeseries", **query)["data"]
        assert overview["latency"] == _analytics(ada, "latency", **query)["data"]
        assert overview["agent_activity"] == _analytics(ada, "agents", **query)["data"][:5]


def test_recent_conversations_are_the_conversation_lists_first_page(ada):
    agents = _populate(ada)
    overview = _overview(ada, range="today")
    listed = ada.get("/v1/console/conversations", params={"limit": 5}).json()["data"]
    assert overview["recent_conversations"] == listed
    # Of any date — labelled as such — and newest first; the range does not hide them.
    assert len(listed) == 5 and overview["conversations"]["total"] == 2
    filtered = _overview(ada, agent_id=agents["sales"])["recent_conversations"]
    assert {c["agent"]["name"] for c in filtered} == {"Sales"}
    assert (
        filtered
        == ada.get(
            "/v1/console/conversations", params={"agent_id": agents["sales"], "limit": 5}
        ).json()["data"]
    )


def test_missing_stays_missing_and_zero_stays_zero(ada):
    org = _org_id(ada)
    session = _session(ada_org := org, _agent(ada), started=_at(21, 8), status="active")
    _recorded(session, ada_org, _turn(None), _turn(None))
    overview = _overview(ada)
    assert overview["duration"] == {"measured": 0, "unmeasured": 1, "total_s": None, "avg_s": None}
    assert overview["turns"]["total"] == 2
    v2v = overview["latency"][0]
    assert (v2v["metric"], v2v["samples"], v2v["missing"], v2v["p50_ms"]) == (
        "voice_to_voice",
        0,
        2,
        None,
    )
    assert [b["conversations"] for b in overview["usage"]] == [0, 0, 0, 0, 0, 0, 1]


# ══ inventory and attention ═══════════════════════════════════════════════════


def test_an_empty_organization_is_intentional(ada):
    overview = _overview(ada)
    assert overview["agents"] == {"active": 0, "archived": 0, "unsupported_language": 0}
    assert overview["conversations"]["total"] == 0
    assert (
        overview["duration"]["avg_s"] is None and overview["turns"]["avg_per_conversation"] is None
    )
    assert overview["recent_conversations"] == overview["agent_activity"] == []
    assert all(m["samples"] == 0 and m["p50_ms"] is None for m in overview["latency"])
    assert [a["code"] for a in overview["attention"]] == ["no_agents"]


def test_the_agent_inventory_and_attention_are_deterministic(ada):
    _populate(ada)
    with DBSession(engine) as db:  # an agent the developer API allowed with its frozen default
        db.add(Agent(org_id=_org_id(ada), name="Legacy", system_prompt="x", language="en-IN"))
        db.commit()
    overview = _overview(ada)
    assert overview["agents"] == {"active": 3, "archived": 1, "unsupported_language": 1}
    assert [a["code"] for a in overview["attention"]] == ["unsupported_language"]
    assert [
        a["code"] for a in _overview(ada, **{"from": "2026-01-01", "to": "2026-01-02"})["attention"]
    ] == [
        "unsupported_language",
        "no_conversations",
    ]
    # The inventory is the organization's now: neither the range nor the agent filter changes it.
    support = _overview(ada)["agent_activity"][0]["id"]
    assert _overview(ada, range="today", agent_id=support)["agents"] == overview["agents"]


def test_agent_activity_is_limited_and_counts_the_rest(ada):
    org = _org_id(ada)
    for n in range(7):
        agent = _agent(ada, f"A{n}")
        for _ in range(n + 1):
            _session(org, agent, started=_at(20))
    overview = _overview(ada)
    assert [a["name"] for a in overview["agent_activity"]] == ["A6", "A5", "A4", "A3", "A2"]
    assert overview["agent_activity_more"] == 2


def test_the_response_is_deterministic(ada):
    _populate(ada)
    assert _overview(ada, range="30d") == _overview(ada, range="30d")


# ══ security ══════════════════════════════════════════════════════════════════


def test_another_organization_sees_nothing_of_this_one(ada, bob):
    agents = _populate(ada)
    theirs = _overview(bob, range="30d")
    assert theirs["agents"] == {"active": 0, "archived": 0, "unsupported_language": 0}
    assert theirs["conversations"]["total"] == 0 and theirs["recent_conversations"] == []
    for params in ({"org_id": _org_id(ada)}, {"organization_id": _org_id(ada)}):
        assert _overview(bob, range="30d", **params) == theirs  # a forged org id changes nothing
    foreign = bob.get("/v1/console/overview", params={"agent_id": agents["support"]})
    missing = bob.get("/v1/console/overview", params={"agent_id": "agt_nope"})
    assert foreign.status_code == missing.status_code == 404
    assert foreign.json()["error"]["message"] == missing.json()["error"]["message"]


@pytest.mark.parametrize("role", ["viewer", "developer", "admin"])
def test_every_role_reads_the_overview_of_its_organization(ada, bob, role):
    _populate(ada)
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
    assert _overview(bob) == _overview(ada)


def test_the_overview_needs_a_console_session_and_takes_no_source_filter(ada):
    with TestClient(build_console_app(ConsoleSettings(), webrtc_settings=WebRTCSettings())) as c:
        response = c.get("/v1/console/overview")
        assert (response.status_code, response.json()["error"]["type"]) == (401, "unauthenticated")
    refused = ada.get("/v1/console/overview", params={"source": "api"})
    assert (refused.status_code, refused.json()["error"]["type"]) == (400, "invalid_request")
    assert ada.get("/v1/console/overview", params={"range": "year"}).status_code == 422


def test_the_overview_is_a_console_operation_and_carries_no_key_material(ada):
    ada.post("/v1/console/api-keys", headers=HEADERS, json={"label": "k"})
    spec = ada.get("/openapi.json").json()["paths"]["/v1/console/overview"]["get"]
    assert (spec["x-voxvoice-surface"], spec["security"]) == ("console", [{"ConsoleSession": []}])
    body = ada.get("/v1/console/overview").text
    assert "vf_" not in body and "key_hash" not in body
