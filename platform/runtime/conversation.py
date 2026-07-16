"""Conversation State Machine (M2B.1).

The deterministic, single-source-of-truth model for a conversation's lifecycle.
It operates over the Constitution's `core.states.SessionState` (the conversation
lifecycle — a Session *is* a conversation instance, §A0) and `SESSION_TRANSITIONS`;
it does NOT invent a parallel state vocabulary (DRY; one concept, one name).

This is NOT the Conversation Orchestrator. It contains no orchestration, no
providers, no AI, no I/O — only: legal states, transition validation, guards,
per-state metadata, immutable transition history, typed transition errors, and
synchronous transition notifications. Future milestones (the Orchestrator at
M2B.2, etc.) **consume** this machine instead of tracking state themselves.

Mapping of the M2B.1 example states to this model (see DL-0007):
  Created→CREATED · WaitingForAudio→CONNECTED · Completed→ENDED(reason="completed")
  · Cancelled→ENDED(reason="cancelled") · Failed→FAILED. The turn phases
  (Listening / Processing / Speaking / Interrupted) are `core.states.TurnState`,
  driven by Turn Intelligence at M3 — not conversation-lifecycle states.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from dataclasses import dataclass

from core.states import (
    SESSION_STATE_META,
    SESSION_TRANSITIONS,
    TERMINAL_SESSION_STATES,
    SessionState,
    StateMeta,
)

from .errors import (
    DuplicateConversationTransition,
    InvalidConversationTransition,
    TerminalConversationError,
)

log = logging.getLogger("voxvoice.runtime.conversation")


@dataclass(frozen=True, slots=True)
class TransitionRecord:
    """One immutable entry in the transition history. `previous` is None only for
    the initial CREATED record."""

    previous: SessionState | None
    new: SessionState
    ts: float
    reason: str
    source: str


# A transition observer is a synchronous notification sink (logging now, an
# Event Bus bridge later — the Event Bus itself is M4). It must never raise into
# the machine.
TransitionObserver = Callable[[TransitionRecord], None]


class ConversationStateMachine:
    """Deterministic conversation-lifecycle state machine. Every transition is
    explicit and validated; there are no implicit transitions and no silent
    corrections."""

    def __init__(
        self,
        *,
        on_transition: TransitionObserver | None = None,
        clock: Callable[[], float] | None = None,
    ) -> None:
        self._clock = clock or time.time
        self._on_transition = on_transition
        self._state = SessionState.CREATED
        self._history: list[TransitionRecord] = []
        # Record (and announce) the initial state; not a transition (previous=None).
        self._record(previous=None, new=SessionState.CREATED, reason="created", source="system")

    # ── queries ─────────────────────────────────────────────────────────────
    @property
    def state(self) -> SessionState:
        return self._state

    @property
    def is_terminal(self) -> bool:
        return self._state in TERMINAL_SESSION_STATES

    @property
    def history(self) -> tuple[TransitionRecord, ...]:
        """An immutable snapshot of the transition history."""
        return tuple(self._history)

    def metadata(self, state: SessionState | None = None) -> StateMeta:
        return SESSION_STATE_META[state or self._state]

    def allowed_transitions(self) -> frozenset[SessionState]:
        if self.is_terminal:
            return frozenset()
        return SESSION_TRANSITIONS[self._state]

    def can_transition(self, target: SessionState) -> bool:
        return (
            not self.is_terminal
            and target != self._state
            and target in SESSION_TRANSITIONS[self._state]
        )

    # ── the one mutating operation ──────────────────────────────────────────
    def transition(
        self, target: SessionState, *, reason: str, source: str
    ) -> TransitionRecord:
        """Perform an explicit, validated transition. Raises a typed error on any
        illegal move (terminal, duplicate, or not-in-table) — never corrects
        silently."""
        current = self._state
        if current in TERMINAL_SESSION_STATES:
            raise TerminalConversationError(
                f"{current.value} is terminal; cannot transition to {target.value}"
            )
        if target == current:
            raise DuplicateConversationTransition(
                f"already in {current.value}; duplicate transition rejected"
            )
        if target not in SESSION_TRANSITIONS[current]:
            raise InvalidConversationTransition(
                f"illegal transition {current.value} -> {target.value}"
            )
        self._state = target
        return self._record(previous=current, new=target, reason=reason, source=source)

    # ── internals ────────────────────────────────────────────────────────────
    def _record(
        self,
        *,
        previous: SessionState | None,
        new: SessionState,
        reason: str,
        source: str,
    ) -> TransitionRecord:
        record = TransitionRecord(
            previous=previous, new=new, ts=self._clock(), reason=reason, source=source
        )
        self._history.append(record)
        log.info(
            "conversation %s -> %s (reason=%s source=%s)",
            previous.value if previous else "∅",
            new.value,
            reason,
            source,
        )
        if self._on_transition is not None:
            try:
                self._on_transition(record)
            except Exception as exc:  # noqa: BLE001 — observability is best-effort
                log.warning("transition observer raised (ignored): %s", exc)
        return record
