"""Deterministic finite-state-machine framework (M5.0).

The single reusable FSM every runtime module builds on — replacing the hand-written
transition tables + momentary-normalization repeated across the Turn Intelligence
engines (this framework is *created* at M5.0; migrating those engines is later).

Design invariants (carried from the Turn/Endpoint/Interrupt engines, generalized):
- **Deterministic:** exactly one `(state, trigger) → target`; an injected clock; a
  guard/action model with no hidden state. Same triggers → same states + history.
- **Validated:** the definition rejects duplicate `(state, trigger)` edges; firing an
  undefined edge, a rejected guard, or leaving a terminal state raises a typed error
  (rooted in `core.errors`). No silent transitions.
- **Observable:** an immutable `history` of `TransitionRecord`s + an optional
  `on_transition` observer receiving a `TransitionEvent`.
- **Replayable:** `replay(triggers)` re-runs a fresh machine from the initial state.
- **Async-ready:** guards/actions are synchronous here (deterministic core); an async
  variant subclasses `StateMachine` and overrides `_run_action` to await — the table,
  validation, history, and events are reused unchanged.

Generic over the state type `S` and trigger type `Tr` (both hashable — typically a
`StrEnum`). Owns FSM mechanics only: no domain logic, no I/O.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Hashable, Iterable
from dataclasses import dataclass
from typing import Generic, TypeVar

from core.errors import PlatformError, RuntimeConflict

S = TypeVar("S", bound=Hashable)
Tr = TypeVar("Tr", bound=Hashable)

# Guard: may this transition fire given a context? Action: side-effect on transition.
Guard = Callable[[object], bool]
Action = Callable[[object], None]


# ── errors (rooted in the platform hierarchy) ───────────────────────────────
class FSMError(RuntimeConflict):
    """Base for state-machine violations (a lifecycle conflict)."""


class InvalidTransition(FSMError):
    """No transition is defined for `(state, trigger)`."""


class GuardRejected(FSMError):
    """A defined transition's guard returned False."""


class TerminalStateError(FSMError):
    """A trigger was fired from a terminal state (no outgoing edges)."""


class DuplicateTransition(PlatformError):
    """Two transitions share the same `(state, trigger)` — non-deterministic."""


# ── value types ──────────────────────────────────────────────────────────────
@dataclass(frozen=True, slots=True)
class Transition(Generic[S, Tr]):
    source: S
    trigger: Tr
    target: S
    guard: Guard | None = None
    action: Action | None = None


@dataclass(frozen=True, slots=True)
class TransitionRecord(Generic[S, Tr]):
    """One executed transition — the immutable audit unit of `history`."""

    seq: int
    ts: float
    source: S
    trigger: Tr
    target: S


@dataclass(frozen=True, slots=True)
class TransitionEvent(Generic[S, Tr]):
    """Delivered to the `on_transition` observer (not the Event Bus — a bridge maps
    it, exactly like the engine observers do today)."""

    machine_id: str
    seq: int
    ts: float
    source: S
    trigger: Tr
    target: S


TransitionObserver = Callable[[TransitionEvent], None]


# ── definition (validated, reusable, immutable once built) ─────────────────
class StateMachineDefinition(Generic[S, Tr]):
    """A validated transition table. Build once, instantiate many `StateMachine`s."""

    def __init__(self) -> None:
        self._edges: dict[tuple[S, Tr], Transition[S, Tr]] = {}
        self._states: set[S] = set()

    def add(
        self,
        source: S,
        trigger: Tr,
        target: S,
        *,
        guard: Guard | None = None,
        action: Action | None = None,
    ) -> StateMachineDefinition[S, Tr]:
        key = (source, trigger)
        if key in self._edges:
            raise DuplicateTransition(f"duplicate transition {source!r} --{trigger!r}-->")
        self._edges[key] = Transition(source, trigger, target, guard, action)
        self._states.update((source, target))
        return self  # chainable

    @property
    def states(self) -> frozenset[S]:
        return frozenset(self._states)

    def terminal_states(self) -> frozenset[S]:
        """States with no outgoing edge."""
        with_out = {s for (s, _t) in self._edges}
        return frozenset(self._states - with_out)

    def get(self, source: S, trigger: Tr) -> Transition[S, Tr] | None:
        return self._edges.get((source, trigger))

    def triggers_from(self, source: S) -> frozenset[Tr]:
        return frozenset(t for (s, t) in self._edges if s == source)


