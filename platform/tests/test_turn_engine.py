"""Turn Engine tests (M3.1).

Covers the turn lifecycle (TurnLifecycle), the session-scoped engine (TurnEngine),
the pipeline façade (TurnCoordinator): valid/invalid/duplicate/terminal
transitions, timing capture, cancellation, events, and determinism.
"""

from __future__ import annotations

import pytest

from runtime.errors import (
    DuplicateTurnTransition,
    InvalidTurnTransition,
    NoActiveTurnError,
    TerminalTurnError,
)
from runtime.turn import (
    TERMINAL_TURN_PHASES,
    TURN_TRANSITIONS,
    TurnCoordinator,
    TurnEngine,
    TurnLifecycle,
)
from runtime.turn import (
    TurnPhase as P,
)
from runtime.turn.context import TurnContext

_FULL_PATH = [
    P.LISTENING,
    P.USER_SPEAKING,
    P.USER_FINISHED,
    P.ASSISTANT_THINKING,
    P.ASSISTANT_SPEAKING,
    P.TURN_COMPLETED,
]
_PATHS: dict[P, list[P]] = {
    P.IDLE: [],
    P.LISTENING: _FULL_PATH[:1],
    P.USER_SPEAKING: _FULL_PATH[:2],
    P.USER_FINISHED: _FULL_PATH[:3],
    P.ASSISTANT_THINKING: _FULL_PATH[:4],
    P.ASSISTANT_SPEAKING: _FULL_PATH[:5],
    P.TURN_COMPLETED: _FULL_PATH,
    P.TURN_CANCELLED: [P.TURN_CANCELLED],
}
_VALID_PAIRS = [(src, tgt) for src, targets in TURN_TRANSITIONS.items() for tgt in targets]


def _lifecycle_at(phase: P) -> TurnLifecycle:
    lc = TurnLifecycle(TurnContext(session_id="s", turn_index=1))
    for step in _PATHS[phase]:
        lc.transition(step)
    assert lc.phase is phase
    return lc


# ── TurnLifecycle ────────────────────────────────────────────────────────────
def test_initial_phase_is_idle():
    lc = TurnLifecycle(TurnContext("s", 1))
    assert lc.phase is P.IDLE


@pytest.mark.parametrize("src, tgt", _VALID_PAIRS)
def test_every_valid_transition(src, tgt):
    lc = _lifecycle_at(src)
    lc.transition(tgt)
    assert lc.phase is tgt


def test_every_invalid_transition_raises():
    for src in TURN_TRANSITIONS:
        for tgt in P:
            if tgt == src or tgt in TURN_TRANSITIONS[src]:
                continue
            lc = _lifecycle_at(src)
            if src in TERMINAL_TURN_PHASES:
                with pytest.raises(TerminalTurnError):
                    lc.transition(tgt)
            else:
                with pytest.raises(InvalidTurnTransition):
                    lc.transition(tgt)


def test_duplicate_transition_rejected():
    lc = _lifecycle_at(P.LISTENING)
    with pytest.raises(DuplicateTurnTransition):
        lc.transition(P.LISTENING)


def test_terminal_is_immutable():
    lc = _lifecycle_at(P.TURN_COMPLETED)
    assert lc.is_terminal
    with pytest.raises(TerminalTurnError):
        lc.transition(P.LISTENING)


def test_timing_records_phase_entries():
    ticks = iter([10.0, 11.0, 12.0, 13.0, 14.0, 15.0, 16.0])
    lc = TurnLifecycle(TurnContext("s", 1), clock=lambda: next(ticks))
    for step in _FULL_PATH:
        lc.transition(step)
    t = lc.context.timing
    assert t.turn_started_at == 11.0  # LISTENING
    assert t.first_audio_at == 12.0  # USER_SPEAKING
    assert t.user_finished_at == 13.0  # USER_FINISHED
    assert t.assistant_started_at == 14.0  # ASSISTANT_THINKING
    assert t.assistant_speaking_at == 15.0
    assert t.completed_at == 16.0  # TURN_COMPLETED


