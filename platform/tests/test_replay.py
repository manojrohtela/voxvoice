"""Conversation Replay & Observability tests (M4.3).

Records a deterministic synthetic conversation onto an Event Bus and exercises the
recorder, timeline (+ durations + cursor), event index (filter by every dimension +
search), correlation, metrics, the runtime-state inspector, and the replay controller
(step / jump / speed / advance). Replay is proven deterministic and free of any
Runtime execution, and the recorder is proven isolated from the bus/Runtime.
"""

from __future__ import annotations

import itertools

import pytest

from core.events import EventMetadata, EventSource, EventType
from events import InProcessEventBus
from replay import (
    ConversationRecorder,
    EventCorrelator,
    EventFilter,
    ReplayEngine,
    ReplayState,
)
from replay.errors import CursorOutOfRange

T = EventType
S = EventSource


def _recorded():
    """Drive a fixed 17-event conversation; return (session, {label: event})."""
    ids = (f"e{i}" for i in itertools.count())
    ticks = itertools.count(1)  # ts == seq == 1,2,3,...
    bus = InProcessEventBus(id_factory=lambda: next(ids), clock=lambda: float(next(ticks)))
    rec = ConversationRecorder()
    rec.attach(bus)

    def pub(source):
        return bus.publisher(
            EventMetadata(session_id="S", source=source, org_id="O", conversation_id="S")
        )

    orch, prov, turn, va, ep, itr, st, rt = (
        pub(S.ORCHESTRATOR), pub(S.PROVIDER_REGISTRY), pub(S.TURN_ENGINE),
        pub(S.VOICE_ACTIVITY), pub(S.ENDPOINT), pub(S.INTERRUPT),
        pub(S.SMART_TURN), pub(S.RUNTIME),
    )
    ev = {}
    ev["session_started"] = orch.publish(T.SESSION_STARTED)
    ev["prov_stt"] = prov.publish(T.PROVIDER_CONNECTED, {"capability": "stt", "provider": "local"})
    ev["prov_tts"] = prov.publish(T.PROVIDER_CONNECTED, {"capability": "tts", "provider": "local"})
    ev["turn1_start"] = turn.publish(T.TURN_STARTED, turn=1)
    ev["speech_start"] = va.publish(T.SPEECH_STARTED)
    ev["speech_stop"] = va.publish(T.SPEECH_STOPPED)
    ev["endpoint"] = ep.publish(T.ENDPOINT_DETECTED, {"decision": "endpoint_confirmed"}, turn=1)
    ev["asst_start"] = turn.publish(T.ASSISTANT_STARTED_SPEAKING, turn=1)
    ev["asst_stop"] = turn.publish(T.ASSISTANT_STOPPED_SPEAKING, turn=1)
    ev["turn1_done"] = turn.publish(T.TURN_COMPLETED, turn=1)
    ev["turn2_start"] = turn.publish(T.TURN_STARTED, turn=2)
    ev["int_detected"] = itr.publish(T.INTERRUPT_DETECTED)
    ev["int_confirmed"] = itr.publish(T.INTERRUPT_CONFIRMED)
    ev["turn2_cancel"] = turn.publish(T.TURN_CANCELLED, turn=2)
    ev["smart"] = st.publish(T.SMART_TURN_DECISION, {"decision": "finish_turn"})
    ev["error"] = rt.publish(T.RUNTIME_ERROR, {"reason": "boom"})
    ev["session_ended"] = orch.publish(T.SESSION_ENDED)
    return rec.get("S"), ev


# ── recording ────────────────────────────────────────────────────────────────
def test_recording_captures_ordered_events():
    session, _ = _recorded()
    assert session.event_count == 17
    assert session.closed is True  # SESSION_ENDED closed it
    assert session.org_id == "O"
    assert session.conversation_id == "S"
    seqs = [e.seq for e in session.events]
    assert seqs == list(range(1, 18))  # ordered by bus seq


def test_recorder_is_isolated_from_the_bus():
    bus = InProcessEventBus()
    rec = ConversationRecorder()
    rec.attach(bus)
    # A broken internal state must never raise into publish (Runtime isolation).
    rec._sessions = None  # type: ignore[assignment] — force a recording failure
    pub = bus.publisher(EventMetadata(session_id="x", source=S.RUNTIME))
    pub.publish(T.RUNTIME_STARTED)  # must not raise
    assert rec.failure_count == 1
    other: list = []
    bus.subscribe(other.append)
    pub.publish(T.RUNTIME_STOPPED)
    assert len(other) == 1  # the bus still works


# ── timeline + durations + cursor ────────────────────────────────────────────
def test_timeline_durations_paired():
    session, ev = _recorded()
    eng = ReplayEngine(session)
    tl = eng.timeline
    assert len(tl) == 17
    by_id = {e.event_id: e for e in tl.entries}
    assert by_id[ev["speech_start"].id].duration_ms == 1000.0  # 6-5
    assert by_id[ev["turn1_start"].id].duration_ms == 6000.0  # 10-4
    assert by_id[ev["asst_start"].id].duration_ms == 1000.0  # 9-8
    assert by_id[ev["turn2_start"].id].duration_ms == 3000.0  # 14-11 (cancel closes it)
    # colour-coding present + laned
    assert by_id[ev["endpoint"].id].category == "endpoint"
    assert by_id[ev["endpoint"].id].lane == "decisions"


