"""Knowledge retrieval results (M5.2B-2) — immutable, provenance-complete.

Every retrieved chunk carries **complete provenance** so a consumer never performs an
additional repository lookup: document id + version + chunk id, the source + citation
metadata, the retrieval score, the full ranking breakdown, the knowledge scope, and the
content hash. `KnowledgeResult` is the immutable envelope the Retrieval Engine returns;
`KnowledgeHit.to_passage()` narrows it to the frozen `core.ports.KnowledgePort` shape.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from core.ports.knowledge import KnowledgePassage

from .domain import KnowledgeScope, KnowledgeType


@dataclass(frozen=True, slots=True)
class RankingBreakdown:
    """The per-signal contribution behind a hit's score (deterministic, explainable).
    Keys are the ranking signal names; `total` is the weighted sum the ranker produced."""

    signals: dict[str, float] = field(default_factory=dict)
    total: float = 0.0


@dataclass(frozen=True, slots=True)
class Citation:
    """The citeable provenance of a chunk — everything a consumer needs to attribute a
    passage without touching the repositories again."""

    document_id: str
    version: int
    chunk_id: str
    source_uri: str | None
    title: str | None
    author: str | None
    content_hash: str
    char_start: int
    char_end: int


@dataclass(frozen=True, slots=True)
class KnowledgeHit:
    """One retrieved chunk with complete provenance. Immutable."""

    chunk_id: str
    document_id: str
    version: int
    content: str
    content_hash: str
    score: float
    knowledge_type: KnowledgeType
    scope: KnowledgeScope
    citation: Citation
    breakdown: RankingBreakdown = field(default_factory=RankingBreakdown)
    source_metadata: dict[str, str] = field(default_factory=dict)

    def to_passage(self) -> KnowledgePassage:
        """Narrow to the frozen `KnowledgePort` passage shape (the store side of RAG).
        The rich provenance rides along in `metadata` so nothing is lost."""
        return KnowledgePassage(
            id=self.chunk_id,
            source_id=self.document_id,
            text=self.content,
            score=self.score,
            metadata={
                "version": self.version,
                "content_hash": self.content_hash,
                "knowledge_type": self.knowledge_type.value,
                "source_uri": self.citation.source_uri,
                "title": self.citation.title,
                "char_start": self.citation.char_start,
                "char_end": self.citation.char_end,
                "ranking": dict(self.breakdown.signals),
            },
        )


@dataclass(frozen=True, slots=True)
class KnowledgeResult:
    """The immutable envelope returned by the Retrieval Engine: ranked hits + the totals
    and the strategy/profile that produced them (for observability + replay)."""

    hits: tuple[KnowledgeHit, ...] = ()
    total: int = 0
    strategy: str = ""
    profile: str = ""
    candidates: int = 0

    def passages(self) -> tuple[KnowledgePassage, ...]:
        return tuple(h.to_passage() for h in self.hits)
