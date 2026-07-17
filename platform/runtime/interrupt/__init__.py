"""runtime.interrupt — the Interrupt Intelligence Framework (Constitution §A4.2, M3.4).

Detects caller barge-in (user speech while the assistant is speaking), coordinates
a safe pre-emption of assistant playback, and hands off to a new user turn.
`InterruptEngine` owns the deterministic `InterruptDecision` state for a session
(sole writer), governed by a configurable `InterruptPolicy`; `InterruptCoordinator`
is the Runtime Pipeline's integration point (and its `stop_requested` signal) and
the framework's one link to the Turn Engine; `InterruptContext`/`InterruptTiming`
carry per-session data + timing; `InterruptEvent` is the internal notification
extension point (not the Event Bus, M4).

It consumes neutral signals — assistant playback state + Voice Activity speech /
silence — never provider objects. M3.4 is deterministic, policy-driven barge-in —
no semantic interruption prediction, speaker identification, wake-words, echo
cancellation, or noise suppression (later milestones).
"""

from .context import InterruptContext, InterruptTiming
from .coordinator import InterruptCoordinator
from .decision import (
    CONFIRMED_INTERRUPT,
    INTERRUPT_TRANSITIONS,
    InterruptDecision,
)
from .engine import InterruptEngine
from .events import InterruptEvent, InterruptObserver
from .policy import InterruptPolicy

__all__ = [
    "InterruptEngine",
    "InterruptPolicy",
    "InterruptCoordinator",
    "InterruptContext",
    "InterruptTiming",
    "InterruptEvent",
    "InterruptObserver",
    "InterruptDecision",
    "INTERRUPT_TRANSITIONS",
    "CONFIRMED_INTERRUPT",
]