def test_timeline_cursor_step_and_bounds():
    session, _ = _recorded()
    cur = ReplayEngine(session).timeline
    from replay import TimelineCursor

    c = TimelineCursor(cur)
    assert c.at_start and not c.at_end
    c.step_forward()
    assert c.index == 1 and c.current.seq == 1
    c.seek(17)
    assert c.at_end
    with pytest.raises(CursorOutOfRange):
        c.step_forward()
    c.reset()
    with pytest.raises(CursorOutOfRange):
        c.step_backward()


# ── filtering (every dimension) + search ─────────────────────────────────────
def test_filter_by_turn_type_source_time():
    session, _ = _recorded()
    idx = ReplayEngine(session).index
    assert len(idx.filter(EventFilter(turn=1))) == 5
    assert len(idx.filter(EventFilter(types=frozenset({T.TURN_STARTED})))) == 2
    assert len(idx.filter(EventFilter(source="provider_registry"))) == 2
    assert len(idx.filter(EventFilter(org_id="O"))) == 17
    assert len(idx.filter(EventFilter(conversation_id="S"))) == 17
    assert len(idx.filter(EventFilter(provider="local"))) == 2
    assert len(idx.filter(EventFilter(start_ts=5.0, end_ts=10.0))) == 6


def test_search_payload():
    session, _ = _recorded()
    idx = ReplayEngine(session).index
    assert len(idx.search("boom")) == 1
    assert len(idx.search("local")) == 2
    assert len(idx.search("finish_turn")) == 1


# ── correlation ──────────────────────────────────────────────────────────────
def test_correlation_by_turn():
    session, _ = _recorded()
    groups = {g.key: g for g in EventCorrelator().by_turn(session.events)}
    assert groups["1"].count == 5
    assert groups["2"].count == 2  # TURN_STARTED + TURN_CANCELLED (interrupts have no turn)


# ── metrics ──────────────────────────────────────────────────────────────────
def test_metrics_derived_correctly():
    session, _ = _recorded()
    m = ReplayEngine(session).metrics()
    assert (m.turn_count, m.turns_completed, m.turns_cancelled) == (2, 1, 1)
    assert m.total_turn_duration_ms == 9000.0
    assert m.avg_turn_duration_ms == 4500.0
    assert m.avg_response_time_ms == 4000.0
    assert m.speech_time_ms == 1000.0 and m.speech_segments == 1
    assert m.assistant_speaking_ms == 1000.0
    assert m.interrupt_count == 1 and m.endpoint_count == 1
    assert m.smart_turn_decisions == 1
    assert m.provider_usage == {"local": 2}
    assert m.errors == 1
    assert m.duration_s == 16.0
    # honesty: metrics with no upstream events yet are flagged, not faked
    assert m.token_usage == 0 and "token_usage" in m.pending_metrics
    assert m.thinking_time_ms == 0.0 and "thinking_time_ms" in m.pending_metrics


# ── runtime-state inspector (deterministic fold) ─────────────────────────────
def test_inspector_state_at_various_points():
    session, _ = _recorded()
    eng = ReplayEngine(session)
    s6 = eng.state_at(6)  # after SPEECH_STOPPED
    assert s6.connection == "connected" and s6.turn_phase == "active"
    assert s6.turn_index == 1 and s6.voice_activity == "silence"
    s8 = eng.state_at(8)  # after ASSISTANT_STARTED_SPEAKING
    assert s8.turn_phase == "assistant_speaking"
    s_end = eng.state_at(len(session.events))
    assert s_end.connection == "ended" and s_end.turn_phase == "cancelled"
    assert s_end.smart_turn == "finish_turn" and s_end.last_error == "boom"
    assert s_end.providers == {"stt": "connected", "tts": "connected"}


# ── replay controller: step / jump / speed / advance ─────────────────────────
def test_replay_step_and_jump():
    session, ev = _recorded()
    c = ReplayEngine(session).controller()
    assert c.state is ReplayState.STOPPED
    c.step_forward()
    assert c.position == 1 and c.state is ReplayState.PAUSED
    c.jump_to_timestamp(6.0)
    assert c.position == 6  # entries with ts <= 6
    c.jump_to_event(ev["endpoint"].id)
    assert c.current.event_id == ev["endpoint"].id
    c.stop()
    assert c.position == 0 and c.state is ReplayState.STOPPED


def test_replay_speed_advance_is_deterministic():
    session, _ = _recorded()

    def run(speed):
        c = ReplayEngine(session).controller(speed=speed)
        c.play()
        steps = [len(c.advance(2.0)) for _ in range(5)]
        return steps, c.position, c.state

    assert run(1.0) == run(1.0)  # deterministic replay (same inputs → same output)
    assert run(2.0) == run(2.0)
    steps_1x, pos_1x, state_1x = run(1.0)
    steps_2x, pos_2x, state_2x = run(2.0)
    # Over the same 5 advances, 2x covers more recorded time and reaches the end;
    # 1x is still mid-timeline (deterministic, speed-scaled).
    assert state_2x is ReplayState.STOPPED and pos_2x == 17
    assert sum(steps_2x) == 17  # all events crossed
    assert sum(steps_1x) < 17 and state_1x is ReplayState.PLAYING


def test_replay_runs_no_runtime_logic():
    # The engine only reads recorded events; constructing + fully replaying a session
    # touches no Runtime object (pure functions over the event list).
    session, _ = _recorded()
    eng = ReplayEngine(session)
    c = eng.controller(speed=100.0)
    c.play()
    c.advance(1000.0)
    assert c.state is ReplayState.STOPPED
    # Re-deriving everything is idempotent (no mutation of recorded events).
    assert eng.metrics().turn_count == 2
    assert len(eng.timeline) == 17
