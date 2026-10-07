"""M12.13 — the starter agent templates, against the real server and the real runtime.

The definitions live with the console (`console/src/features/agents/templates.json`) because a
template is product data, not a runtime concept: there is no template table, no template endpoint
and no template runtime. This suite reads that same file and proves the part the console cannot
prove on its own —

* every template's starting values are accepted by the **real** agent validation, not by a fake;
* each one produces an **ordinary agent**: it lists, it reads back, it edits, it archives;
* each one completes a **real WebRTC call** through the frozen Playground path and appears in
  Conversations, Analytics and the Overview exactly like any other agent;
* a template is **immutable** — two agents made from one template are independent of each other
  and of the file on disk;
* templates are global, but the agents made from them are organization-owned and isolated;
* a `viewer` cannot create one, because creating from a template is just creating an agent.

The providers are the deterministic ones (M12.4's stack), so this is evidence about the product
and the media path, **not** about real speech.
"""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from . import test_m12_e2e as e2e
from .test_m12_e2e import HEADERS, _call, _product, _signup

#: The console tables are wiped around every test, exactly as the M12.10 journey does it. Bound as
#: a module attribute so pytest registers the imported autouse fixture in this module too.
_clean_console = e2e._clean_console

TEMPLATES_FILE = (
    Path(__file__).resolve().parents[2]
    / "console"
    / "src"
    / "features"
    / "agents"
    / "templates.json"
)


def _load() -> list[dict]:
    data = json.loads(TEMPLATES_FILE.read_text())
    assert data["schema_version"] == 1
    return data["templates"]


TEMPLATES = _load()
IDS = [template["id"] for template in TEMPLATES]


