"""M12.5 — Conversations: persisted, ordered, tenant-scoped call history (ADR-0055 DP6, LLD §6).

What is proven here:

* **F-7** — on the product path (`audio_endpointing=False`, as the frozen `create_session` leaves
  it) a real conversation's turn gets **no `voice_to_voice_ms`** and an orphan second `metric.turn`;
  through `AudioEndpointingController` the same conversation yields one complete metric. The
  decorator is applied only to the real profile, and can be switched off;
* **F-2** — the console composition attaches Epic 10's durable log: a session started through the
  unchanged developer API is recorded, and its lifecycle is readable as a conversation;
* **F-3** — the transcript sink records each item's offset in the call, so transcripts have an
  order; sessions without one are reported as unordered, never guessed;
* the conversation read model: newest-first keyset pagination and filters, product views of
  `metric.turn` (absent stays absent — never 0), an allow-listed timeline, no raw payloads;
* tenant isolation on every read, including events filed under a foreign `org_id`.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session as DBSession
from sqlmodel import SQLModel, delete

from api.console import ConsoleSettings, build_console_app
from api.console.app import apply_session_defaults
from api.console.identity.deps import CONSOLE_HEADER
from api.console.persistence import AgentStateRow, ConsoleSessionRow, MembershipRow, UserRow
from api.console.session_defaults import AudioEndpointingController
from api.db import engine
from api.models import ApiKey, Session, Transcript
from api.security import generate_api_key, hash_key
from api.webrtc.app import _persist_transcript
from api.webrtc.config import WebRTCSettings
from core.events import Event, EventSource, EventType
from events.persistence import EventDatabase, SqlDurableEventLog
from runtime.service import RuntimeSessionService

HEADERS = {CONSOLE_HEADER: "1"}
PASSWORD = "correct horse battery"


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


def _agent(client: TestClient, name: str = "Bot") -> str:
    response = client.post(
        "/v1/console/agents", headers=HEADERS, json={"name": name, "system_prompt": "hi"}
    )
    return response.json()["id"]


def _session(
    org_id: str,
    agent_id: str,
    *,
    started: datetime,
    status: str = "ended",
    source: str | None = None,
    seconds: float = 30.0,
) -> str:
    with DBSession(engine) as db:
        session = Session(
            org_id=org_id,
            agent_id=agent_id,
            engine="voxvoice",
            status=status,
            started_at=started,
            ended_at=started + timedelta(seconds=seconds) if status == "ended" else None,
            meta={"source": source} if source else {},
        )
        db.add(session)
        db.commit()
        return session.id


def _append(*events: Event) -> None:
    database = EventDatabase(str(engine.url))
    database.create_all()
    log = SqlDurableEventLog(database)
    for event in events:
        log.append(event)
    database.close()


def _event(session_id: str, org_id: str, seq: int, type_: EventType, ts: float, **fields) -> Event:
    return Event(
        id=f"evt_{session_id}_{seq}",
        type=type_,
        ts=ts,
        seq=seq,
        session_id=session_id,
        source=fields.pop("source", EventSource.RUNTIME),
        org_id=org_id,
        **fields,
    )


# ══ F-7: audio endpointing on the product path ════════════════════════════════


async def _metric_turns(*, through_decorator: bool) -> list[Event]:
    """One real M11 conversation with the request the product path sends
    (`audio_endpointing=False`), optionally started through the console's decorator."""
    from tests.test_e2e_conversation import real_stack, run_conversation
    from tests.test_epic10_runtime_wiring import RecordingLog, wired_plane

    log = RecordingLog()
    _, request, endpoint, registry = real_stack(
        words=["hello there"], reply=["Good morning. "], audio_endpointing=False
    )
    service = RuntimeSessionService(
        registry, on_transcript=None, streaming_playback=True, observability=wired_plane(log)
    )
    controller = AudioEndpointingController(service) if through_decorator else service

    class Driven:  # the harness reads the service's internals; starts go through `controller`
        _workers = service._workers  # noqa: SLF001
        _registry = service._registry  # noqa: SLF001
        start = controller.start
        end = controller.end

    await run_conversation(Driven(), request, endpoint)
    return log.of(EventType.METRIC_TURN)


