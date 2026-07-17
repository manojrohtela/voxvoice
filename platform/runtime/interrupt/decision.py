"""Interrupt decisions and their legal transitions (M3.4).

`InterruptDecision` is the deterministic model of *barge-in* — whether the caller
is interrupting the assistant while it speaks. It sits above the Voice Activity
Framework (which reports acoustic activity) and the Turn Engine (which owns turn
state): the Interrupt Intelligence Framework consumes speech-activity + assistant
playback signals and produces an interruption **decision**, then coordinates a
safe pre-emption of assistant playback. It never owns turn or conversation state.

The lifecycle is per assistant-utterance and cyclic. Detection is signal-driven up
to `INTERRUPT_CONFIRMED`; the pipeline then drives the *action* phase
(`ASSISTANT_STOPPING` → `USER_TURN_STARTING` → `INTERRUPT_COMPLETED`) as it stops
playback and hands off to a new user turn. The two momentary outcomes —
`INTERRUPT_COMPLETED` (a real interrupt ran) and `INTERRUPT_CANCELLED` (a false
alarm) — normalize back to a resting decision before the next observation.

M3.4 is deterministic, provider-neutral barge-in only: no semantic interruption
prediction, speaker identification, wake-words, echo cancellation, or noise
suppression (those are later milestones).
"""

from __future__ import annotations

from enum import StrEnum


class InterruptDecision(StrEnum):
    IDLE = "idle"  # assistant not speaking; no interruption possible
    ASSISTANT_SPEAKING = "assistant_speaking"  # assistant playing back; monitoring for barge-in
    POTENTIAL_INTERRUPT = "potential_interrupt"  # user speech detected; holding for confirmation
    INTERRUPT_CONFIRMED = "interrupt_confirmed"  # barge-in confirmed; pipeline must pre-empt
    ASSISTANT_STOPPING = "assistant_stopping"  # playback is being stopped/cancelled
    USER_TURN_STARTING = "user_turn_starting"  # interrupted turn cancelled; new user turn begins
    INTERRUPT_COMPLETED = "interrupt_completed"  # terminal-of-cycle: the interrupt ran cleanly
    INTERRUPT_CANCELLED = "interrupt_cancelled"  # the potential interrupt did not become real


# Legal decision transitions (source -> allowed targets). The model is cyclic (one
# cycle per assistant utterance); the two momentary outcomes return to a resting
# decision. No self-loops (a repeat is a DuplicateInterruptDecision).
INTERRUPT_TRANSITIONS: dict[InterruptDecision, frozenset[InterruptDecision]] = {
    InterruptDecision.IDLE: frozenset({InterruptDecision.ASSISTANT_SPEAKING}),
    InterruptDecision.ASSISTANT_SPEAKING: frozenset(
        {InterruptDecision.POTENTIAL_INTERRUPT, InterruptDecision.IDLE}
    ),
    InterruptDecision.POTENTIAL_INTERRUPT: frozenset(
        {InterruptDecision.INTERRUPT_CONFIRMED, InterruptDecision.INTERRUPT_CANCELLED}
    ),
    InterruptDecision.INTERRUPT_CONFIRMED: frozenset({InterruptDecision.ASSISTANT_STOPPING}),
    InterruptDecision.ASSISTANT_STOPPING: frozenset({InterruptDecision.USER_TURN_STARTING}),
    InterruptDecision.USER_TURN_STARTING: frozenset({InterruptDecision.INTERRUPT_COMPLETED}),
    InterruptDecision.INTERRUPT_COMPLETED: frozenset({InterruptDecision.IDLE}),
    InterruptDecision.INTERRUPT_CANCELLED: frozenset(
        {InterruptDecision.ASSISTANT_SPEAKING, InterruptDecision.IDLE}
    ),
}

# The decision that triggers the pipeline to pre-empt assistant playback.
CONFIRMED_INTERRUPT: InterruptDecision = InterruptDecision.INTERRUPT_CONFIRMED

# Momentary outcomes normalized back to a resting decision before the next signal.
_MOMENTARY: frozenset[InterruptDecision] = frozenset(
    {InterruptDecision.INTERRUPT_COMPLETED, InterruptDecision.INTERRUPT_CANCELLED}
)
