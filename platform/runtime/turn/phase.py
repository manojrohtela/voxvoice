"""Turn-lifecycle phases and legal transitions (M3.1).

`TurnPhase` is the **per-turn lifecycle** — a coarse, deterministic model of one
user-utterance → assistant-response cycle. It is distinct from (and complementary
to) the Constitution's finer `core.states.TurnState` (which adds ENDPOINTING and
TOOL_WAIT): those speech-processing states plug into this lifecycle as VAD /
endpoint detection / tools land at M3.2+. Reconciliation of the two is a future
ADR (DL-0012). Kept in `runtime/turn/` — a runtime-internal state definition,
like `runtime.state.WorkerState`.

M3.1 is the framework only: no VAD, no endpoint/semantic detection, no
interruptions. A turn advances only when the pipeline notifies it of a lifecycle
event.
"""

from __future__ import annotations

from enum import StrEnum


class TurnPhase(StrEnum):
    IDLE = "idle"  # turn created; nothing has happened yet
    LISTENING = "listening"  # waiting for the caller's next utterance
    USER_SPEAKING = "user_speaking"  # caller audio / partial transcripts arriving
    USER_FINISHED = "user_finished"  # the utterance is complete (a Final)
    ASSISTANT_THINKING = "assistant_thinking"  # the assistant is producing a response
    ASSISTANT_SPEAKING = "assistant_speaking"  # the assistant's response is being played
    TURN_COMPLETED = "turn_completed"  # terminal: the turn finished normally
    TURN_CANCELLED = "turn_cancelled"  # terminal: the turn was cancelled/aborted


# Legal turn transitions (source -> allowed targets). Every non-terminal phase can
# be cancelled (shutdown, session cancel). ASSISTANT_THINKING may complete directly
# (an empty/no-op response speaks nothing). Terminal phases have no exits.
TURN_TRANSITIONS: dict[TurnPhase, frozenset[TurnPhase]] = {
    TurnPhase.IDLE: frozenset({TurnPhase.LISTENING, TurnPhase.TURN_CANCELLED}),
    TurnPhase.LISTENING: frozenset({TurnPhase.USER_SPEAKING, TurnPhase.TURN_CANCELLED}),
    TurnPhase.USER_SPEAKING: frozenset({TurnPhase.USER_FINISHED, TurnPhase.TURN_CANCELLED}),
    TurnPhase.USER_FINISHED: frozenset(
        {TurnPhase.ASSISTANT_THINKING, TurnPhase.TURN_CANCELLED}
    ),
    TurnPhase.ASSISTANT_THINKING: frozenset(
        {TurnPhase.ASSISTANT_SPEAKING, TurnPhase.TURN_COMPLETED, TurnPhase.TURN_CANCELLED}
    ),
    TurnPhase.ASSISTANT_SPEAKING: frozenset(
        {TurnPhase.TURN_COMPLETED, TurnPhase.TURN_CANCELLED}
    ),
    TurnPhase.TURN_COMPLETED: frozenset(),
    TurnPhase.TURN_CANCELLED: frozenset(),
}

TERMINAL_TURN_PHASES: frozenset[TurnPhase] = frozenset(
    {TurnPhase.TURN_COMPLETED, TurnPhase.TURN_CANCELLED}
)