async def test_f7_the_product_path_loses_voice_to_voice_without_audio_endpointing():
    """Characterizes the defect: the conversation, with the request `create_session` builds."""
    metrics = await _metric_turns(through_decorator=False)
    assert len(metrics) == 2, "speech_end should land in an orphan second metric"
    assert all(m.data.get("voice_to_voice_ms") is None for m in metrics)


async def test_f7_the_decorator_yields_one_complete_metric_per_turn():
    metrics = await _metric_turns(through_decorator=True)
    assert len(metrics) == 1
    payload = metrics[0].data
    assert isinstance(payload["voice_to_voice_ms"], int)
    # `stt` may be absent: this scripted recognizer finalizes before the endpoint decision, and a
    # negative interval is dropped, never emitted (ADR-0047 ME6) — absent, not zero.
    assert set(payload["ms"]) >= {"endpoint", "llm", "tts", "egress"}


def test_f7_the_decorator_changes_only_audio_endpointing():
    from core.session_control import StartSessionRequest

    seen: list[StartSessionRequest] = []

    class Inner:
        async def start(self, request):
            seen.append(request)

        async def end(self, session_id):
            seen.append(session_id)

        def conversation_state(self, session_id):
            return "listening"

    import asyncio

    request = StartSessionRequest(
        session_id="ses_1", org_id="org_1", agent_id="agt_1", channel="web",
        system_prompt="p", first_message="hi", language="en", llm_model="m", tts_voice="v",
    )
    decorated = AudioEndpointingController(Inner())
    asyncio.run(decorated.start(request))
    (forwarded,) = seen
    assert forwarded.audio_endpointing is True
    assert forwarded == request.__class__(**{**vars_of(request), "audio_endpointing": True})
    assert decorated.conversation_state("ses_1") == "listening"


def vars_of(request) -> dict:
    from dataclasses import fields

    return {f.name: getattr(request, f.name) for f in fields(request)}


@pytest.mark.parametrize(
    "transport,switch,wrapped",
    [("webrtc", True, True), ("webrtc", False, False), ("local", True, False)],
)
def test_f7_is_applied_only_to_the_real_profile_and_can_be_switched_off(transport, switch, wrapped):
    inner = object()
    webrtc = WebRTCSettings(transport=transport, voice_id="voice")
    result = apply_session_defaults(inner, webrtc, ConsoleSettings(audio_endpointing=switch))
    assert isinstance(result, AudioEndpointingController) is wrapped
    switched_off = ConsoleSettings.from_env({"VOXVOICE_CONSOLE_AUDIO_ENDPOINTING": "false"})
    assert switched_off.audio_endpointing is False


# ══ F-2: the console runtime writes the durable log ═══════════════════════════


def test_f2_a_session_run_by_the_console_app_is_recorded_and_readable(ada):
    org_id, agent_id = _org_id(ada), _agent(ada)
    raw = generate_api_key()
    with DBSession(engine) as db:
        db.add(ApiKey(org_id=org_id, key_hash=hash_key(raw), label="t"))
        db.commit()

    started = ada.post("/v1/sessions", headers={"x-api-key": raw}, json={"agent_id": agent_id})
    assert started.status_code == 201, started.text
    session_id = started.json()["session_id"]
    assert ada.post(f"/v1/sessions/{session_id}/end", headers={"x-api-key": raw}).status_code == 200

    detail = ada.get(f"/v1/console/conversations/{session_id}").json()
    assert detail["events_recorded"] is True
    labels = [entry["label"] for entry in detail["timeline"]]
    assert labels[0] == "Session started" and "Session ended" in labels
    # Routine provider teardown is engine detail, not a failure on the timeline.
    assert all("Provider" not in label for label in labels)
    seqs = [entry["seq"] for entry in detail["timeline"]]
    assert seqs == sorted(seqs)
    assert detail["status"] == "ended" and detail["source"] == "api"