def test_events_emitted_including_initial():
    seen: list[tuple] = []
    lc = TurnLifecycle(TurnContext("s", 1), on_event=lambda e: seen.append((e.previous, e.phase)))
    lc.transition(P.LISTENING)
    assert seen[0] == (None, P.IDLE)  # initial record
    assert seen[1] == (P.IDLE, P.LISTENING)


def test_observer_errors_do_not_break_lifecycle():
    def boom(_e):
        raise RuntimeError("observer boom")

    lc = TurnLifecycle(TurnContext("s", 1), on_event=boom)
    lc.transition(P.LISTENING)  # must not raise
    assert lc.phase is P.LISTENING


# ── TurnEngine ───────────────────────────────────────────────────────────────
def test_engine_full_turn_archives_on_completion():
    eng = TurnEngine("s")
    eng.begin_turn()
    assert eng.current is not None and eng.current.phase is P.LISTENING
    assert eng.turn_count == 1
    for step in _FULL_PATH[1:]:
        eng.advance(step)
    assert eng.current is None  # archived on terminal
    assert len(eng.history) == 1 and eng.history[0].phase is P.TURN_COMPLETED


def test_engine_advance_without_turn_raises():
    with pytest.raises(NoActiveTurnError):
        TurnEngine("s").advance(P.USER_SPEAKING)


def test_engine_cancel_current():
    eng = TurnEngine("s")
    eng.begin_turn()
    eng.advance(P.USER_SPEAKING)
    eng.cancel_current()
    assert eng.current is None
    assert eng.history[0].phase is P.TURN_CANCELLED


def test_engine_begin_cancels_incomplete_previous():
    eng = TurnEngine("s")
    eng.begin_turn()
    eng.advance(P.USER_SPEAKING)
    eng.begin_turn()  # previous incomplete → cancelled
    assert eng.turn_count == 2
    assert eng.history[0].phase is P.TURN_CANCELLED


# ── TurnCoordinator ──────────────────────────────────────────────────────────
def test_coordinator_full_turn():
    eng = TurnEngine("s")
    co = TurnCoordinator(eng)
    co.on_user_speech()  # lazy begin → USER_SPEAKING
    assert eng.current is not None and eng.current.phase is P.USER_SPEAKING
    assert eng.turn_count == 1
    co.on_user_final()
    co.on_assistant_thinking()
    co.on_assistant_speaking()
    co.on_turn_completed()
    assert eng.current is None
    assert eng.history[-1].phase is P.TURN_COMPLETED


def test_coordinator_user_speech_is_idempotent():
    eng = TurnEngine("s")
    co = TurnCoordinator(eng)
    co.on_user_speech()
    co.on_user_speech()  # subsequent partials — no duplicate error
    co.on_user_speech()
    assert eng.current is not None and eng.current.phase is P.USER_SPEAKING


def test_coordinator_cancel():
    eng = TurnEngine("s")
    co = TurnCoordinator(eng)
    co.on_user_speech()
    co.cancel()
    assert eng.current is None and eng.history[0].phase is P.TURN_CANCELLED


def test_coordinator_two_turns():
    eng = TurnEngine("s")
    co = TurnCoordinator(eng)
    for _ in range(2):
        co.on_user_speech()
        co.on_user_final()
        co.on_assistant_thinking()
        co.on_assistant_speaking()
        co.on_turn_completed()
    assert eng.turn_count == 2
    assert [c.phase for c in eng.history] == [P.TURN_COMPLETED, P.TURN_COMPLETED]


def test_deterministic_phase_history():
    def run() -> list[P]:
        eng = TurnEngine("s")
        co = TurnCoordinator(eng)
        co.on_user_speech()
        co.on_user_final()
        co.on_assistant_thinking()
        co.on_assistant_speaking()
        co.on_turn_completed()
        return [c.phase for c in eng.history]

    assert run() == run() == [P.TURN_COMPLETED]
