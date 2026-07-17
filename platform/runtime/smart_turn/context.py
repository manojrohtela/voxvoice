"""SmartTurnContext + SmartTurnTiming (M3.5).

Per-session Smart Turn data: identity, the last committed decision, a count, and
the most-recent entry timestamp of each decision. The object a future metrics /
coaching subsystem reads to reason about conversational timing. Timing is captured,
never processed here.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .decision import SmartTurnDecision


@dataclass(slots=True)
class SmartTurnTiming:
    """Latest entry timestamp per decision (wall clock)."""

    entered_at: dict[SmartTurnDecision, float] = field(default_factory=dict)

    def record(self, decision: SmartTurnDecision, ts: float) -> None:
        self.entered_at[decision] = ts

    def at(self, decision: SmartTurnDecision) -> float | None:
        return self.entered_at.get(decision)


@dataclass(slots=True)
class SmartTurnContext:
    """The last Smart Turn decision + timing for one session. `last_decision` is
    advanced only by the SmartTurnEngine (its sole writer)."""

    session_id: str
    last_decision: SmartTurnDecision | None = None
    decision_count: int = 0
    timing: SmartTurnTiming = field(default_factory=SmartTurnTiming)
