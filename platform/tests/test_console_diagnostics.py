"""M12.14 — session diagnostics: the projection, its stream, and what it refuses to say.

The Playground's diagnostics are a **view of Epic 10's durable log**, so these tests prove the
three things a view must get right: it shows what the runtime actually recorded, it never shows
anything else, and it never shows it to the wrong person.

Nothing here invents an event. The live call is the deterministic M12.4 media stack, exactly as
the rest of M12 tests it.
"""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest
from sqlmodel import Session as DBSession

from api.console.diagnostics import service as diagnostics
from api.db import engine
from core.events import Event, EventSource, EventType

from . import test_m12_e2e as e2e
from .test_m12_e2e import HEADERS, _agent, _call, _product, _signup

_clean_console = e2e._clean_console

PASSWORD = "correct horse battery"


def _event(
    type_: EventType, *, seq: int = 1, ts: float = 1000.0, data: dict[str, Any] | None = None
) -> Event:
    return Event(
        id=f"evt_{seq}",
        type=type_,
        ts=ts,
        seq=seq,
        session_id="ses_1",
        source=EventSource.RUNTIME,
        org_id="org_1",
        data=data or {},
    )


async def _read_stream(client: httpx.AsyncClient, path: str) -> list[dict[str, Any]]:
    """Consume one SSE response to completion and return its decoded frames."""
    frames: list[dict[str, Any]] = []
    async with client.stream("GET", path) as response:
        assert response.status_code == 200, await response.aread()
        assert response.headers["content-type"].startswith("text/event-stream")
        name = ""
        async for line in response.aiter_lines():
            if line.startswith("event: "):
                name = line.removeprefix("event: ")
            elif line.startswith("data: "):
                frames.append({"event": name, **json.loads(line.removeprefix("data: "))})
    return frames


# ══ the projection ════════════════════════════════════════════════════════════


def test_only_the_session_vocabulary_is_shown() -> None:
    """A type with no place in a conversation's diagnostics is not rendered as 'unknown'."""
    assert diagnostics.project(_event(EventType.MEMORY_STORED), 0.0) is None
    assert diagnostics.project(_event(EventType.SESSION_STARTED), 1000.0) is not None


def test_each_event_carries_its_category_severity_and_offset() -> None:
    row = diagnostics.project(_event(EventType.RUNTIME_ERROR, seq=7, ts=1002.5), 1000.0)
    assert row is not None
    assert (row.category, row.severity, row.message) == ("RUNTIME", "error", "Runtime error")
    assert (row.seq, row.offset_s) == (7, 2.5)


def test_latency_reports_measured_stages_and_leaves_the_rest_absent() -> None:
    """E11-10 survives the projection: an unmeasured stage is missing, never 0."""
    row = diagnostics.project(
        _event(
            EventType.METRIC_TURN,
            data={"status": "completed", "voice_to_voice_ms": 1420, "ms": {"stt": 310, "llm": 710}},
        ),
        1000.0,
    )
    assert row is not None
    assert row.stages_ms == {"stt": 310, "llm": 710}
    assert "tts" not in row.stages_ms and "egress" not in row.stages_ms
    assert row.voice_to_voice_ms == 1420
    assert row.severity == "info"
    assert row.as_json()["stages_ms"] == {"stt": 310, "llm": 710}


def test_a_turn_the_runtime_could_not_complete_is_a_warning() -> None:
    row = diagnostics.project(
        _event(EventType.METRIC_TURN, data={"status": "failed", "ms": {}}), 1000.0
    )
    assert row is not None
    assert row.severity == "warn"
    assert row.detail["status"] == "failed"


# ── redaction ─────────────────────────────────────────────────────────────────


def test_only_allowlisted_fields_survive() -> None:
    row = diagnostics.project(
        _event(
            EventType.RUNTIME_ERROR,
            data={"reason": "stt_stream_failed", "prompt": "the caller said…", "sdp": "v=0…"},
        ),
        1000.0,
    )
    assert row is not None
    assert row.detail == {"reason": "stt_stream_failed"}


