"""`OutboxLadder` (M10.7, ADR-0036 RL9) — the bounded retry ladder as a **pure decision**.

Given where an entry is, how many attempts it has had, what the last attempt did and what the
policy says, this computes what should happen next. That is all it does.

**It performs no network I/O and no database I/O. It never sleeps, loops, polls, schedules,
starts a thread, recovers or dispatches.** Those are not omissions to be filled in later — they
are the reason M10.7 can exist without becoming the recovery driver, which **M10.8** owns
(ADR-0035 DW7, reaffirmed by ADR-0036 RL1). The ladder decides *what*; something else, in a later
milestone, decides *when*.

This is the shape `reasoning.RetryController` already established for provider calls — *"it
computes the backoff delay and records it; the caller decides whether to honour it"* — applied to
durable delivery. For one input the output is identical every run: no clock is read, no randomness
is drawn, and **there is no jitter** (ADR-0036 RL7), so a ladder decision is replay-safe.

Backoff comes from `core.policies.RetryPolicy` — the repository's existing, validated,
deterministic exponential backoff (1-based, capped by `max_delay_s`). M10.7 does **not** define a
second retry-policy type: two sources of truth for backoff is one too many. The delivery-tuned
configuration lives in `DELIVERY_RETRY_POLICY` below, explicitly constructed, so **no retry
constant appears inside transition logic** (ADR-0032 ED7).
"""

from __future__ import annotations

from dataclasses import dataclass

from core.policies import RetryPolicy

from .ports import DeliveryOutcome, OutboxState, OutboxTrigger

#: The delivery ladder: 5 attempts at 30s, 60s, 120s, 240s, 480s, capped at one hour
#: (ADR-0036 RL6, owner-approved values).
#:
#: Explicitly constructed and injected rather than read from a call site — ED7 forbids "hidden
#: retry constants, no magic numbers buried in a call site", and a policy passed as a parameter
#: is the difference between a tunable ladder and a hardcoded one.
#:
#: Recorded honestly: at five attempts the exponential never reaches `max_delay_s`, so the cap is
#: configured but not exercised by *this* ladder. It is real, and a policy that reaches it is
#: tested separately rather than assumed.
DELIVERY_RETRY_POLICY = RetryPolicy(
    max_attempts=5,
    base_delay_s=30.0,
    multiplier=2.0,
    max_delay_s=3600.0,
)


@dataclass(frozen=True, slots=True)
class LadderDecision:
    """What should happen to one entry. A value, not an effect — nothing here has been applied.

    `next_attempt_at` is an **absolute** instant and is set only for `RETRYING`; a terminal
    decision has no next attempt, and saying otherwise would leave a dead entry looking due.
    """

    trigger: OutboxTrigger
    state: OutboxState
    next_attempt_at: float | None = None

    @property
    def is_terminal(self) -> bool:
        return self.state is not OutboxState.RETRYING


class OutboxLadder:
    """Turns *(attempt count, outcome)* into a transition decision. Deterministic and I/O-free.

    The policy is injected (defaulting to the delivery ladder) so a caller — or a test — can bound
    the ladder differently without touching this logic, and so the numbers never live in the
    branch that uses them.
    """

    def __init__(self, policy: RetryPolicy | None = None) -> None:
        self._policy = policy or DELIVERY_RETRY_POLICY

    @property
    def policy(self) -> RetryPolicy:
        return self._policy

    def decide(
        self,
        outcome: DeliveryOutcome,
        *,
        attempt_count: int,
        now: float,
    ) -> LadderDecision:
        """The whole ladder.

        `attempt_count` is the 1-based count of attempts **already begun**, including the one
        that produced `outcome` (ADR-0036 RL8).
        """
        if outcome is DeliveryOutcome.SUCCESS:
            return LadderDecision(OutboxTrigger.DELIVER, OutboxState.DELIVERED)

        if outcome is DeliveryOutcome.PERMANENT_FAILURE:
            # ED8: retrying is known-futile, so the ladder is not entered and not consumed.
            return LadderDecision(OutboxTrigger.FAIL_PERMANENT, OutboxState.QUARANTINED)

        # Transient. ED7: bounded — infinite retry is prohibited.
        if attempt_count >= self._policy.max_attempts:
            return LadderDecision(OutboxTrigger.EXHAUST, OutboxState.DEAD_LETTERED)

        return LadderDecision(
            OutboxTrigger.FAIL_TRANSIENT,
            OutboxState.RETRYING,
            next_attempt_at=now + self._policy.delay_for(attempt_count),
        )

    def delay_for(self, attempt: int) -> float:
        """The deterministic backoff for a 1-based attempt. Never slept here."""
        return self._policy.delay_for(attempt)

    def attempts_remaining(self, attempt_count: int) -> int:
        """How many attempts the ladder still permits. Never negative."""
        return max(0, self._policy.max_attempts - attempt_count)
