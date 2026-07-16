"""Session- and turn-state definitions and legal transition tables
(Constitution §A4.4 / ADR-0003).

Definitions only — the state *machine* (the component that runs these tables and
emits transition events) is part of the Conversation Orchestrator, built at M2+.
The transition tables here are the single source of truth the orchestrator and
its tests assert against (TESTING_POLICY: state-machine tests).
"""

from __future__ import annotations

from enum import StrEnum


class SessionState(StrEnum):
    CREATED = "created"
    CONNECTED = "connected"
    IN_CONVERSATION = "in_conversation"
    SUSPENDED = "suspended"
    ENDING = "ending"
    ENDED = "ended"
    FAILED = "failed"


class TurnState(StrEnum):
    LISTENING = "listening"
    USER_SPEAKING = "user_speaking"
    ENDPOINTING = "endpointing"
    THINKING = "thinking"
    TOOL_WAIT = "tool_wait"
    SPEAKING = "speaking"


# Legal session transitions (source -> allowed targets). Illegal transitions
# raise RuntimeConflict once the orchestrator runs these (AR-5, AR-6).
SESSION_TRANSITIONS: dict[SessionState, frozenset[SessionState]] = {
    SessionState.CREATED: frozenset({SessionState.CONNECTED, SessionState.FAILED}),
    SessionState.CONNECTED: frozenset({SessionState.IN_CONVERSATION, SessionState.FAILED}),
    SessionState.IN_CONVERSATION: frozenset({SessionState.SUSPENDED, SessionState.ENDING}),
    SessionState.SUSPENDED: frozenset({SessionState.IN_CONVERSATION, SessionState.ENDING}),
    SessionState.ENDING: frozenset({SessionState.ENDED}),
    SessionState.ENDED: frozenset(),
    SessionState.FAILED: frozenset(),
}

# Legal turn transitions (inside IN_CONVERSATION). Barge-in and timeout edges are
# encoded by the orchestrator's policy tables (M2+); this is the base graph.
TURN_TRANSITIONS: dict[TurnState, frozenset[TurnState]] = {
    TurnState.LISTENING: frozenset({TurnState.USER_SPEAKING}),
    TurnState.USER_SPEAKING: frozenset({TurnState.ENDPOINTING, TurnState.LISTENING}),
    TurnState.ENDPOINTING: frozenset({TurnState.THINKING, TurnState.USER_SPEAKING}),
    TurnState.THINKING: frozenset({TurnState.TOOL_WAIT, TurnState.SPEAKING}),
    TurnState.TOOL_WAIT: frozenset({TurnState.THINKING}),
    TurnState.SPEAKING: frozenset({TurnState.LISTENING, TurnState.USER_SPEAKING}),
}