# ══ F-3: transcript order ═════════════════════════════════════════════════════


def test_f3_the_transcript_sink_records_each_items_offset_in_the_call(ada):
    org_id, agent_id = _org_id(ada), _agent(ada)
    session_id = _session(
        org_id, agent_id, started=datetime.now(UTC) - timedelta(seconds=5), status="active"
    )
    _persist_transcript(session_id, "user", "first", True)
    _persist_transcript(session_id, "agent", "second", True)
    _persist_transcript(session_id, "user", "partial, ignored", False)

    detail = ada.get(f"/v1/console/conversations/{session_id}").json()
    assert [t["text"] for t in detail["transcript"]] == ["first", "second"]
    first, second = (t["offset_s"] for t in detail["transcript"])
    assert 4.0 <= first <= second
    assert detail["transcript_ordered"] is True


def test_f3_a_transcript_without_timing_is_reported_unordered(ada):
    org_id, agent_id = _org_id(ada), _agent(ada)
    session_id = _session(org_id, agent_id, started=datetime.now(UTC))
    with DBSession(engine) as db:  # what the frozen default app, and pre-M12.5 sessions, stored
        db.add(Transcript(session_id=session_id, role="user", text="a", ts=0.0))
        db.add(Transcript(session_id=session_id, role="agent", text="b", ts=0.0))
        db.commit()

    detail = ada.get(f"/v1/console/conversations/{session_id}").json()
    assert detail["transcript_ordered"] is False
    assert {t["text"] for t in detail["transcript"]} == {"a", "b"}


# ══ the read model ════════════════════════════════════════════════════════════


def test_detail_maps_metric_turn_to_a_product_view(ada):
    org_id, agent_id = _org_id(ada), _agent(ada, "Support")
    session_id = _session(org_id, agent_id, started=datetime.now(UTC), source="console_playground")
    _append(
        _event(session_id, org_id, 1, EventType.SESSION_STARTED, 1000.0),
        _event(session_id, org_id, 2, EventType.TURN_STARTED, 1001.0, turn=1),
        _event(session_id, org_id, 3, EventType.MODEL_TOKEN, 1001.5, turn=1),  # engine detail
        _event(
            session_id, org_id, 4, EventType.METRIC_TURN, 1002.0, turn=1,
            source=EventSource.OBSERVABILITY,
            data={
                "status": "completed",
                "at": {"speech_end": 5.1, "first_audio_out": 5.9},
                "ms": {"endpoint": 120, "llm": 310, "tts": 90, "egress": 40, "user_speech": 800},
                "voice_to_voice_ms": 780,
            },
        ),
        _event(
            session_id, org_id, 5, EventType.METRIC_TURN, 1003.0, turn=2,
            source=EventSource.OBSERVABILITY,
            data={"status": "cancelled", "at": {}, "ms": {}, "voice_to_voice_ms": None},
        ),
        _event(session_id, org_id, 6, EventType.SESSION_ENDED, 1004.5),
    )

    detail = ada.get(f"/v1/console/conversations/{session_id}").json()

    assert detail["agent"] == {"id": agent_id, "name": "Support"}
    assert detail["source"] == "console_playground"
    first, second = detail["turns"]
    assert first == {
        "turn": 1,
        "status": "completed",
        "voice_to_voice_ms": 780,
        "stages_ms": {"endpoint": 120, "llm": 310, "tts": 90, "egress": 40},
        "user_speech_ms": 800,
        "unmeasured": ["stt"],
    }
    # Unmeasured is absent, never zero (E11-10).
    assert second["voice_to_voice_ms"] is None and second["stages_ms"] == {}
    assert second["unmeasured"] == ["endpoint", "stt", "llm", "tts", "egress"]
    assert [(e["label"], e["offset_s"]) for e in detail["timeline"]] == [
        ("Session started", 0.0),
        ("Turn started", 1.0),
        ("Session ended", 4.5),
    ]
    assert "speech_end" not in str(detail) and "model.token" not in str(detail)


