"""Endpoint Intelligence Framework tests (M3.3).

Covers the deterministic decision model, the configurable policy (minimum speech,
silence hold, minimum silence, confidence threshold, timeout), false-endpoint
prevention (cancel on resumed speech), the coordinator's Turn Engine notification,
invalid/duplicate transition guards, and deterministic replay.
"""

from __future__ import annotations

import pytest

from runtime.endpoint import (
    ENDPOINT_TRANSITIONS,
    EndpointCoordinator,
    EndpointDecision,
    EndpointEngine,
    EndpointPolicy,
)
from runtime.errors import (
    DuplicateEndpointDecision,
    InvalidEndpointTransition,
    RuntimeConflict,
)
from runtime.turn import TurnCoordinator, TurnEngine, TurnPhase

D = EndpointDecision


# ── default policy: a completed segment confirms immediately ─────────────────
def test_default_policy_confirms_on_segment():
    eng = EndpointEngine("ses")
    assert eng.decision is D.CONTINUE_LISTENING
    assert eng.note_speech() is None  # a partial keeps the turn open
    assert eng.note_segment() is D.ENDPOINT_CONFIRMED


def test_lone_segment_confirms_without_a_partial():
    # A Final with no preceding Partial still confirms (a segment implies speech).
    eng = EndpointEngine("ses")
    assert eng.note_segment() is D.ENDPOINT_CONFIRMED


def test_two_utterances_cycle_cleanly():
    eng = EndpointEngine("ses")
    assert eng.note_segment() is D.ENDPOINT_CONFIRMED
    # The next observation normalizes the confirmed decision back to listening.
    assert eng.note_speech() is None
    assert eng.decision is D.CONTINUE_LISTENING
    assert eng.note_segment() is D.ENDPOINT_CONFIRMED
    assert eng.context.turn_index == 2  # two decision cycles


# ── minimum speech duration ──────────────────────────────────────────────────
def test_min_speech_frames_gate():
    eng = EndpointEngine("ses", policy=EndpointPolicy(min_speech_frames=3))
    # one partial + one segment = 2 speech observations < 3 → keep listening
    assert eng.note_speech() is None
    assert eng.note_segment() is None
    assert eng.decision is D.CONTINUE_LISTENING
    # another partial + segment → speech run reaches the threshold → confirm
    assert eng.note_speech() is None
    assert eng.note_segment() is D.ENDPOINT_CONFIRMED


# ── silence hold: a segment opens a potential endpoint, silence confirms it ──
def test_silence_hold_confirms_via_silence():
    eng = EndpointEngine("ses", policy=EndpointPolicy(silence_hold_frames=2))
    assert eng.note_speech() is None
    assert eng.note_segment() is D.POTENTIAL_ENDPOINT  # held, not confirmed
    assert eng.note_silence() is None  # 1/2 silence frames
    assert eng.note_silence() is D.ENDPOINT_CONFIRMED  # 2/2 → confirmed


def test_min_silence_frames_also_gates_confirmation():
    eng = EndpointEngine(
        "ses", policy=EndpointPolicy(silence_hold_frames=1, min_silence_frames=3)
    )
    assert eng.note_segment() is D.POTENTIAL_ENDPOINT
    assert eng.note_silence() is None  # hold satisfied but min_silence (3) not
    assert eng.note_silence() is None
    assert eng.note_silence() is D.ENDPOINT_CONFIRMED


# ── false-endpoint prevention: resumed speech cancels a pending endpoint ─────
def test_resumed_speech_cancels_pending_endpoint():
    eng = EndpointEngine("ses", policy=EndpointPolicy(silence_hold_frames=2))
    assert eng.note_segment() is D.POTENTIAL_ENDPOINT
    assert eng.note_silence() is None  # 1/2
    assert eng.note_speech() is D.CANCEL_ENDPOINT  # speech resumed → withdraw
    # Same turn resumes; a later segment can confirm again.
    assert eng.note_segment() is D.POTENTIAL_ENDPOINT
    assert eng.note_silence() is None
    assert eng.note_silence() is D.ENDPOINT_CONFIRMED
    assert eng.context.turn_index == 1  # still the same turn (cancel is mid-turn)


# ── confidence threshold ─────────────────────────────────────────────────────
def test_low_confidence_segment_does_not_endpoint():
    eng = EndpointEngine("ses", policy=EndpointPolicy(confidence_threshold=0.5))
    assert eng.note_segment(confidence=0.2) is None  # below threshold → keep listening
    assert eng.decision is D.CONTINUE_LISTENING
    assert eng.note_segment(confidence=0.9) is D.ENDPOINT_CONFIRMED


