"""Interrupt Intelligence Framework tests (M3.4).

Covers the deterministic barge-in decision model, the configurable policy (minimum
interrupt speech, confidence threshold, hold duration, assistant grace period,
duplicate suppression), the full action lifecycle, false-alarm cancellation,
invalid/duplicate transition guards, the coordinator's stop signal + Turn Engine
interaction, and deterministic replay.
"""

from __future__ import annotations

import pytest

from runtime.errors import (
    DuplicateInterruptDecision,
    InvalidInterruptTransition,
    RuntimeConflict,
)
from runtime.interrupt import (
    INTERRUPT_TRANSITIONS,
    InterruptCoordinator,
    InterruptDecision,
    InterruptEngine,
    InterruptPolicy,
)
from runtime.turn import TurnCoordinator, TurnEngine, TurnPhase

D = InterruptDecision


# ── default policy: first speech during playback confirms a barge-in ─────────
def test_no_interrupt_while_idle():
    eng = InterruptEngine("ses")
    assert eng.decision is D.IDLE
    # Speech with no assistant speaking is not a barge-in.
    assert eng.note_user_speech() is None
    assert eng.decision is D.IDLE


def test_assistant_started_begins_monitoring():
    eng = InterruptEngine("ses")
    assert eng.note_assistant_started() is D.ASSISTANT_SPEAKING
    # Duplicate start is a no-op (not an error).
    assert eng.note_assistant_started() is None


def test_default_policy_confirms_on_first_speech():
    eng = InterruptEngine("ses")
    eng.note_assistant_started()
    assert eng.note_user_speech() is D.INTERRUPT_CONFIRMED


def test_assistant_finished_without_interrupt_returns_idle():
    eng = InterruptEngine("ses")
    eng.note_assistant_started()
    assert eng.note_assistant_finished() is D.IDLE


# ── full action lifecycle ────────────────────────────────────────────────────
def test_full_interrupt_lifecycle():
    eng = InterruptEngine("ses")
    eng.note_assistant_started()
    assert eng.note_user_speech() is D.INTERRUPT_CONFIRMED
    assert eng.begin_stop() is D.ASSISTANT_STOPPING
    assert eng.start_user_turn() is D.USER_TURN_STARTING
    assert eng.complete() is D.INTERRUPT_COMPLETED
    assert eng.context.interrupt_count == 1
    # Next assistant utterance cycles cleanly (completed → idle on next signal).
    assert eng.note_assistant_started() is D.ASSISTANT_SPEAKING
    assert eng.context.decision is D.ASSISTANT_SPEAKING


# ── minimum interrupt speech duration ────────────────────────────────────────
def test_min_interrupt_speech_frames_gate():
    eng = InterruptEngine("ses", policy=InterruptPolicy(min_interrupt_speech_frames=3))
    eng.note_assistant_started()
    assert eng.note_user_speech() is None  # 1
    assert eng.note_user_speech() is None  # 2
    assert eng.note_user_speech() is D.INTERRUPT_CONFIRMED  # 3 → confirm


# ── interruption hold duration (potential → confirmed after hold) ────────────
def test_interrupt_hold_frames():
    eng = InterruptEngine("ses", policy=InterruptPolicy(interrupt_hold_frames=2))
    eng.note_assistant_started()
    assert eng.note_user_speech() is D.POTENTIAL_INTERRUPT  # raised, holding
    assert eng.note_user_speech() is None  # hold 1/2
    assert eng.note_user_speech() is D.INTERRUPT_CONFIRMED  # hold 2/2 → confirm


# ── assistant grace period ───────────────────────────────────────────────────
def test_assistant_grace_frames_ignored():
    eng = InterruptEngine("ses", policy=InterruptPolicy(assistant_grace_frames=2))
    eng.note_assistant_started()
    assert eng.note_user_speech() is None  # within grace (1)
    assert eng.note_user_speech() is None  # within grace (2)
    assert eng.note_user_speech() is D.INTERRUPT_CONFIRMED  # past grace → confirm


# ── confidence threshold ─────────────────────────────────────────────────────
def test_low_confidence_speech_does_not_interrupt():
    eng = InterruptEngine("ses", policy=InterruptPolicy(confidence_threshold=0.5))
    eng.note_assistant_started()
    assert eng.note_user_speech(confidence=0.2) is None  # below threshold
    assert eng.decision is D.ASSISTANT_SPEAKING
    assert eng.note_user_speech(confidence=0.9) is D.INTERRUPT_CONFIRMED


# ── false alarm: speech stops before hold elapses → cancelled, resume ────────
def test_potential_interrupt_cancelled_by_silence():
    eng = InterruptEngine("ses", policy=InterruptPolicy(interrupt_hold_frames=3))
    eng.note_assistant_started()
    assert eng.note_user_speech() is D.POTENTIAL_INTERRUPT
    assert eng.note_user_silence() is D.INTERRUPT_CANCELLED
    # Normalized back to monitoring (assistant still speaking).
    assert eng.note_user_speech() is D.POTENTIAL_INTERRUPT  # a fresh burst starts over
    assert eng.assistant_active is True


