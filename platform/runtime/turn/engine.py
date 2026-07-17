"""TurnEngine — the session-scoped owner of turn lifecycle (M3.1).

Owns the sequence of turns for one conversation: the current turn (a
`TurnLifecycle`) and the history of completed/cancelled `TurnContext`s. It is the
SOLE writer of turn state. It does NOT own conversation state (the Orchestrator
does) or worker state (the SessionWorker does) — it is driven by lifecycle
notifications (via a `TurnCoordinator`) and never touches speech, providers, or
conversation transitions.

Framework only (M3.1): a turn advances only when notified. VAD, endpoint
detection, and the interrupt engine are later milestones that will feed this
engine without changing its shape.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable

from ..errors import NoActiveTurnError
from .context import TurnContext
from .events import TurnObserver
from .lifecycle import TurnLifecycle
from .phase import TurnPhase

log = logging.getLogger("voxvoice.runtime.turn")


class TurnEngine:
    def __init__(
        self,
        session_id: str,
        *,
        on_event: TurnObserver | None = None,
        clock: Callable[[], float] | None = None,
    ) -> None:
        self._session_id = session_id
        self._on_event = on_event
        self._clock = clock or time.time
        self._current: TurnLifecycle | None = None
        self._history: list[TurnContext] = []
        self._turn_index = 0

    # ── read-only exposure ──────────────────────────────────────────────────
    @property
    def current(self) -> TurnLifecycle | None:
        return self._current

    @property
    def current_context(self) -> TurnContext | None:
        return self._current.context if self._current is not None else None

    @property
    def history(self) -> tuple[TurnContext, ...]:
        """Immutable snapshot of completed/cancelled turns, in order."""
        return tuple(self._history)

    @property
    def turn_count(self) -> int:
        """Turns started so far (including the active one)."""
        return self._turn_index

    # ── turn management (sole writer of turn state) ─────────────────────────
    def begin_turn(self) -> TurnContext:
        """Start a new turn (IDLE → LISTENING). If a previous turn is still
        active it is cancelled first (defensive; the coordinator normally
        completes a turn before beginning the next)."""
        if self._current is not None and not self._current.is_terminal:
            log.debug("begin_turn: cancelling incomplete turn %d", self._turn_index)
            self._current.transition(TurnPhase.TURN_CANCELLED)
            self._history.append(self._current.context)
        self._turn_index += 1
        context = TurnContext(session_id=self._session_id, turn_index=self._turn_index)
        self._current = TurnLifecycle(context, on_event=self._on_event, clock=self._clock)
        self._current.transition(TurnPhase.LISTENING)
        return context

    def advance(self, target: TurnPhase) -> None:
        """Advance the active turn to `target`. Archives the turn when it reaches
        a terminal phase. Raises if there is no active turn."""
        if self._current is None:
            raise NoActiveTurnError(f"no active turn to advance to {target.value}")
        self._current.transition(target)
        if self._current.is_terminal:
            self._history.append(self._current.context)
            self._current = None

    def cancel_current(self) -> None:
        """Cancel the active turn (idempotent — no-op if none/terminal)."""
        if self._current is not None and not self._current.is_terminal:
            self._current.transition(TurnPhase.TURN_CANCELLED)
            self._history.append(self._current.context)
            self._current = None