def test_events_filed_under_another_organization_are_never_shown(ada, bob):
    org_id, agent_id = _org_id(ada), _agent(ada)
    session_id = _session(org_id, agent_id, started=datetime.now(UTC))
    _append(
        _event(session_id, org_id, 1, EventType.SESSION_STARTED, 10.0),
        _event(session_id, _org_id(bob), 2, EventType.RUNTIME_ERROR, 11.0),  # malformed row
    )

    detail = ada.get(f"/v1/console/conversations/{session_id}").json()
    assert [e["label"] for e in detail["timeline"]] == ["Session started"]


def test_a_conversation_before_the_log_was_attached_says_so(ada):
    session_id = _session(_org_id(ada), _agent(ada), started=datetime.now(UTC))
    detail = ada.get(f"/v1/console/conversations/{session_id}").json()
    assert detail["events_recorded"] is False
    assert detail["turns"] == [] and detail["timeline"] == []


def test_the_list_is_newest_first_paginated_and_filtered(ada):
    org_id = _org_id(ada)
    first, second = _agent(ada, "First"), _agent(ada, "Second")
    base = datetime(2026, 9, 1, tzinfo=UTC)
    ids = [
        _session(org_id, first if i % 2 else second, started=base + timedelta(minutes=i),
                 status="ended" if i < 4 else "failed")
        for i in range(5)
    ]
    with DBSession(engine) as db:
        db.add(Transcript(session_id=ids[4], role="user", text="x", ts=1.0))
        db.add(Transcript(session_id=ids[4], role="agent", text="y", ts=2.0))
        db.commit()

    page = ada.get("/v1/console/conversations?limit=2").json()
    assert [c["id"] for c in page["data"]] == [ids[4], ids[3]]
    assert page["data"][0]["message_count"] == 2
    assert page["data"][0]["duration_s"] is None  # failed: never ended
    assert page["data"][1]["duration_s"] == 30.0
    rest = ada.get(f"/v1/console/conversations?limit=2&cursor={page['next_cursor']}").json()
    assert [c["id"] for c in rest["data"]] == [ids[2], ids[1]]
    last = ada.get(f"/v1/console/conversations?limit=2&cursor={rest['next_cursor']}").json()
    assert [c["id"] for c in last["data"]] == [ids[0]] and last["next_cursor"] is None

    by_agent = ada.get(f"/v1/console/conversations?agent_id={first}").json()["data"]
    assert {c["agent"]["name"] for c in by_agent} == {"First"}
    failed = ada.get("/v1/console/conversations?status=failed").json()["data"]
    assert [c["id"] for c in failed] == [ids[4]]


def test_a_bad_cursor_or_status_is_refused(ada):
    assert ada.get("/v1/console/conversations?cursor=not-a-cursor").status_code == 400
    response = ada.get("/v1/console/conversations?status=exploded")
    assert response.status_code == 422
    assert response.json()["error"]["type"] == "invalid_request"


# ══ tenancy and access ════════════════════════════════════════════════════════


def test_another_organizations_conversations_are_invisible(ada, bob):
    session_id = _session(_org_id(ada), _agent(ada), started=datetime.now(UTC))

    assert bob.get(f"/v1/console/conversations/{session_id}").status_code == 404
    assert bob.get("/v1/console/conversations").json()["data"] == []
    assert [c["id"] for c in ada.get("/v1/console/conversations").json()["data"]] == [session_id]


def test_conversations_need_a_session():
    with TestClient(build_console_app(ConsoleSettings(), webrtc_settings=WebRTCSettings())) as c:
        assert c.get("/v1/console/conversations").status_code == 401
        assert c.get("/v1/console/conversations/ses_x").status_code == 401