# ── the machine ──────────────────────────────────────────────────────────────
class StateMachine(Generic[S, Tr]):
    """A deterministic FSM instance over a validated definition."""

    def __init__(
        self,
        definition: StateMachineDefinition[S, Tr],
        initial: S,
        *,
        machine_id: str = "fsm",
        clock: Callable[[], float] | None = None,
        on_transition: TransitionObserver | None = None,
    ) -> None:
        self._def = definition
        self._initial = initial
        self._state = initial
        self._id = machine_id
        self._clock = clock or time.time
        self._on = on_transition
        self._history: list[TransitionRecord[S, Tr]] = []
        self._seq = 0

    # ── read-only exposure ──────────────────────────────────────────────────
    @property
    def state(self) -> S:
        return self._state

    @property
    def history(self) -> tuple[TransitionRecord[S, Tr], ...]:
        return tuple(self._history)

    @property
    def is_terminal(self) -> bool:
        return not self._def.triggers_from(self._state)

    def available(self) -> frozenset[Tr]:
        return self._def.triggers_from(self._state)

    def can(self, trigger: Tr, ctx: object | None = None) -> bool:
        """True if `trigger` has a defined edge here whose guard (if any) admits `ctx`."""
        edge = self._def.get(self._state, trigger)
        if edge is None:
            return False
        return edge.guard is None or bool(edge.guard(ctx))

    # ── firing (sole writer of state) ───────────────────────────────────────
    def fire(self, trigger: Tr, ctx: object | None = None) -> S:
        """Advance on `trigger`. Validates edge + guard, runs the action, records
        history, emits the transition event. Raises (never silent) on any violation."""
        if self.is_terminal:
            raise TerminalStateError(f"state {self._state!r} is terminal; cannot fire {trigger!r}")
        edge = self._def.get(self._state, trigger)
        if edge is None:
            raise InvalidTransition(f"no transition {self._state!r} --{trigger!r}-->")
        if edge.guard is not None and not edge.guard(ctx):
            raise GuardRejected(f"guard rejected {self._state!r} --{trigger!r}--> {edge.target!r}")
        source = self._state
        self._run_action(edge, ctx)
        self._state = edge.target
        self._seq += 1
        ts = self._clock()
        self._history.append(TransitionRecord(self._seq, ts, source, trigger, edge.target))
        if self._on is not None:
            try:
                self._on(TransitionEvent(self._id, self._seq, ts, source, trigger, edge.target))
            except Exception:  # noqa: BLE001 — observability is best-effort
                pass
        return self._state

    def _run_action(self, edge: Transition[S, Tr], ctx: object | None) -> None:
        """Run the transition action. Overridden by an async subclass to await."""
        if edge.action is not None:
            edge.action(ctx)

    # ── replay + reset ──────────────────────────────────────────────────────
    def reset(self) -> None:
        self._state = self._initial
        self._history.clear()
        self._seq = 0

    def replay(self, triggers: Iterable[Tr], ctx: object | None = None) -> tuple[S, ...]:
        """Deterministically re-run `triggers` from the initial state; return the
        state after each. Pure w.r.t. the definition + clock."""
        self.reset()
        out: list[S] = []
        for t in triggers:
            out.append(self.fire(t, ctx))
        return tuple(out)


# a convenience for building definitions inline
def define(edges: Iterable[tuple]) -> StateMachineDefinition:
    """Build a definition from `(source, trigger, target[, guard[, action]])` tuples."""
    d: StateMachineDefinition = StateMachineDefinition()
    for e in edges:
        src, trig, tgt = e[0], e[1], e[2]
        guard = e[3] if len(e) > 3 else None
        action = e[4] if len(e) > 4 else None
        d.add(src, trig, tgt, guard=guard, action=action)
    return d
