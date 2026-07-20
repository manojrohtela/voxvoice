"""Knowledge Ranking Engine (M5.2B-2) — deterministic chunk scoring.

Scores each candidate chunk as a weighted linear blend of neutral signals via the shared
`core.policies.RankingPolicy` (the module names the signals; the policy weights them, and
the active `RetrievalProfile` supplies the weights):

- **semantic**          — vector-search similarity for the chunk (0..1)
- **keyword**           — lexical term-overlap of the query terms with the chunk (0..1)
- **freshness**         — monotonic decay of the document's age (injected clock)
- **popularity**        — saturating function of a document access count (0 by default)
- **version_priority**  — 1.0 for the document's current version, decayed for older
- **org_priority**      — deterministic knowledge-type precedence (reference/org > … )
- **document_priority** — the document's stored confidence (0..1)
- **chunk_priority**    — lead-bias: earlier chunks score slightly higher

Fully deterministic: no system randomness, injected clock only, chunk-id breaks every
tie. An optional injected `Reranker` (extension point) may reorder the final hits.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass

from core.policies import RankingPolicy

from .domain import Chunk, Document, KnowledgeType

# Deterministic knowledge-type precedence for the org_priority signal (0..1).
_TYPE_PRIORITY: Mapping[KnowledgeType, float] = {
    KnowledgeType.REFERENCE: 1.0,
    KnowledgeType.ORGANIZATION: 0.9,
    KnowledgeType.STATIC: 0.8,
    KnowledgeType.SHARED: 0.6,
    KnowledgeType.PRIVATE: 0.5,
    KnowledgeType.GENERATED: 0.4,
    KnowledgeType.IMPORTED: 0.4,
    KnowledgeType.EXTERNAL: 0.3,
}


@dataclass(frozen=True, slots=True)
class Candidate:
    """A retrieval candidate: the chunk, its owning document, and the raw text signals
    the engine gathered (semantic/keyword). The ranker derives the rest."""

    chunk: Chunk
    document: Document
    similarity: float = 0.0
    keyword: float = 0.0


@dataclass(frozen=True, slots=True)
class ScoredChunk:
    candidate: Candidate
    score: float
    signals: Mapping[str, float]


class KnowledgeRankingEngine:
    def __init__(
        self,
        *,
        policy: RankingPolicy | None = None,
        weights: Mapping[str, float] | None = None,
        clock: Callable[[], float] | None = None,
        half_life_s: float = 86_400.0,  # one day — documents age slower than memories
    ) -> None:
        import time

        self._default_policy = policy or RankingPolicy(weights=dict(weights or {}))
        self._clock = clock or time.time
        if half_life_s <= 0:
            raise ValueError("half_life_s must be > 0")
        self._half_life = half_life_s

    def rank(
        self,
        candidates: Sequence[Candidate],
        *,
        weights: Mapping[str, float] | None = None,
        now: float | None = None,
    ) -> list[ScoredChunk]:
        """Score every candidate and return them sorted by score desc (chunk-id tie-break)."""
        now = self._clock() if now is None else now
        policy = (
            RankingPolicy(weights=dict(weights)) if weights is not None else self._default_policy
        )

        scored: list[ScoredChunk] = []
        for c in candidates:
            signals = {
                "semantic": _clamp(c.similarity),
                "keyword": _clamp(c.keyword),
                "freshness": self._freshness(c.document, now),
                "popularity": _popularity(c.document),
                "version_priority": _version_priority(c.chunk, c.document),
                "org_priority": _TYPE_PRIORITY.get(c.document.knowledge_type, 0.4),
                "document_priority": _clamp(c.document.metadata.confidence),
                "chunk_priority": 1.0 / (1.0 + c.chunk.index),
            }
            scored.append(ScoredChunk(c, policy.score(signals), signals))

        scored.sort(key=lambda s: (-s.score, s.candidate.chunk.id))
        return scored

    def _freshness(self, doc: Document, now: float) -> float:
        ref = doc.metadata.updated_at or doc.metadata.created_at
        if ref <= 0:
            return 0.0
        age = max(0.0, now - ref)
        return math.pow(0.5, age / self._half_life)  # 1.0 fresh → 0.5 at one half-life


def _clamp(v: float) -> float:
    return 0.0 if v < 0.0 else 1.0 if v > 1.0 else v


def _popularity(doc: Document) -> float:
    """Saturating popularity from an optional `access_count` in document metadata extra.
    Absent → 0.0 (no popularity provider wired). Deterministic."""
    raw = doc.metadata.extra.get("access_count")
    try:
        count = int(raw) if raw is not None else 0
    except (TypeError, ValueError):
        count = 0
    return count / (count + 1.0) if count > 0 else 0.0


def _version_priority(chunk: Chunk, doc: Document) -> float:
    """Current version → 1.0; each version behind decays geometrically."""
    behind = max(0, doc.version - chunk.version)
    return math.pow(0.5, behind)
