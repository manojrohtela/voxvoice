"""Memory record lifecycle (M5.1B-1) — built on `core.fsm`.

The one authority for how a record moves between `MemoryState`s. It reuses the shared
deterministic FSM framework (no hand-written transition logic, per M5.0): illegal
moves raise typed `FSMError`s rather than silently corrupting state.

```
CREATED ─store→ STORED ─activate→ ACTIVE ─archive→ ARCHIVED ─expire→ EXPIRED ─delete→ DELETED
                                    └──────────── delete ───────────────────────────────┘
                                                   (ARCHIVED ─delete→ DELETED)
```

`advance()` validates a single transition on an immutable record and returns the
record in its new state — records are serialized/immutable, so no live machine is
kept per record; a fresh one is instantiated at the record's current state to check
the move. `EXPIRED`/`ARCHIVED` handling itself (TTL, capacity) is a later milestone —
only the legal edges are declared here.
"""

from __future__ import annotations

from collections.abc import Callable
from enum import StrEnum

from core.fsm import StateMachine, StateMachineDefinition

from .domain import MemoryRecord, MemoryState


class MemoryTrigger(StrEnum):
    """The lifecycle triggers (verbs that move a record)."""

    STORE = "store"
    ACTIVATE = "activate"
    ARCHIVE = "archive"
    EXPIRE = "expire"
    DELETE = "delete"


def _lifecycle_def() -> StateMachineDefinition[MemoryState, MemoryTrigger]:
    d: StateMachineDefinition[MemoryState, MemoryTrigger] = StateMachineDefinition()
    d.add(MemoryState.CREATED, MemoryTrigger.STORE, MemoryState.STORED)
    d.add(MemoryState.STORED, MemoryTrigger.ACTIVATE, MemoryState.ACTIVE)
    d.add(MemoryState.ACTIVE, MemoryTrigger.ARCHIVE, MemoryState.ARCHIVED)
    d.add(MemoryState.ARCHIVED, MemoryTrigger.EXPIRE, MemoryState.EXPIRED)
    d.add(MemoryState.EXPIRED, MemoryTrigger.DELETE, MemoryState.DELETED)
    # explicit removal ("forget") from either live resting state
    d.add(MemoryState.ACTIVE, MemoryTrigger.DELETE, MemoryState.DELETED)
    d.add(MemoryState.ARCHIVED, MemoryTrigger.DELETE, MemoryState.DELETED)
    return d


# One validated definition, shared by every record instance (built once).
LIFECYCLE: StateMachineDefinition[MemoryState, MemoryTrigger] = _lifecycle_def()


def machine_for(
    state: MemoryState,
    *,
    machine_id: str = "memory.record",
    clock: Callable[[], float] | None = None,
) -> StateMachine[MemoryState, MemoryTrigger]:
    """A record-lifecycle machine seeded at `state` (for validation/inspection)."""
    return StateMachine(LIFECYCLE, state, machine_id=machine_id, clock=clock)


def advance(
    record: MemoryRecord,
    trigger: MemoryTrigger,
    *,
    clock: Callable[[], float] | None = None,
) -> MemoryRecord:
    """Validate `trigger` against the record's current state and return the record in
    its resulting state. Raises `FSMError` (typed) on an illegal transition."""
    fsm = machine_for(record.state, machine_id=str(record.id), clock=clock)
    new_state = fsm.fire(trigger)
    return record.with_state(new_state)