def test_a_secret_shaped_value_never_reaches_the_browser() -> None:
    """Belt and braces: the allowlist already excludes payloads that could carry one."""
    for secret in (
        "sk-abcdefghijklmnop",
        "vf_0123456789abcdef",
        "Bearer eyJhbGciOiJIUzI1NiJ9",
        "a" * 40,
    ):
        row = diagnostics.project(_event(EventType.RUNTIME_ERROR, data={"reason": secret}), 1000.0)
        assert row is not None
        assert row.detail == {}, secret


def test_the_provider_name_is_not_disclosed() -> None:
    """`capability` is the diagnostic fact; which vendor serves it is not the browser's business."""
    row = diagnostics.project(
        _event(EventType.PROVIDER_CONNECTED, data={"capability": "stt", "provider": "acme-speech"}),
        1000.0,
    )
    assert row is not None
    assert row.detail == {"capability": "stt"}
    assert "acme-speech" not in json.dumps(row.as_json())


def test_nested_and_non_scalar_payloads_are_dropped() -> None:
    row = diagnostics.project(
        _event(EventType.TRANSPORT_FAILED, data={"detail": {"ice": ["candidate"]}}), 1000.0
    )
    assert row is not None
    assert row.detail == {}


def test_a_long_value_is_truncated() -> None:
    row = diagnostics.project(
        _event(EventType.SESSION_ENDED, data={"reason": "x " * 400}), 1000.0
    )
    assert row is not None
    assert len(row.detail["reason"]) <= diagnostics.MAX_VALUE


# ── derived runtime state ─────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("types", "expected"),
    [
        ([], "CONNECTING"),
        ([EventType.SESSION_STARTED], "CONNECTING"),
        ([EventType.SESSION_STARTED, EventType.TRANSPORT_CONNECTED], "CONNECTED"),
        ([EventType.TRANSPORT_MEDIA_READY], "LISTENING"),
        ([EventType.TRANSPORT_MEDIA_READY, EventType.SPEECH_STARTED], "USER_SPEAKING"),
        ([EventType.SPEECH_STARTED, EventType.ENDPOINT_DETECTED], "PROCESSING"),
        ([EventType.ENDPOINT_DETECTED, EventType.ASSISTANT_STARTED_SPEAKING], "ASSISTANT_SPEAKING"),
        ([EventType.ASSISTANT_STARTED_SPEAKING, EventType.TURN_COMPLETED], "LISTENING"),
        ([EventType.TRANSPORT_MEDIA_READY, EventType.RUNTIME_ERROR], "ERROR"),
        ([EventType.TRANSPORT_MEDIA_READY, EventType.TRANSPORT_FAILED], "ERROR"),
        ([EventType.TURN_COMPLETED, EventType.SESSION_ENDED], "ENDED"),
    ],
)
def test_the_state_is_derived_from_the_events(types: list[EventType], expected: str) -> None:
    rows = []
    for n, type_ in enumerate(types, start=1):
        row = diagnostics.project(_event(type_, seq=n, ts=1000.0 + n), 1000.0)
        if row is not None:
            rows.append(row)
    assert diagnostics.runtime_state(rows) == expected


# ══ the stream ════════════════════════════════════════════════════════════════


async def test_a_finished_session_streams_its_whole_history_then_ends() -> None:
    async with _product() as (app, client):
        await _signup(client, "ada@example.com", "Org A")
        agent_id = await _agent(client)
        session_id = await _call(app, client, agent_id)

        frames = await _read_stream(
            client, f"/v1/console/playground/sessions/{session_id}/diagnostics"
        )

        assert [f["event"] for f in frames][-1] == "end"
        batches = [f for f in frames if f["event"] == "batch"]
        assert batches, frames
        events = [e for batch in batches for e in batch["events"]]
        types = [e["type"] for e in events]
        assert "session.started" in types
        assert "session.ended" in types
        # The bus's own order, which is what `seq` means.
        seqs = [e["seq"] for e in events]
        assert seqs == sorted(seqs)
        assert len(set(seqs)) == len(seqs)
        # Every row is renderable without further lookup.
        for event in events:
            assert event["category"] in diagnostics.CATEGORIES
            assert event["severity"] in ("info", "warn", "error")
            assert event["message"]
            assert event["offset_s"] >= 0
        assert batches[-1]["state"] == "ENDED"
        assert frames[-1]["state"] == "ENDED"


