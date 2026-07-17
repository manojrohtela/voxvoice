"""TurnLifecycle — the deterministic state machine for ONE turn (M3.1).

Analogous to `runtime.conversation.ConversationStateMachine`, but for a turn.
Validates every transition against `TURN_TRANSITIONS`, records phase-entry timing
into the turn's `TurnContext`, and emits a `TurnEvent` per transition. No implicit
transitions, no silent corrections.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable

from ..errors import (
    DuplicateTurnTransition,
    InvalidTurnTransition,
    TerminalTurnError,
)
from .context import TurnContext
from .events import TurnEvent, TurnObserver
from .phase import TERMINAL_TURN_PHASES, TURN_TRANSITIONS, TurnPhase

log = logging.getLogger("voxvoice.runtime.turn")


class TurnLifecycle:
    """Owns and advances one turn's phase. The `TurnContext` it wraps is the
    turn's data; this class is the only writer of `context.phase`."""

    def __init__(
        self,
        context: TurnContext,
        *,
        on_event: TurnObserver | None = None,
        clock: Callable[[], float] | None = None,
    ) -> None:
        self._context = context
        self._on_event = on_event
        self._clock = clock or time.time
        # Record (and announce) the initial IDLE phase; not a transition.
        self._record(previous=None, phase=context.phase)

    @property
    def context(self) -> TurnContext:
        return self._context

    @property
    def phase(self) -> TurnPhase:
        return self._context.phase

    @property
    def is_terminal(self) -> bool:
        return self._context.phase in TERMINAL_TURN_PHASES

    def can_transition(self, target: TurnPhase) -> bool:
        return (
            not self.is_terminal
            and target != self._context.phase
            and target in TURN_TRANSITIONS[self._context.phase]
        )

    def transition(self, target: TurnPhase) -> None:
        """Advance to `target`. Raises a typed error on any illegal move
        (terminal, duplicate, or not-in-table) — never corrects silently."""
        current = self._context.phase
        if current in TERMINAL_TURN_PHASES:
            raise TerminalTurnError(
                f"turn phase {current.value} is terminal; cannot go to {target.value}"
            )
        if target == current:
            raise DuplicateTurnTransition(
                f"turn already in {current.value}; duplicate transition rejected"
            )
        if target not in TURN_TRANSITIONS[current]:
            raise InvalidTurnTransition(
                f"illegal turn transition {current.value} -> {target.value}"
            )
        self._context.phase = target
        self._record(previous=current, phase=target)

    def _record(self, *, previous: TurnPhase | None, phase: TurnPhase) -> None:
        ts = self._clock()
        self._context.timing.record(phase, ts)
        log.debug(
            "turn %s#%d: %s -> %s",
            self._context.session_id,
            self._context.turn_index,
            previous.value if previous else "∅",
            phase.value,
        )
        if self._on_event is None:
            return
        event = TurnEvent(
            session_id=self._context.session_id,
            turn_index=self._context.turn_index,
            previous=previous,
            phase=phase,
            ts=ts,
        )
        try:
            self._on_event(event)
        except Exception as exc:  # noqa: BLE001 — observability is best-effort
            log.warning("turn observer raised (ignored): %s", exc)
