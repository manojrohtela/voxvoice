"""EndpointContext + EndpointTiming (M3.3).

Per-session endpoint data: identity, the current decision, and the most-recent
entry timestamp of each decision. Like the Voice Activity model the cycle repeats
per turn, so timing records the *latest* entry into each decision (overwrite). The
object a future metrics/coaching subsystem reads to reason about the current turn's
close-out. Timing is captured, never processed here.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .decision import EndpointDecision


@dataclass(slots=True)
class EndpointTiming:
    """Latest entry timestamp per decision (wall clock)."""

    entered_at: dict[EndpointDecision, float] = field(default_factory=dict)

    def record(self, decision: EndpointDecision, ts: float) -> None:
        # Cyclic model → record the latest entry into a decision (overwrite).
        self.entered_at[decision] = ts

    @property
    def potential_at(self) -> float | None:
        return self.entered_at.get(EndpointDecision.POTENTIAL_ENDPOINT)

    @property
    def confirmed_at(self) -> float | None:
        return self.entered_at.get(EndpointDecision.ENDPOINT_CONFIRMED)

    @property
    def cancelled_at(self) -> float | None:
        return self.entered_at.get(EndpointDecision.CANCEL_ENDPOINT)

    @property
    def timeout_at(self) -> float | None:
        return self.entered_at.get(EndpointDecision.TIMEOUT_ENDPOINT)


@dataclass(slots=True)
class EndpointContext:
    """The endpoint decision + timing for one session. `decision` is advanced only
    by the EndpointEngine (its sole writer). `turn_index` counts decision cycles."""

    session_id: str
    decision: EndpointDecision = EndpointDecision.CONTINUE_LISTENING
    turn_index: int = 0
    timing: EndpointTiming = field(default_factory=EndpointTiming)
