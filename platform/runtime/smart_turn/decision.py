"""Smart Turn decisions (M3.5).

`SmartTurnDecision` is the *final conversational timing decision* — the single
output the Runtime Pipeline consumes when Smart Turn is active. The Smart Turn
Engine combines the sub-frameworks' signals (Voice Activity, Endpoint, Interrupt,
Turn Engine + runtime timing) and a configurable policy into one of these; the
sub-frameworks keep owning their own state, Smart Turn only *arbitrates*.

These are arbitration outputs, not a lifecycle state machine (unlike the Turn /
Voice Activity / Endpoint / Interrupt models), so there is no transition table — a
decision is a deterministic function of the current signal snapshot + policy +
(optional) semantic hint. Extensible: new decisions are additive (Constitution
§A12) and require no change to existing arbitration.
"""

from __future__ import annotations

from enum import StrEnum


class SmartTurnDecision(StrEnum):
    WAIT = "wait"  # hold; not enough signal to act (e.g. within adaptive grace)
    CONTINUE_LISTENING = "continue_listening"  # the user turn is still open
    FINISH_TURN = "finish_turn"  # the user turn is complete → respond now
    CANCEL_ENDPOINT = "cancel_endpoint"  # withdraw a pending endpoint (user resumed)
    DELAY_RESPONSE = "delay_response"  # endpoint reached, but hold the reply (adaptive silence)
    CONFIRM_INTERRUPT = "confirm_interrupt"  # a barge-in should pre-empt the assistant
    IGNORE_INTERRUPT = "ignore_interrupt"  # a barge-in is below threshold → keep speaking
    RESUME_ASSISTANT = "resume_assistant"  # a false/withdrawn interrupt → resume playback


# The decisions that tell the pipeline to produce/allow the assistant response.
FINISH_DECISIONS: frozenset[SmartTurnDecision] = frozenset(
    {SmartTurnDecision.FINISH_TURN, SmartTurnDecision.DELAY_RESPONSE}
)
# The decisions that tell the pipeline to pre-empt assistant playback.
PREEMPT_DECISIONS: frozenset[SmartTurnDecision] = frozenset(
    {SmartTurnDecision.CONFIRM_INTERRUPT}
)
