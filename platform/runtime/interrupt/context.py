"""InterruptContext + InterruptTiming (M3.4).

Per-session interrupt data: identity, the current decision, and the most-recent
entry timestamp of each decision. Like the Voice Activity / Endpoint models the
cycle repeats per assistant utterance, so timing records the *latest* entry into
each decision (overwrite). The object a future metrics/coaching subsystem reads to
reason about barge-in latency. Timing is captured, never processed here.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .decision import InterruptDecision


@dataclass(slots=True)
class InterruptTiming:
    """Latest entry timestamp per decision (wall clock)."""

    entered_at: dict[InterruptDecision, float] = field(default_factory=dict)

    def record(self, decision: InterruptDecision, ts: float) -> None:
        # Cyclic model → record the latest entry into a decision (overwrite).
        self.entered_at[decision] = ts

    @property
    def assistant_speaking_at(self) -> float | None:
        return self.entered_at.get(InterruptDecision.ASSISTANT_SPEAKING)

    @property
    def potential_at(self) -> float | None:
        return self.entered_at.get(InterruptDecision.POTENTIAL_INTERRUPT)

    @property
    def confirmed_at(self) -> float | None:
        return self.entered_at.get(InterruptDecision.INTERRUPT_CONFIRMED)

    @property
    def completed_at(self) -> float | None:
        return self.entered_at.get(InterruptDecision.INTERRUPT_COMPLETED)

    @property
    def cancelled_at(self) -> float | None:
        return self.entered_at.get(InterruptDecision.INTERRUPT_CANCELLED)


@dataclass(slots=True)
class InterruptContext:
    """The interrupt decision + timing for one session. `decision` is advanced only
    by the InterruptEngine (its sole writer). `interrupt_count` counts confirmed
    barge-ins (completed interrupt cycles)."""

    session_id: str
    decision: InterruptDecision = InterruptDecision.IDLE
    interrupt_count: int = 0
    timing: InterruptTiming = field(default_factory=InterruptTiming)
