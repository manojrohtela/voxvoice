"""MemoryPort — the query interface the Memory Runtime implements (M5.0, interface
only). Consumers (e.g. the Context Runtime) depend on this `core` port, never on the
`memory` package — dependency inversion keeps the graph acyclic. Neutral DTOs only;
no storage, no provider, no AI logic. Shapes are provisional until M5.1."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


class MemoryTier(StrEnum):
    WORKING = "working"
    CONVERSATION = "conversation"
    SESSION = "session"
    LONG_TERM = "long_term"
    SEMANTIC = "semantic"
    EPISODIC = "episodic"
    USER = "user"
    ORGANIZATION = "organization"


@dataclass(frozen=True, slots=True)
class MemoryScope:
    """Ownership keys — the tenant/isolation boundary a record belongs to."""

    session_id: str | None = None
    conversation_id: str | None = None
    user_id: str | None = None
    org_id: str | None = None


@dataclass(frozen=True, slots=True)
class MemoryQuery:
    text: str | None = None
    tier: MemoryTier | None = None
    scope: MemoryScope = field(default_factory=MemoryScope)
    tags: tuple[str, ...] = ()
    limit: int = 10
    min_score: float = 0.0


@dataclass(frozen=True, slots=True)
class MemoryHit:
    id: str
    content: str
    tier: MemoryTier
    score: float
    metadata: dict[str, Any] = field(default_factory=dict)


class MemoryPort(ABC):
    @abstractmethod
    def store(
        self, content: str, *, tier: MemoryTier, scope: MemoryScope, importance: float = 0.5
    ) -> str:
        """Persist a memory; return its id."""

    @abstractmethod
    def retrieve(self, query: MemoryQuery) -> Sequence[MemoryHit]:
        """Ranked retrieval within the query's tier/scope."""

    @abstractmethod
    def forget(self, memory_id: str) -> None:
        """Remove one memory (idempotent)."""
