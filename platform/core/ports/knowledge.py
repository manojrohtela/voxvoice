"""KnowledgePort — knowledge retrieval interface (the store side of RAG; M5.0,
interface only). No generation, no reasoning. Shapes provisional until the Knowledge
Runtime lands."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True, slots=True)
class KnowledgeQuery:
    text: str
    source_ids: tuple[str, ...] = ()
    org_id: str | None = None
    limit: int = 5
    min_score: float = 0.0


@dataclass(frozen=True, slots=True)
class KnowledgePassage:
    id: str
    source_id: str
    text: str
    score: float
    metadata: dict[str, Any] = field(default_factory=dict)


class KnowledgePort(ABC):
    @abstractmethod
    def retrieve(self, query: KnowledgeQuery) -> Sequence[KnowledgePassage]:
        """Ranked passages relevant to the query, scoped to org/sources."""

    @abstractmethod
    def sources(self, *, org_id: str | None = None) -> Sequence[str]:
        """The registered source ids (optionally tenant-scoped)."""