# ── timeout: a turn may not run forever ──────────────────────────────────────
def test_timeout_endpoint_after_max_frames():
    eng = EndpointEngine("ses", policy=EndpointPolicy(min_speech_frames=99, max_turn_frames=3))
    # min_speech is unreachable, so speech alone never confirms — timeout fires.
    assert eng.note_speech() is None  # frame 1
    assert eng.note_speech() is None  # frame 2
    assert eng.note_speech() is D.TIMEOUT_ENDPOINT  # frame 3 → forced close
    assert eng.context.turn_index == 1  # one finalized turn (the timeout)
    # After a timeout the next observation starts a fresh (not-yet-finalized) turn.
    assert eng.note_speech() is None
    assert eng.context.turn_index == 1


def test_timeout_fires_while_holding_a_potential_endpoint():
    eng = EndpointEngine(
        "ses", policy=EndpointPolicy(silence_hold_frames=99, max_turn_frames=2)
    )
    assert eng.note_segment() is D.POTENTIAL_ENDPOINT  # frame 1
    assert eng.note_silence() is D.TIMEOUT_ENDPOINT  # frame 2 → forced close


# ── flush: stream ended with a segment still pending ─────────────────────────
def test_flush_confirms_a_pending_potential_endpoint():
    eng = EndpointEngine("ses", policy=EndpointPolicy(silence_hold_frames=5))
    assert eng.note_segment() is D.POTENTIAL_ENDPOINT
    assert eng.flush() is D.ENDPOINT_CONFIRMED


def test_flush_is_a_noop_while_listening():
    eng = EndpointEngine("ses")
    assert eng.flush() is None


# ── reset: clean close-out from any decision (cancellation/shutdown) ─────────
def test_reset_turn_from_potential_is_clean_and_idempotent():
    eng = EndpointEngine("ses", policy=EndpointPolicy(silence_hold_frames=2))
    assert eng.note_segment() is D.POTENTIAL_ENDPOINT
    eng.reset_turn()  # cancels the pending hold, returns to listening
    assert eng.decision is D.CONTINUE_LISTENING
    eng.reset_turn()  # idempotent — no error while already listening
    assert eng.decision is D.CONTINUE_LISTENING


# ── transition table integrity + strict guards ──────────────────────────────
def test_transition_table_targets_are_all_known():
    for src, targets in ENDPOINT_TRANSITIONS.items():
        assert isinstance(src, EndpointDecision)
        for t in targets:
            assert t in ENDPOINT_TRANSITIONS


def test_invalid_transition_raises():
    eng = EndpointEngine("ses")
    # CONTINUE_LISTENING -> CANCEL_ENDPOINT is not a legal edge.
    with pytest.raises(InvalidEndpointTransition):
        eng._transition(D.CANCEL_ENDPOINT, 1.0)


def test_duplicate_decision_raises():
    eng = EndpointEngine("ses")
    with pytest.raises(DuplicateEndpointDecision):
        eng._transition(D.CONTINUE_LISTENING, 1.0)  # already listening


def test_endpoint_errors_root_in_runtime_conflict():
    # Endpoint state-machine violations are lifecycle conflicts (like turn/VAD).
    assert issubclass(InvalidEndpointTransition, RuntimeConflict)
    assert issubclass(DuplicateEndpointDecision, RuntimeConflict)


# ── determinism ──────────────────────────────────────────────────────────────
def test_deterministic_replay():
    def run():
        eng = EndpointEngine("ses", policy=EndpointPolicy(silence_hold_frames=2))
        out = []
        for step in ("speech", "segment", "silence", "speech", "segment", "silence", "silence"):
            out.append(getattr(eng, f"note_{step}")())
        return out

    assert run() == run()


# ── coordinator notifies the Turn Engine on a final decision ────────────────
def test_coordinator_notifies_turn_engine_on_confirm():
    turn_engine = TurnEngine("ses")
    turns = TurnCoordinator(turn_engine)
    turns.on_user_speech()  # begin a turn: LISTENING -> USER_SPEAKING
    coord = EndpointCoordinator(engine=EndpointEngine("ses"), session_id="ses", turns=turns)

    decision = coord.on_segment()
    assert decision is D.ENDPOINT_CONFIRMED
    # The endpoint framework advanced the turn to USER_FINISHED (never owns it).
    assert turn_engine.current is not None
    assert turn_engine.current.phase is TurnPhase.USER_FINISHED


def test_coordinator_speech_and_silence_do_not_finish_turn():
    turn_engine = TurnEngine("ses")
    turns = TurnCoordinator(turn_engine)
    turns.on_user_speech()
    coord = EndpointCoordinator(
        engine=EndpointEngine("ses", policy=EndpointPolicy(silence_hold_frames=5)),
        session_id="ses",
        turns=turns,
    )
    coord.on_user_speech()
    assert coord.on_segment() is D.POTENTIAL_ENDPOINT
    coord.on_user_silence()  # 1/5 — not enough to confirm
    # The turn has NOT been finished (still USER_SPEAKING).
    assert turn_engine.current.phase is TurnPhase.USER_SPEAKING