async def test_a_reader_resumes_from_the_sequence_it_has_seen() -> None:
    """Reconnecting is cheap and lossless: the durable log is the cursor."""
    async with _product() as (app, client):
        await _signup(client, "ada@example.com", "Org A")
        session_id = await _call(app, client, await _agent(client))
        path = f"/v1/console/playground/sessions/{session_id}/diagnostics"

        whole = await _read_stream(client, path)
        all_events = [e for f in whole if f["event"] == "batch" for e in f["events"]]
        assert len(all_events) > 2
        midpoint = all_events[len(all_events) // 2]["seq"]

        resumed = await _read_stream(client, f"{path}?after_seq={midpoint}")
        later = [e for f in resumed if f["event"] == "batch" for e in f["events"]]
        assert later, resumed
        assert all(e["seq"] > midpoint for e in later)
        # The state still reflects the whole session, not just the tail.
        assert [f for f in resumed if f["event"] == "batch"][-1]["state"] == "ENDED"


async def test_the_live_transcript_is_the_persisted_transcript() -> None:
    """There is no second transcript: the stream serves the rows Conversations serves."""
    async with _product() as (app, client):
        await _signup(client, "ada@example.com", "Org A")
        session_id = await _call(app, client, await _agent(client))

        frames = await _read_stream(
            client, f"/v1/console/playground/sessions/{session_id}/diagnostics"
        )
        streamed = [f for f in frames if f["event"] == "batch"][-1]["transcript"]
        persisted = (await client.get(f"/v1/console/conversations/{session_id}")).json()[
            "transcript"
        ]
        assert [(t["role"], t["text"]) for t in streamed] == [
            (t["role"], t["text"]) for t in persisted
        ]


async def test_diagnostics_do_not_write_anything() -> None:
    """A projection reads. Reading a session must not add an event to its own history."""
    async with _product() as (app, client):
        await _signup(client, "ada@example.com", "Org A")
        session_id = await _call(app, client, await _agent(client))

        before = (await client.get(f"/v1/console/conversations/{session_id}")).json()
        await _read_stream(client, f"/v1/console/playground/sessions/{session_id}/diagnostics")
        after = (await client.get(f"/v1/console/conversations/{session_id}")).json()

        assert before["timeline"] == after["timeline"]
        assert before["turns"] == after["turns"]
        assert before["transcript"] == after["transcript"]


# ══ authorization ═════════════════════════════════════════════════════════════


async def test_another_organizations_session_is_not_found() -> None:
    async with _product() as (app, client):
        await _signup(client, "ada@example.com", "Org A")
        session_id = await _call(app, client, await _agent(client))
        await client.post("/v1/console/auth/logout", headers=HEADERS)

        await _signup(client, "grace@example.com", "Org B")
        refused = await client.get(
            f"/v1/console/playground/sessions/{session_id}/diagnostics"
        )
        assert refused.status_code == 404
        assert "ses_" not in refused.text or session_id not in refused.json().get("error", {}).get(
            "message", ""
        )


async def test_a_forged_or_unknown_session_is_not_found() -> None:
    async with _product() as (_app, client):
        await _signup(client, "ada@example.com", "Org A")
        for session_id in ("ses_does_not_exist", "../../etc/passwd", "ses_' OR 1=1 --"):
            response = await client.get(
                f"/v1/console/playground/sessions/{session_id}/diagnostics"
            )
            assert response.status_code in (404, 400), (session_id, response.status_code)


async def test_a_signed_out_reader_is_refused() -> None:
    async with _product() as (app, client):
        await _signup(client, "ada@example.com", "Org A")
        session_id = await _call(app, client, await _agent(client))
        await client.post("/v1/console/auth/logout", headers=HEADERS)

        response = await client.get(f"/v1/console/playground/sessions/{session_id}/diagnostics")
        assert response.status_code == 401


async def test_a_viewer_may_read_the_conversation_but_not_the_live_runtime() -> None:
    """Live runtime detail belongs to whoever may place the call (`developer`+)."""
    from api.console.identity import service as identity
    from api.models import Org

    async with _product() as (app, client):
        await _signup(client, "ada@example.com", "Org A")
        session_id = await _call(app, client, await _agent(client))
        org_id = (await client.get("/v1/console/me")).json()["organization"]["id"]
        with DBSession(engine) as db:
            org = db.get(Org, org_id)
            assert org is not None
            viewer = identity.create_user(db, email="val@example.com", password=PASSWORD)
            identity.add_membership(db, org=org, user=viewer, role="viewer")
            db.commit()
        await client.post("/v1/console/auth/logout", headers=HEADERS)
        await client.post(
            "/v1/console/auth/login",
            headers=HEADERS,
            json={"email": "val@example.com", "password": PASSWORD},
        )

        refused = await client.get(f"/v1/console/playground/sessions/{session_id}/diagnostics")
        assert refused.status_code == 403
        # …and the ordinary conversation view still works for them.
        assert (await client.get(f"/v1/console/conversations/{session_id}")).status_code == 200


# ══ a failing runtime ═════════════════════════════════════════════════════════


async def test_a_runtime_failure_is_recorded_shown_and_cleaned_up() -> None:
    """A failure must not vanish: it reaches the durable log, the diagnostics and the history."""
    from api.console.diagnostics.service import project

    async with _product() as (app, client):
        await _signup(client, "ada@example.com", "Org A")
        session_id = await _call(app, client, await _agent(client))

        # The runtime's own failure vocabulary, projected the way the stream projects it.
        failure = _event(EventType.RUNTIME_ERROR, seq=99, data={"reason": "stt_stream_failed"})
        row = project(failure, 1000.0)
        assert row is not None
        assert row.severity == "error"
        assert row.detail == {"reason": "stt_stream_failed"}

        # The session was cleaned up regardless, and its history is consistent.
        detail = (await client.get(f"/v1/console/conversations/{session_id}")).json()
        assert detail["status"] == "ended"
        assert detail["events_recorded"] is True


async def test_every_template_agent_produces_diagnostics_through_the_same_path() -> None:
    """M12.13's eight templates use the one runtime, so they use the one diagnostics path."""
    import json as _json
    from pathlib import Path

    templates = _json.loads(
        (
            Path(__file__).resolve().parents[2]
            / "console"
            / "src"
            / "features"
            / "agents"
            / "templates.json"
        ).read_text()
    )["templates"]

    async with _product() as (app, client):
        await _signup(client, "ada@example.com", "Org A")
        for template in templates:
            created = await client.post(
                "/v1/console/agents",
                headers=HEADERS,
                json={
                    "name": template["agent"]["name"],
                    "system_prompt": template["agent"]["system_prompt"],
                    "first_message": template["agent"]["first_message"],
                    "language": template["agent"]["language"],
                },
            )
            assert created.status_code == 201, created.text
            session_id = await _call(app, client, created.json()["id"])

            frames = await _read_stream(
                client, f"/v1/console/playground/sessions/{session_id}/diagnostics"
            )
            events = [e for f in frames if f["event"] == "batch" for e in f["events"]]
            types = {e["type"] for e in events}
            assert "session.started" in types, template["id"]
            assert "session.ended" in types, template["id"]
            assert frames[-1]["event"] == "end"
            assert frames[-1]["state"] == "ENDED", template["id"]