def test_assistant_finished_during_potential_drops_interrupt():
    eng = InterruptEngine("ses", policy=InterruptPolicy(interrupt_hold_frames=3))
    eng.note_assistant_started()
    assert eng.note_user_speech() is D.POTENTIAL_INTERRUPT
    assert eng.note_assistant_finished() is D.INTERRUPT_CANCELLED
    # Assistant is done → normalization returns to IDLE, not ASSISTANT_SPEAKING.
    assert eng.note_user_speech() is None
    assert eng.decision is D.IDLE


# ── duplicate suppression: extra speech after confirm does nothing ───────────
def test_duplicate_speech_after_confirm_suppressed():
    eng = InterruptEngine("ses")
    eng.note_assistant_started()
    assert eng.note_user_speech() is D.INTERRUPT_CONFIRMED
    assert eng.note_user_speech() is None  # already confirmed → suppressed
    assert eng.begin_stop() is D.ASSISTANT_STOPPING
    assert eng.note_user_speech() is None  # still suppressed during stopping


# ── reset (runtime cancellation / shutdown) ──────────────────────────────────
def test_reset_from_any_state_lands_idle():
    eng = InterruptEngine("ses")
    eng.note_assistant_started()
    eng.note_user_speech()  # confirmed
    eng.begin_stop()  # stopping
    eng.reset()
    assert eng.decision is D.IDLE
    assert eng.assistant_active is False
    eng.reset()  # idempotent
    assert eng.decision is D.IDLE


# ── transition table integrity + strict guards ──────────────────────────────
def test_transition_table_targets_are_all_known():
    for src, targets in INTERRUPT_TRANSITIONS.items():
        assert isinstance(src, InterruptDecision)
        for t in targets:
            assert t in INTERRUPT_TRANSITIONS


def test_invalid_transition_raises():
    eng = InterruptEngine("ses")
    # IDLE -> INTERRUPT_CONFIRMED is not a legal edge.
    with pytest.raises(InvalidInterruptTransition):
        eng._transition(D.INTERRUPT_CONFIRMED, 1.0)


def test_duplicate_decision_raises():
    eng = InterruptEngine("ses")
    with pytest.raises(DuplicateInterruptDecision):
        eng._transition(D.IDLE, 1.0)  # already IDLE


def test_begin_stop_without_confirm_raises():
    eng = InterruptEngine("ses")
    eng.note_assistant_started()
    with pytest.raises(InvalidInterruptTransition):
        eng.begin_stop()  # ASSISTANT_SPEAKING -> ASSISTANT_STOPPING is illegal


def test_interrupt_errors_root_in_runtime_conflict():
    assert issubclass(InvalidInterruptTransition, RuntimeConflict)
    assert issubclass(DuplicateInterruptDecision, RuntimeConflict)


# ── determinism ──────────────────────────────────────────────────────────────
def test_deterministic_replay():
    def run():
        eng = InterruptEngine("ses", policy=InterruptPolicy(interrupt_hold_frames=2))
        out = []
        eng.note_assistant_started()
        for _ in range(4):
            out.append(eng.note_user_speech())
        return out

    assert run() == run()


# ── coordinator: stop signal + Turn Engine interaction ───────────────────────
def test_coordinator_sets_stop_on_confirm_and_cancels_turn():
    turn_engine = TurnEngine("ses")
    turns = TurnCoordinator(turn_engine)
    # Simulate an active assistant turn.
    turns.on_user_speech()
    turns.on_user_final()
    turns.on_assistant_thinking()
    turns.on_assistant_speaking()
    coord = InterruptCoordinator(engine=InterruptEngine("ses"), session_id="ses", turns=turns)

    coord.on_assistant_started()
    assert coord.stop_requested.is_set() is False
    assert coord.on_user_speech() is D.INTERRUPT_CONFIRMED
    assert coord.stop_requested.is_set() is True  # playback loop is signalled
    assert coord.interrupting is True

    coord.begin_stop()
    coord.start_user_turn()  # cancels the interrupted assistant turn
    assert turn_engine.current is None  # the pre-empted turn was terminalized
    assert turn_engine.history[-1].phase is TurnPhase.TURN_CANCELLED
    coord.complete()
    assert coord.stop_requested.is_set() is False  # cleared for the next utterance
    assert coord.engine.context.interrupt_count == 1


def test_coordinator_reset_clears_stop():
    coord = InterruptCoordinator(engine=InterruptEngine("ses"), session_id="ses")
    coord.on_assistant_started()
    coord.on_user_speech()
    assert coord.stop_requested.is_set() is True
    coord.cancel()
    assert coord.stop_requested.is_set() is False
    assert coord.engine.decision is D.IDLE
