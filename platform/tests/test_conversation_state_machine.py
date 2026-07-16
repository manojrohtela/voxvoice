"""Conversation State Machine tests (M2B.1) — full transition coverage.

Covers every valid transition, every invalid transition, terminal immutability,
recovery, cancellation/completion (end-reason), the interruption boundary,
duplicate rejection, transition history + provenance, observer notifications, and
per-state metadata. No orchestration/provider/AI logic exists to test.
"""

from __future__ import annotations

import dataclasses

import pytest

from core.states import (
    SESSION_STATE_META,
    SESSION_TRANSITIONS,
    TERMINAL_SESSION_STATES,
)
from core.states import (
    SessionState as C,
)
from runtime.conversation import ConversationStateMachine
from runtime.errors import (
    DuplicateConversationTransition,
    InvalidConversationTransition,
    TerminalConversationError,
)

# A valid path to each state, so tests can position a machine anywhere.
_PATHS: dict[C, list[C]] = {
    C.CREATED: [],
    C.CONNECTED: [C.CONNECTED],
    C.IN_CONVERSATION: [C.CONNECTED, C.IN_CONVERSATION],
    C.SUSPENDED: [C.CONNECTED, C.IN_CONVERSATION, C.SUSPENDED],
    C.ENDING: [C.CONNECTED, C.IN_CONVERSATION, C.ENDING],
    C.ENDED: [C.CONNECTED, C.IN_CONVERSATION, C.ENDING, C.ENDED],
    C.FAILED: [C.FAILED],
}

_VALID_PAIRS = [(src, tgt) for src, targets in SESSION_TRANSITIONS.items() for tgt in targets]


def _machine_at(state: C) -> ConversationStateMachine:
    m = ConversationStateMachine()
    for step in _PATHS[state]:
        m.transition(step, reason="setup", source="test")
    assert m.state is state
    return m


def test_initial_state_recorded():
    m = ConversationStateMachine()
    assert m.state is C.CREATED
    assert len(m.history) == 1
    assert m.history[0].previous is None and m.history[0].new is C.CREATED


@pytest.mark.parametrize("src, tgt", _VALID_PAIRS)
def test_every_valid_transition(src, tgt):
    m = _machine_at(src)
    rec = m.transition(tgt, reason="ok", source="test")
    assert m.state is tgt
    assert rec.previous is src and rec.new is tgt


def test_every_invalid_transition_raises():
    for src in SESSION_TRANSITIONS:
        for tgt in C:
            if tgt == src or tgt in SESSION_TRANSITIONS[src]:
                continue  # self and legal targets tested elsewhere
            m = _machine_at(src)
            if src in TERMINAL_SESSION_STATES:
                with pytest.raises(TerminalConversationError):
                    m.transition(tgt, reason="x", source="test")
            else:
                with pytest.raises(InvalidConversationTransition):
                    m.transition(tgt, reason="x", source="test")


def test_terminal_states_are_immutable():
    for term in TERMINAL_SESSION_STATES:
        m = _machine_at(term)
        assert m.is_terminal
        assert m.allowed_transitions() == frozenset()
        with pytest.raises(TerminalConversationError):
            m.transition(C.CONNECTED, reason="x", source="test")


def test_duplicate_transition_rejected():
    m = _machine_at(C.IN_CONVERSATION)
    with pytest.raises(DuplicateConversationTransition):
        m.transition(C.IN_CONVERSATION, reason="dup", source="test")


def test_recovery_transition_is_explicit():
    m = _machine_at(C.SUSPENDED)
    rec = m.transition(C.IN_CONVERSATION, reason="reconnected", source="transport")
    assert m.state is C.IN_CONVERSATION and rec.reason == "reconnected"


def test_cancellation_carries_reason_not_a_separate_state():
    m = _machine_at(C.IN_CONVERSATION)
    m.transition(C.ENDING, reason="cancelled", source="consumer")
    m.transition(C.ENDED, reason="cancelled", source="system")
    assert m.is_terminal and m.state is C.ENDED
    assert any(r.reason == "cancelled" for r in m.history)


def test_completion_flow():
    m = _machine_at(C.IN_CONVERSATION)
    m.transition(C.ENDING, reason="completed", source="orchestrator")
    m.transition(C.ENDED, reason="completed", source="system")
    assert m.state is C.ENDED


def test_interruption_is_turn_level_not_a_conversation_state():
    # Barge-in is a TurnState concern (M3). The conversation machine has no
    # 'interrupted' state and stays IN_CONVERSATION across interruptions.
    assert not hasattr(C, "INTERRUPTED")
    m = _machine_at(C.IN_CONVERSATION)
    assert m.state is C.IN_CONVERSATION


def test_history_snapshot_is_immutable():
    m = _machine_at(C.CONNECTED)
    snapshot = m.history
    m.transition(C.IN_CONVERSATION, reason="x", source="test")
    assert len(snapshot) == 2  # tuple snapshot unaffected by later transitions
    assert len(m.history) == 3
    with pytest.raises(dataclasses.FrozenInstanceError):
        snapshot[0].new = C.FAILED  # records are frozen


def test_transition_records_full_provenance():
    m = ConversationStateMachine()
    rec = m.transition(C.CONNECTED, reason="connected", source="transport")
    assert rec.previous is C.CREATED and rec.new is C.CONNECTED
    assert rec.reason == "connected" and rec.source == "transport" and rec.ts > 0


def test_observer_receives_every_transition():
    seen: list[tuple] = []
    m = ConversationStateMachine(on_transition=lambda r: seen.append((r.previous, r.new)))
    m.transition(C.CONNECTED, reason="x", source="test")
    m.transition(C.IN_CONVERSATION, reason="x", source="test")
    assert seen[0] == (None, C.CREATED)  # initial announcement
    assert (C.CREATED, C.CONNECTED) in seen
    assert (C.CONNECTED, C.IN_CONVERSATION) in seen


def test_observer_errors_do_not_break_the_machine():
    def boom(_record) -> None:
        raise RuntimeError("observer boom")

    m = ConversationStateMachine(on_transition=boom)
    # Must not raise despite the observer raising.
    m.transition(C.CONNECTED, reason="x", source="test")
    assert m.state is C.CONNECTED


def test_metadata_terminal_flags_match_table():
    for s in C:
        meta = SESSION_STATE_META[s]
        assert meta.description and meta.recovery
        assert meta.terminal == (s in TERMINAL_SESSION_STATES)


def test_can_transition_and_allowed_transitions():
    m = _machine_at(C.CONNECTED)
    assert m.can_transition(C.IN_CONVERSATION) is True
    assert m.can_transition(C.ENDED) is False
    assert m.can_transition(C.CONNECTED) is False  # duplicate
    assert m.allowed_transitions() == SESSION_TRANSITIONS[C.CONNECTED]
