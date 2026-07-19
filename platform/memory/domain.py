"""Memory domain model (M5.1B-1) — immutable value objects.

The domain vocabulary the Memory Runtime manipulates. `MemoryTier`, `MemoryScope`,
`MemoryQuery`, and `MemoryHit` are **reused from `core.ports.memory`** (the port other
runtimes depend on) — never redefined here. This module adds the record aggregate and
its descriptors: `MemoryId`, `MemoryType`, `MemoryState`, `MemoryMetadata`,
`MemoryRecord`, `MemoryFilter`, `MemoryResult`.

Everything is frozen + slotted: deterministic, cheap, replay-friendly. No behaviour
beyond pure derivations; no storage, no ranking, no policy (all later milestones).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field, replace
from enum import StrEnum

# Re-export the port DTOs so the domain has one import surface (do NOT redefine them).
from core.ports.memory import MemoryHit, MemoryQuery, MemoryScope, MemoryTier

__all__ = [
    "MemoryId",
    "MemoryType",
    "MemoryState",
    "MemoryTier",
    "MemoryScope",
    "MemoryQuery",
    "MemoryHit",
    "MemoryMetadata",
    "MemoryRecord",
    "MemoryFilter",
    "MemoryResult",
]


@dataclass(frozen=True, slots=True)
class MemoryId:
    """A memory's identity. A thin, hashable wrapper over the id string so the domain
    never passes bare strings around (and can evolve the id scheme without churn)."""

    value: str

    def __str__(self) -> str:
        return self.value

    @classmethod
    def new(cls, factory: Callable[[], str]) -> MemoryId:
        """Mint a new id from an injected factory (deterministic under a fake one)."""
        return cls(factory())


class MemoryType(StrEnum):
    """What a memory *is*, independent of which tier stores it. Descriptive only —
    no behaviour keys off this in M5.1B-1."""

    NOTE = "note"  # generic/manual entry
    UTTERANCE = "utterance"  # a turn of dialogue
    FACT = "fact"  # an asserted fact
    SUMMARY = "summary"  # a condensed rollup
    PREFERENCE = "preference"  # a user/agent preference
    TOOL_RESULT = "tool_result"  # output of a tool call
    EVENT = "event"  # a recorded happening


class MemoryState(StrEnum):
    """A record's lifecycle position (the `memory.lifecycle` FSM in `lifecycle.py`)."""

    CREATED = "created"
    STORED = "stored"
    ACTIVE = "active"
    ARCHIVED = "archived"
    EXPIRED = "expired"
    DELETED = "deleted"


@dataclass(frozen=True, slots=True)
class MemoryMetadata:
    """Descriptive envelope carried with a record. Timestamps are injected-clock
    values (seconds). Ranking/expiration signals (`importance`, `confidence`,
    `access_count`, `last_access_at`, `expires_at`) are read by the M5.1B-2 ranking +
    policy engines. All fields are additive with defaults — old documents deserialize
    unchanged (backward compatible)."""

    type: MemoryType = MemoryType.NOTE
    importance: float = 0.5
    confidence: float = 1.0  # how trustworthy the memory is (0..1)
    tags: tuple[str, ...] = ()
    source: str | None = None  # originating event id, when known
    origin: str | None = None  # producing subsystem (e.g. "transcript", "tool")
    created_by: str | None = None  # user/agent that caused the write
    agent_id: str | None = None  # owning agent (agent-scope isolation)
    correlation_id: str | None = None
    created_at: float = 0.0
    updated_at: float = 0.0
    expires_at: float | None = None
    access_count: int = 0
    last_access_at: float | None = None
    extra: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class MemoryRecord:
    """The memory aggregate: content + where it lives (tier/scope) + lifecycle state +
    descriptive metadata. The embedding vector itself lives only in the vector store;
    the record just remembers whether it was indexed (`embedded`)."""

    id: MemoryId
    content: str
    tier: MemoryTier
    scope: MemoryScope
    state: MemoryState = MemoryState.CREATED
    metadata: MemoryMetadata = field(default_factory=MemoryMetadata)
    embedded: bool = False

    # ── pure derivations (return a new record; never mutate) ─────────────────
    def with_state(self, state: MemoryState) -> MemoryRecord:
        return replace(self, state=state)

    def with_embedded(self, embedded: bool = True) -> MemoryRecord:
        return replace(self, embedded=embedded)

    def with_metadata(self, metadata: MemoryMetadata) -> MemoryRecord:
        return replace(self, metadata=metadata)

    def accessed(self, now: float) -> MemoryRecord:
        """Record one access (for frequency/recency ranking). Returns a new record."""
        return replace(
            self,
            metadata=replace(
                self.metadata,
                access_count=self.metadata.access_count + 1,
                last_access_at=now,
            ),
        )

    def touched(self, now: float) -> MemoryRecord:
        """Bump `updated_at` (a content/importance change). Returns a new record."""
        return replace(self, metadata=replace(self.metadata, updated_at=now))


@dataclass(frozen=True, slots=True)
class MemoryFilter:
    """A deterministic, attribute-only selector the repository layer understands.

    This is **not** a retrieval query — no ranking, no similarity, no keyword search
    (all deferred to the Retrieval Engine, M5.1B-2). It filters by exact attributes and
    caps the count. `states=()` means "any state"."""

    scope: MemoryScope | None = None
    tier: MemoryTier | None = None
    type: MemoryType | None = None
    tags: tuple[str, ...] = ()
    agent_id: str | None = None  # agent-scope isolation (matched on metadata)
    states: tuple[MemoryState, ...] = ()
    limit: int | None = None


@dataclass(frozen=True, slots=True)
class MemoryResult:
    """The outcome of a retrieval — hits plus a count and the echoed query. Kept
    separate from the port's `MemoryHit` sequence so callers/telemetry have one object
    to carry (the port method still returns `Sequence[MemoryHit]`)."""

    hits: tuple[MemoryHit, ...] = ()
    total: int = 0
    query: MemoryQuery | None = None
