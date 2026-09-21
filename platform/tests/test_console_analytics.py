"""M12.6 — Analytics: read-only aggregates over the conversation data (ADR-0055 DP6, Amendment 2).

The contract is `docs/architecture/epic12/m12-analytics-definitions.md`. What is proven here:

* every figure is derived from the M11 `session` rows and `metric.turn` in the Epic 10 durable log,
  and is **exact** against a hand-built population — including one designed to catch join
  multiplication (many turns, transcripts and lifecycle events per conversation);
* **missing is never 0**: an unmeasured latency is not a sample, an unfinished conversation has no
  duration, an unrecorded conversation has unknown turns, and a figure without population is null —
  while a genuine 0 (an empty day, a 0 ms measurement) stays 0;
* the range is UTC calendar days, half-open on instants, membership by `started_at`;
* **F-7 end to end**: the `voice_to_voice_ms` a real M11 conversation produces reaches the analytics
  unchanged, and without audio endpointing it is reported missing, not 0;
* tenant isolation on every endpoint and filter, and the role matrix.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Iterator
from datetime import UTC, date, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session as DBSession
from sqlmodel import SQLModel, delete

from api.console import ConsoleSettings, build_console_app
from api.console.analytics.service import percentile
from api.console.identity import service as identity
from api.console.identity.deps import CONSOLE_HEADER
from api.console.persistence import AgentStateRow, ConsoleSessionRow, MembershipRow, UserRow
from api.console.routers.analytics import analytics_today
from api.db import engine
from api.models import Org, Session, Transcript
from api.webrtc.config import WebRTCSettings
from core.events import Event, EventSource, EventType
from events.persistence import EventDatabase, SqlDurableEventLog

HEADERS = {CONSOLE_HEADER: "1"}
PASSWORD = "correct horse battery"
TODAY = date(2026, 9, 21)
ENDPOINTS = ("summary", "latency", "timeseries", "agents")


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
    app = build_console_app(ConsoleSettings(signup="open"), webrtc_settings=WebRTCSettings())
    app.dependency_overrides[analytics_today] = lambda: TODAY
    client = TestClient(app)
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


def _at(day: int, hour: int = 12, minute: int = 0, second: int = 0) -> datetime:
    return datetime(2026, 9, day, hour, minute, second, tzinfo=UTC)


def _session(
    org_id: str,
    agent_id: str,
    *,
    started: datetime | None,
    status: str = "ended",
    seconds: float | None = 30.0,
    source: str | None = None,
) -> str:
    with DBSession(engine) as db:
        session = Session(
            org_id=org_id,
            agent_id=agent_id,
            engine="voxvoice",
            status=status,
            started_at=started,
            # Only an ended session has an end (the frozen `end_session` is its only writer).
            ended_at=started + timedelta(seconds=seconds)
            if started and seconds is not None and status == "ended"
            else None,
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


_counter = iter(range(1, 1_000_000))


def _event(session_id: str, org_id: str, type_: EventType, **fields) -> Event:
    n = next(_counter)
    return Event(
        id=f"evt_an_{n}",
        type=type_,
        ts=float(n),
        seq=n,
        session_id=session_id,
        source=EventSource.RUNTIME,
        org_id=org_id,
        **fields,
    )


def _recorded(
    session_id: str, org_id: str, *turns: dict, noise: int = 0, event_org: str | None = None
) -> None:
    """A recorded conversation: `session.started`, then one `metric.turn` per payload in `turns`,
    with `noise` extra lifecycle events per turn (they must never be counted as anything)."""
    owner = event_org or org_id
    events = [_event(session_id, owner, EventType.SESSION_STARTED)]
    for index, payload in enumerate(turns, start=1):
        events.append(_event(session_id, owner, EventType.TURN_STARTED, turn=index))
        events += [
            _event(session_id, owner, EventType.TRANSPORT_MEDIA_READY, turn=index)
            for _ in range(noise)
        ]
        events.append(_event(session_id, owner, EventType.METRIC_TURN, turn=index, data=payload))
    events.append(_event(session_id, owner, EventType.SESSION_ENDED))
    _append(*events)


def _turn(v2v: int | None = None, status: str = "completed", **ms: int) -> dict:
    payload: dict = {"status": status, "at": {"speech_end": 1.0}, "ms": dict(ms)}
    payload["voice_to_voice_ms"] = v2v
    return payload


def _get(client: TestClient, endpoint: str, **params) -> dict:
    response = client.get(f"/v1/console/analytics/{endpoint}", params=params)
    assert response.status_code == 200, response.text
    return response.json()


def _latency(client: TestClient, metric: str = "voice_to_voice", **params) -> dict:
    return next(m for m in _get(client, "latency", **params)["data"] if m["metric"] == metric)


# ══ empty ═════════════════════════════════════════════════════════════════════


def test_an_organization_without_conversations_has_zeros_and_no_averages(ada):
    summary = _get(ada, "summary")
    assert summary["conversations"] == {
        "total": 0,
        "by_status": {"created": 0, "active": 0, "ended": 0, "failed": 0},
        "by_source": {"playground": 0, "api": 0},
        "recorded": 0,
        "not_recorded": 0,
    }
    # Counts of nothing are 0; averages of nothing do not exist.
    assert summary["duration"] == {"measured": 0, "unmeasured": 0, "total_s": None, "avg_s": None}
    assert summary["turns"]["total"] == 0
    assert summary["turns"]["avg_per_conversation"] is None

    for metric in _get(ada, "latency")["data"]:
        assert metric["samples"] == 0
        assert [metric[k] for k in ("avg_ms", "min_ms", "p50_ms", "p95_ms", "max_ms")] == [None] * 5

    series = _get(ada, "timeseries")["data"]
    assert [b["date"] for b in series] == [f"2026-09-{d}" for d in range(15, 22)]
    assert all(b["conversations"] == 0 and b["voice_to_voice_p50_ms"] is None for b in series)
    assert _get(ada, "agents")["data"] == []


# ══ exact aggregation ═════════════════════════════════════════════════════════


def test_the_summary_is_exact_and_does_not_multiply_through_turns_or_events(ada):
    org_id = _org_id(ada)
    support, sales = _agent(ada, "Support"), _agent(ada, "Sales")
    # Three turns, each wrapped in noise events and with transcript rows: still ONE conversation.
    busy = _session(org_id, support, started=_at(20), seconds=90, source="console_playground")
    _recorded(busy, org_id, _turn(800), _turn(1000), _turn(None, "interrupted"), noise=3)
    with DBSession(engine) as db:
        db.add_all(Transcript(session_id=busy, role="user", text=str(i), ts=i) for i in range(6))
        db.commit()
    quiet = _session(org_id, sales, started=_at(19), seconds=30)
    _recorded(quiet, org_id, _turn(600))
    live = _session(org_id, sales, started=_at(21, 9), status="active")
    _recorded(live, org_id)  # started, no turn yet: a known zero
    _session(org_id, sales, started=_at(18), status="failed", seconds=None)  # never recorded

    summary = _get(ada, "summary")
    assert summary["conversations"] == {
        "total": 4,
        "by_status": {"created": 0, "active": 1, "ended": 2, "failed": 1},
        "by_source": {"playground": 1, "api": 3},
        "recorded": 3,
        "not_recorded": 1,
    }
    assert summary["duration"] == {"measured": 2, "unmeasured": 2, "total_s": 120.0, "avg_s": 60.0}
    assert summary["turns"] == {
        "total": 4,
        "by_status": {"completed": 3, "cancelled": 0, "interrupted": 1, "other": 0},
        # 4 turns over the 3 conversations whose turns are known; the unrecorded one is excluded.
        "avg_per_conversation": 1.33,
    }


def test_latency_counts_only_measured_samples_and_keeps_a_real_zero(ada):
    org_id, agent_id = _org_id(ada), _agent(ada)
    session_id = _session(org_id, agent_id, started=_at(20))
    _recorded(
        session_id,
        org_id,
        _turn(700, endpoint=100, llm=300),
        _turn(0, endpoint=0, llm=500),  # a measured 0 is a sample, not a gap
        _turn(None, llm=400),  # voice-to-voice and endpoint not measured
        _turn(900, status="cancelled", llm=200, stt=50),
    )

    report = {m["metric"]: m for m in _get(ada, "latency")["data"]}
    assert list(report) == ["voice_to_voice", "endpoint", "stt", "llm", "tts", "egress"]
    v2v = report["voice_to_voice"]
    assert (v2v["turns"], v2v["samples"], v2v["missing"]) == (4, 3, 1)
    assert (v2v["min_ms"], v2v["p50_ms"], v2v["p95_ms"], v2v["max_ms"]) == (0, 700, 900, 900)
    assert v2v["avg_ms"] == pytest.approx(533.3)
    assert (report["endpoint"]["samples"], report["endpoint"]["min_ms"]) == (2, 0)
    assert (report["llm"]["samples"], report["llm"]["avg_ms"]) == (4, 350.0)
    assert report["stt"]["samples"] == 1
    # Never measured at all: every figure is absent, none is 0.
    assert report["tts"] == {
        "metric": "tts", "turns": 4, "samples": 0, "missing": 4,
        "avg_ms": None, "min_ms": None, "p50_ms": None, "p95_ms": None, "max_ms": None,
    }  # fmt: skip


def test_two_metric_records_for_one_spoken_turn_count_as_two_like_conversations_show(ada):
    """Without audio endpointing (F-7) one exchange yields two records, only one of which may carry
    a voice-to-voice value. Analytics counts records exactly as the conversation detail lists them,
    and never invents the missing value."""
    org_id, agent_id = _org_id(ada), _agent(ada)
    session_id = _session(org_id, agent_id, started=_at(20))
    _append(
        _event(session_id, org_id, EventType.SESSION_STARTED),
        _event(session_id, org_id, EventType.METRIC_TURN, turn=1, data=_turn(None, llm=300)),
        _event(session_id, org_id, EventType.METRIC_TURN, turn=1, data=_turn(None)),
    )
    detail = ada.get(f"/v1/console/conversations/{session_id}").json()
    v2v = _latency(ada)
    assert _get(ada, "summary")["turns"]["total"] == len(detail["turns"]) == 2
    assert (v2v["samples"], v2v["missing"], v2v["p50_ms"]) == (0, 2, None)


@pytest.mark.parametrize(
    "values,p50,p95",
    [
        ([5], 5, 5),
        ([1, 2], 1, 2),
        ([1, 2, 3, 4], 2, 4),
        (list(range(1, 101)), 50, 95),
        ([], None, None),
    ],
)
def test_percentiles_are_nearest_rank_and_always_observed_values(values, p50, p95):
    assert (percentile(values, 50), percentile(values, 95)) == (p50, p95)


# ══ incomplete data ═══════════════════════════════════════════════════════════


def test_an_unfinished_conversation_has_no_duration_rather_than_zero(ada):
    org_id, agent_id = _org_id(ada), _agent(ada)
    _session(org_id, agent_id, started=_at(21, 8), status="active")
    duration = _get(ada, "summary")["duration"]
    assert duration == {"measured": 0, "unmeasured": 1, "total_s": None, "avg_s": None}
    assert _get(ada, "timeseries")["data"][-1]["avg_duration_s"] is None


def test_a_zero_length_conversation_is_a_real_zero_duration(ada):
    org_id, agent_id = _org_id(ada), _agent(ada)
    _session(org_id, agent_id, started=_at(20), seconds=0)
    assert _get(ada, "summary")["duration"] == {
        "measured": 1, "unmeasured": 0, "total_s": 0.0, "avg_s": 0.0
    }  # fmt: skip


def test_conversations_the_log_never_saw_have_unknown_turns(ada):
    org_id, agent_id = _org_id(ada), _agent(ada)
    _session(org_id, agent_id, started=_at(20))  # e.g. from before the log was attached (M12.5)
    summary = _get(ada, "summary")
    assert (summary["conversations"]["recorded"], summary["conversations"]["not_recorded"]) == (
        0,
        1,
    )
    assert summary["turns"]["avg_per_conversation"] is None
    usage = _get(ada, "agents")["data"][0]
    assert (usage["recorded"], usage["avg_turns_per_conversation"]) == (0, None)


# ══ time range ════════════════════════════════════════════════════════════════


def test_membership_is_by_start_utc_half_open_and_a_call_across_midnight_stays_on_its_day(ada):
    org_id, agent_id = _org_id(ada), _agent(ada)
    _session(org_id, agent_id, started=_at(15, 0, 0, 0))  # the range's first instant
    _session(org_id, agent_id, started=_at(14, 23, 59, 59))  # one second before: excluded
    _session(org_id, agent_id, started=_at(22, 0, 0, 0))  # the range's end instant: excluded
    late = _session(org_id, agent_id, started=_at(18, 23, 58), seconds=240)  # ends on the 19th
    _recorded(late, org_id, _turn(500), _turn(700))

    summary = _get(ada, "summary")  # 7d ending TODAY = 15..21
    assert summary["query"]["range"] == {
        "preset": "7d", "first_day": "2026-09-15", "last_day": "2026-09-21",
        "start": "2026-09-15T00:00:00Z", "end": "2026-09-22T00:00:00Z",
        "timezone": "UTC", "bucket": "day",
    }  # fmt: skip
    assert summary["conversations"]["total"] == 2
    series = {b["date"]: b for b in _get(ada, "timeseries")["data"]}
    assert series["2026-09-15"]["conversations"] == 1
    assert (series["2026-09-18"]["conversations"], series["2026-09-18"]["turns"]) == (1, 2)
    assert series["2026-09-18"]["avg_duration_s"] == 240.0  # whole call, on the day it started
    assert series["2026-09-18"]["voice_to_voice_p50_ms"] == 500
    assert (series["2026-09-19"]["conversations"], series["2026-09-19"]["turns"]) == (0, 0)


def test_presets_and_custom_ranges(ada):
    org_id, agent_id = _org_id(ada), _agent(ada)
    for day in (21, 20, 10, 1):
        _session(org_id, agent_id, started=_at(day))
    _session(org_id, agent_id, started=datetime(2026, 8, 20, 12, tzinfo=UTC))

    def total(**params) -> int:
        return _get(ada, "summary", **params)["conversations"]["total"]

    assert (total(range="today"), total(range="7d"), total(range="30d")) == (1, 2, 4)
    assert total(**{"from": "2026-09-01", "to": "2026-09-10"}) == 2
    custom = _get(ada, "timeseries", **{"from": "2026-08-20", "to": "2026-08-22"})
    assert custom["query"]["range"]["preset"] is None
    assert [(b["date"], b["conversations"]) for b in custom["data"]] == [
        ("2026-08-20", 1), ("2026-08-21", 0), ("2026-08-22", 0)
    ]  # fmt: skip


@pytest.mark.parametrize(
    "params",
    [
        {"range": "7d", "from": "2026-09-01", "to": "2026-09-02"},
        {"from": "2026-09-01"},
        {"from": "2026-09-05", "to": "2026-09-01"},
        {"from": "2026-01-01", "to": "2026-09-01"},
    ],
)
def test_an_invalid_range_is_refused(ada, params):
    response = ada.get("/v1/console/analytics/summary", params=params)
    assert response.status_code == 400
    assert response.json()["error"]["type"] == "invalid_request"


def test_unknown_parameter_values_are_refused(ada):
    for params in ({"range": "year"}, {"source": "sdk"}, {"from": "yesterday", "to": "2026-09-01"}):
        assert ada.get("/v1/console/analytics/summary", params=params).status_code == 422


# ══ filters and agents ════════════════════════════════════════════════════════


def test_agent_and_source_filters(ada):
    org_id = _org_id(ada)
    support, sales = _agent(ada, "Support"), _agent(ada, "Sales")
    a = _session(org_id, support, started=_at(20), source="console_playground")
    _recorded(a, org_id, _turn(400))
    b = _session(org_id, sales, started=_at(20))
    _recorded(b, org_id, _turn(900), _turn(1100))

    only_sales = _get(ada, "summary", agent_id=sales)
    assert (only_sales["conversations"]["total"], only_sales["turns"]["total"]) == (1, 2)
    assert only_sales["query"]["filters"] == {"agent_id": sales, "source": None}
    assert _latency(ada, agent_id=sales)["p50_ms"] == 900
    assert _get(ada, "summary", source="playground")["conversations"]["total"] == 1
    assert _get(ada, "summary", source="api")["turns"]["total"] == 2
    assert [u["name"] for u in _get(ada, "agents", source="api")["data"]] == ["Sales"]


def test_agent_usage_is_per_agent_and_includes_archived_agents(ada):
    org_id = _org_id(ada)
    support, sales = _agent(ada, "Support"), _agent(ada, "Sales")
    for seconds in (30, 60):
        s = _session(org_id, sales, started=_at(20), seconds=seconds)
        _recorded(s, org_id, _turn(1000), _turn(2000))
    s = _session(org_id, support, started=_at(19), seconds=10)
    _recorded(s, org_id, _turn(300))
    assert ada.post(f"/v1/console/agents/{support}/archive", headers=HEADERS).status_code == 204

    usage = _get(ada, "agents")["data"]
    assert [(u["name"], u["archived"], u["conversations"], u["turns"]) for u in usage] == [
        ("Sales", False, 2, 4),
        ("Support", True, 1, 1),
    ]
    assert (usage[0]["avg_duration_s"], usage[0]["avg_turns_per_conversation"]) == (45.0, 2.0)
    assert (usage[0]["voice_to_voice"]["samples"], usage[0]["voice_to_voice"]["p50_ms"]) == (
        4,
        1000,
    )
    # History of an archived agent stays queryable.
    assert _get(ada, "summary", agent_id=support)["conversations"]["total"] == 1


# ══ tenancy ═══════════════════════════════════════════════════════════════════


def test_another_organizations_data_is_never_counted(ada, bob):
    org_a, org_b = _org_id(ada), _org_id(bob)
    a_session = _session(org_a, _agent(ada), started=_at(20))
    _recorded(a_session, org_a, _turn(500))
    b_agent = _agent(bob)
    b_session = _session(org_b, b_agent, started=_at(20))
    _recorded(b_session, org_b, _turn(9000), _turn(9000))
    # Malformed: events for A's session filed under B's org, and B's events filed under A's.
    _append(_event(a_session, org_b, EventType.METRIC_TURN, turn=9, data=_turn(1)))
    _append(_event(b_session, org_a, EventType.METRIC_TURN, turn=9, data=_turn(2)))

    for client, turns, p50 in ((ada, 1, 500), (bob, 2, 9000)):
        summary = _get(client, "summary")
        assert (summary["conversations"]["total"], summary["turns"]["total"]) == (1, turns)
        assert _latency(client)["p50_ms"] == p50
        assert sum(b["turns"] for b in _get(client, "timeseries")["data"]) == turns
        assert [u["turns"] for u in _get(client, "agents")["data"]] == [turns]


def test_another_organizations_agent_looks_exactly_like_a_missing_one(ada, bob):
    foreign = _agent(bob)
    _session(_org_id(bob), foreign, started=_at(20))
    for endpoint in ENDPOINTS:
        theirs = ada.get(f"/v1/console/analytics/{endpoint}", params={"agent_id": foreign})
        missing = ada.get(f"/v1/console/analytics/{endpoint}", params={"agent_id": "agt_nope"})
        assert theirs.status_code == missing.status_code == 404
        assert theirs.json()["error"]["message"] == missing.json()["error"]["message"]


def test_the_organization_cannot_be_chosen_by_the_client(ada, bob):
    b_session = _session(_org_id(bob), _agent(bob), started=_at(20))
    _recorded(b_session, _org_id(bob), _turn(500))
    for params in ({"org_id": _org_id(bob)}, {"organization_id": _org_id(bob)}):
        assert _get(ada, "summary", **params)["conversations"]["total"] == 0


@pytest.mark.parametrize("role", ["viewer", "developer", "admin"])
def test_every_role_can_read_analytics_of_its_organization(ada, bob, role):
    org_id = _org_id(ada)
    _recorded(_session(org_id, _agent(ada), started=_at(20)), org_id, _turn(500))
    with DBSession(engine) as db:
        identity.add_membership(
            db, org=db.get(Org, org_id), user=identity.find_user(db, "bob@example.com"), role=role
        )
        db.commit()
    bob.post("/v1/console/session/organization", headers=HEADERS, json={"organization_id": org_id})

    for endpoint in ENDPOINTS:
        assert bob.get(f"/v1/console/analytics/{endpoint}").status_code == 200
    assert _get(bob, "summary")["turns"]["total"] == 1  # the owner (ada) is covered everywhere else


def test_analytics_need_a_session():
    with TestClient(build_console_app(ConsoleSettings(), webrtc_settings=WebRTCSettings())) as c:
        for endpoint in ENDPOINTS:
            response = c.get(f"/v1/console/analytics/{endpoint}")
            assert response.status_code == 401
            assert response.json()["error"]["type"] == "unauthenticated"


def test_analytics_are_read_only(ada):
    # No write route exists: the console's fallback answers any other method (M12.1 error model).
    for endpoint in ENDPOINTS:
        for method in ("POST", "PUT", "PATCH", "DELETE"):
            response = ada.request(method, f"/v1/console/analytics/{endpoint}", headers=HEADERS)
            assert response.status_code in (404, 405)


# ══ F-7, end to end ═══════════════════════════════════════════════════════════


@pytest.mark.parametrize("endpointing", [True, False])
async def test_f7_a_real_conversations_voice_to_voice_reaches_analytics_unchanged(ada, endpointing):
    """Real M11 conversation → `metric.turn` → durable log → M12.5 detail → M12.6 analytics: the
    same measured value at every step, and without audio endpointing it is missing at every step."""
    from tests.test_console_conversations import _metric_turns

    metrics = await _metric_turns(through_decorator=endpointing)
    org_id = _org_id(ada)
    session_id = _session(org_id, _agent(ada), started=_at(20))
    _append(
        _event(session_id, org_id, EventType.SESSION_STARTED),
        *(
            dataclasses.replace(
                m, id=f"evt_f7_{endpointing}_{i}", session_id=session_id, org_id=org_id
            )
            for i, m in enumerate(metrics)
        ),
    )

    produced = [m.data.get("voice_to_voice_ms") for m in metrics]
    shown = [
        t["voice_to_voice_ms"]
        for t in ada.get(f"/v1/console/conversations/{session_id}").json()["turns"]
    ]
    v2v = _latency(ada)
    assert shown == produced
    assert _get(ada, "summary")["turns"]["total"] == len(metrics)
    measured = sorted(v for v in produced if isinstance(v, int))
    if endpointing:
        assert len(measured) == len(metrics) == 1
        assert v2v["min_ms"] == v2v["p50_ms"] == v2v["max_ms"] == measured[0]
    else:
        assert measured == []
        assert (v2v["samples"], v2v["missing"], v2v["p50_ms"]) == (0, len(metrics), None)