async def _create_from_template(client: httpx.AsyncClient, template: dict) -> dict:
    """Exactly what the console's "Use this template" sends: an ordinary agent create."""
    options = (await client.get("/v1/console/agents/options")).json()
    language = template["agent"]["language"]
    response = await client.post(
        "/v1/console/agents",
        headers=HEADERS,
        json={
            "name": template["agent"]["name"],
            "system_prompt": template["agent"]["system_prompt"],
            "first_message": template["agent"]["first_message"],
            "language": language
            if language in options["languages"]
            else options["default_language"],
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


# ══ the definitions ═══════════════════════════════════════════════════════════


def test_the_file_defines_the_eight_starter_templates() -> None:
    assert IDS == [
        "sales_representative",
        "interviewer",
        "customer_support",
        "receptionist",
        "lead_qualifier",
        "appointment_assistant",
        "faq_assistant",
        "voxvoice_demo",
    ]
    assert len({t["id"] for t in TEMPLATES}) == len(TEMPLATES)
    assert len({t["name"] for t in TEMPLATES}) == len(TEMPLATES)
    assert len({t["agent"]["name"] for t in TEMPLATES}) == len(TEMPLATES)


@pytest.mark.parametrize("template", TEMPLATES, ids=IDS)
def test_every_template_is_complete(template: dict) -> None:
    for field in ("id", "name", "category", "short_description", "purpose", "recommended_use"):
        assert template[field].strip(), field
    assert template["version"] >= 1
    assert len(template["behaviour"]) >= 3
    agent = template["agent"]
    assert agent["system_prompt"].strip()
    assert agent["first_message"].strip()
    assert agent["language"] == "en"
    # Model and voice are never pinned: what a provider account offers is deployment-specific, so
    # an agent made from a template takes the server default and the editor can change it.
    assert "llm_model" not in agent
    assert "tts_voice" not in agent


def test_no_template_carries_a_credential() -> None:
    """The definitions are shipped to every authenticated browser; they hold no secret."""
    text = TEMPLATES_FILE.read_text()
    for pattern in ("sk-", "vf_", "Bearer ", "api_key=", "VOXVOICE_SECRET"):
        assert pattern not in text, pattern


# ══ the agent each one creates ════════════════════════════════════════════════


@pytest.mark.parametrize("template", TEMPLATES, ids=IDS)
async def test_a_template_creates_an_ordinary_editable_agent(template: dict) -> None:
    async with _product() as (_app, client):
        await _signup(client, "ada@example.com", "Org A")
        agent = await _create_from_template(client, template)

        # The values arrived verbatim, and the recognizer can hear it.
        assert agent["name"] == template["agent"]["name"]
        assert agent["system_prompt"] == template["agent"]["system_prompt"]
        assert agent["first_message"] == template["agent"]["first_message"]
        assert agent["language_supported"] is True
        assert agent["llm_model"] is None and agent["tts_voice"] is None

        # An ordinary agent: it lists, it reads back, it edits, it archives.
        listed = (await client.get("/v1/console/agents")).json()["data"]
        assert [a["id"] for a in listed] == [agent["id"]]
        assert (await client.get(f"/v1/console/agents/{agent['id']}")).json() == agent

        edited = await client.patch(
            f"/v1/console/agents/{agent['id']}", headers=HEADERS, json={"name": "Renamed"}
        )
        assert edited.status_code == 200
        assert edited.json()["name"] == "Renamed"
        assert edited.json()["system_prompt"] == template["agent"]["system_prompt"]

        archived = await client.post(
            f"/v1/console/agents/{agent['id']}/archive", headers=HEADERS
        )
        assert archived.status_code == 204
        assert (await client.get("/v1/console/agents")).json()["data"] == []


@pytest.mark.parametrize("template", TEMPLATES, ids=IDS)
async def test_every_template_completes_a_real_call_and_is_counted(template: dict) -> None:
    """Agent → Playground → WebRTC → Conversation → Analytics → Overview, per template."""
    async with _product() as (app, client):
        await _signup(client, "ada@example.com", "Org A")
        agent = await _create_from_template(client, template)

        session_id = await _call(app, client, agent["id"])

        detail = (await client.get(f"/v1/console/conversations/{session_id}")).json()
        assert detail["status"] == "ended"
        assert detail["source"] == "console_playground"
        assert detail["agent"]["id"] == agent["id"]
        assert detail["agent"]["name"] == template["agent"]["name"]
        assert detail["events_recorded"] is True
        assert detail["duration_s"] is not None
        assert detail["timeline"][-1]["label"] == "Session ended"

        summary = (await client.get("/v1/console/analytics/summary?range=today")).json()
        assert summary["conversations"]["total"] == 1
        assert summary["conversations"]["by_source"] == {"playground": 1, "api": 0}

        overview = (await client.get("/v1/console/overview?range=today")).json()
        assert overview["conversations"] == summary["conversations"]
        assert [a["name"] for a in overview["agent_activity"]] == [template["agent"]["name"]]


# ══ immutability, isolation, role ═════════════════════════════════════════════


async def test_two_agents_from_one_template_are_independent_and_the_template_is_untouched() -> None:
    template = TEMPLATES[0]
    before = TEMPLATES_FILE.read_bytes()
    async with _product() as (_app, client):
        await _signup(client, "ada@example.com", "Org A")
        first = await _create_from_template(client, template)
        second = await _create_from_template(client, template)
        assert first["id"] != second["id"]
        expected = template["agent"]["system_prompt"]
        assert first["system_prompt"] == second["system_prompt"] == expected

        await client.patch(
            f"/v1/console/agents/{first['id']}",
            headers=HEADERS,
            json={"name": "Only the first", "system_prompt": "Replaced entirely."},
        )

        # The sibling is untouched …
        unchanged = (await client.get(f"/v1/console/agents/{second['id']}")).json()
        assert unchanged["name"] == template["agent"]["name"]
        assert unchanged["system_prompt"] == template["agent"]["system_prompt"]
        # … and a third agent still starts from the template's own values.
        third = await _create_from_template(client, template)
        assert third["system_prompt"] == template["agent"]["system_prompt"]

    # The template is a file, and nothing in the product writes to it.
    assert TEMPLATES_FILE.read_bytes() == before


async def test_templates_are_global_but_the_agents_they_create_are_not() -> None:
    template = TEMPLATES[0]
    async with _product() as (_app, client):
        await _signup(client, "ada@example.com", "Org A")
        a_options = (await client.get("/v1/console/agents/options")).json()
        agent_a = await _create_from_template(client, template)
        await client.post("/v1/console/auth/logout", headers=HEADERS)

        await _signup(client, "grace@example.com", "Org B")
        # The same templates are offered — they are product data, not organization data.
        assert (await client.get("/v1/console/agents/options")).json() == a_options
        agent_b = await _create_from_template(client, template)

        assert agent_b["id"] != agent_a["id"]
        assert [a["id"] for a in (await client.get("/v1/console/agents")).json()["data"]] == [
            agent_b["id"]
        ]
        # Org A's agent is not merely hidden from B: it is reported as missing (S4).
        assert (await client.get(f"/v1/console/agents/{agent_a['id']}")).status_code == 404
        assert (
            await client.patch(
                f"/v1/console/agents/{agent_a['id']}", headers=HEADERS, json={"name": "theirs"}
            )
        ).status_code == 404


async def test_a_viewer_cannot_create_an_agent_from_a_template() -> None:
    """Creating from a template *is* creating an agent, so it meets the same role rule."""
    from sqlmodel import Session as DBSession

    from api.console.identity import service as identity
    from api.db import engine
    from api.models import Org

    async with _product() as (_app, client):
        await _signup(client, "ada@example.com", "Org A")
        org_id = (await client.get("/v1/console/me")).json()["organization"]["id"]
        with DBSession(engine) as db:
            org = db.get(Org, org_id)
            assert org is not None
            viewer = identity.create_user(
                db, email="val@example.com", password="correct horse battery"
            )
            identity.add_membership(db, org=org, user=viewer, role="viewer")
            db.commit()
        await client.post("/v1/console/auth/logout", headers=HEADERS)
        signed_in = await client.post(
            "/v1/console/auth/login",
            headers=HEADERS,
            json={"email": "val@example.com", "password": "correct horse battery"},
        )
        assert signed_in.status_code == 200

        refused = await client.post(
            "/v1/console/agents",
            headers=HEADERS,
            json={
                "name": TEMPLATES[0]["agent"]["name"],
                "system_prompt": TEMPLATES[0]["agent"]["system_prompt"],
            },
        )
        assert refused.status_code == 403
        assert (await client.get("/v1/console/agents")).json()["data"] == []
