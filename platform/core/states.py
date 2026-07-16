"""Session- and turn-state definitions and legal transition tables
(Constitution §A4.4 / ADR-0003).

Definitions only — the runnable state *machine* (validation, history,
notifications) lives in `runtime.conversation` (the machine is a Runtime-Plane
concern; the definitions stay on the core floor). These tables + metadata are the
single source of truth the machine and its tests assert against.

`SessionState` IS the conversation lifecycle: the glossary (§A0) defines a
Session as "one conversation instance". The finer-grained turn phases
(listening / speaking / …) are `TurnState`, driven by Turn Intelligence (M3).
"""

from __future__ import annotations

from dataclasses import dataclass
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


@dataclass(frozen=True, slots=True)
class StateMeta:
    """Descriptive metadata for a conversation (session) state."""

    description: str
    terminal: bool
    recovery: str  # recovery policy (human-readable; enforced by the transition table)


# Terminal conversation states — no transitions out; the machine is immutable here.
TERMINAL_SESSION_STATES: frozenset[SessionState] = frozenset(
    {SessionState.ENDED, SessionState.FAILED}
)

# Per-state metadata for the conversation lifecycle. Data only; the runnable
# machine (runtime.conversation.ConversationStateMachine) consumes this.
SESSION_STATE_META: dict[SessionState, StateMeta] = {
    SessionState.CREATED: StateMeta(
        "Session created; no transport connected yet.",
        terminal=False,
        recovery="On failure -> FAILED.",
    ),
    SessionState.CONNECTED: StateMeta(
        "Transport connected; greeting / first audio pending.",
        terminal=False,
        recovery="On failure -> FAILED.",
    ),
    SessionState.IN_CONVERSATION: StateMeta(
        "Active conversation; the turn machine (TurnState) runs within this state.",
        terminal=False,
        recovery="On transport drop -> SUSPENDED (recoverable); on end -> ENDING.",
    ),
    SessionState.SUSPENDED: StateMeta(
        "Transport dropped; within the reconnect grace window (recoverable).",
        terminal=False,
        recovery="Resume -> IN_CONVERSATION within grace; on grace expiry -> ENDING.",
    ),
    SessionState.ENDING: StateMeta(
        "Teardown in progress (flush transcripts, snapshot, close providers).",
        terminal=False,
        recovery="No recovery; proceeds to ENDED.",
    ),
    SessionState.ENDED: StateMeta(
        "Terminal: conversation ended (reason carried on the transition: "
        "completed | cancelled | …).",
        terminal=True,
        recovery="None (terminal).",
    ),
    SessionState.FAILED: StateMeta(
        "Terminal: unrecoverable failure.",
        terminal=True,
        recovery="None (terminal); the owning host discards the session.",
    ),
}
