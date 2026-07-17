"""Endpoint decisions and their legal transitions (M3.3).

`EndpointDecision` is the deterministic model of "is the caller's turn complete?".
It sits above the Voice Activity Framework (which describes acoustic activity) and
below the Turn Engine (which owns turn state): the Endpoint Intelligence Framework
consumes activity + transcript-segment signals and produces a *decision* about
whether the current turn should finish. It never owns turn state.

The model is cyclic (one decision cycle per user turn). `ENDPOINT_CONFIRMED` and
`TIMEOUT_ENDPOINT` are the two *final* decisions — the Turn Engine is notified to
finish the turn — after which the engine normalizes back to `CONTINUE_LISTENING`
for the next turn. `CANCEL_ENDPOINT` withdraws a pending endpoint (speech resumed)
and also returns to `CONTINUE_LISTENING` within the *same* turn.

M3.3 is deterministic, provider-neutral endpointing driven by a configurable
`EndpointPolicy` — no semantic/LLM endpointing, interruptions, or double-talk
(those are later milestones).
"""

from __future__ import annotations

from enum import StrEnum


class EndpointDecision(StrEnum):
    CONTINUE_LISTENING = "continue_listening"  # the turn is still open (resting)
    POTENTIAL_ENDPOINT = "potential_endpoint"  # a possible end; holding for confirmation
    ENDPOINT_CONFIRMED = "endpoint_confirmed"  # final: the turn should finish now
    CANCEL_ENDPOINT = "cancel_endpoint"  # a pending endpoint was withdrawn (speech resumed)
    TIMEOUT_ENDPOINT = "timeout_endpoint"  # final: the turn was force-closed by max duration


# Legal decision transitions (source -> allowed targets). The model is cyclic: the
# three momentary decisions (CANCEL/CONFIRMED/TIMEOUT) return to CONTINUE_LISTENING
# to begin the next cycle. No self-loops (a repeat is a DuplicateEndpointDecision).
ENDPOINT_TRANSITIONS: dict[EndpointDecision, frozenset[EndpointDecision]] = {
    EndpointDecision.CONTINUE_LISTENING: frozenset(
        {
            EndpointDecision.POTENTIAL_ENDPOINT,
            EndpointDecision.ENDPOINT_CONFIRMED,
            EndpointDecision.TIMEOUT_ENDPOINT,
        }
    ),
    EndpointDecision.POTENTIAL_ENDPOINT: frozenset(
        {
            EndpointDecision.ENDPOINT_CONFIRMED,
            EndpointDecision.CANCEL_ENDPOINT,
            EndpointDecision.TIMEOUT_ENDPOINT,
        }
    ),
    EndpointDecision.CANCEL_ENDPOINT: frozenset({EndpointDecision.CONTINUE_LISTENING}),
    EndpointDecision.ENDPOINT_CONFIRMED: frozenset({EndpointDecision.CONTINUE_LISTENING}),
    EndpointDecision.TIMEOUT_ENDPOINT: frozenset({EndpointDecision.CONTINUE_LISTENING}),
}

# The decisions that finish a turn — the Turn Engine is notified on either.
FINAL_ENDPOINT_DECISIONS: frozenset[EndpointDecision] = frozenset(
    {EndpointDecision.ENDPOINT_CONFIRMED, EndpointDecision.TIMEOUT_ENDPOINT}
)
