"""Memory Query Planner (M5.1B-2).

Turns a `MemoryQuery` into a `RetrievalPlan` — it decides *how* retrieval will run
(keyword / semantic / hybrid) and the exact scoped `MemoryFilter` the repositories
will use. It **never executes retrieval** (the Retrieval Engine does); it is the pure,
deterministic decision layer and the extension seam for future strategies.

Strategy decision (deterministic):
- no query text            → KEYWORD  (a pure scoped filter, ranked by recency/importance)
- text + SEMANTIC tier     → SEMANTIC
- text + tags              → HYBRID   (fuse lexical + vector)
- text only                → KEYWORD

Subclass `MemoryQueryPlanner.choose_strategy` to add strategies without touching the
engine.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum

from .domain import MemoryFilter, MemoryQuery, MemoryScope, MemoryState, MemoryTier


class RetrievalStrategy(StrEnum):
    KEYWORD = "keyword"
    SEMANTIC = "semantic"
    HYBRID = "hybrid"


@dataclass(frozen=True, slots=True)
class RetrievalPlan:
    """A fully-resolved execution plan. Immutable; the engine reads, never mutates."""

    strategy: RetrievalStrategy
    filter: MemoryFilter  # the scoped candidate cut (isolation lives here)
    text: str | None  # normalized query text (None when absent)
    terms: tuple[str, ...]  # tokenized keyword terms (empty when no text)
    limit: int
    min_score: float
    scope: MemoryScope = field(default_factory=MemoryScope)

    @property
    def wants_semantic(self) -> bool:
        return self.strategy in (RetrievalStrategy.SEMANTIC, RetrievalStrategy.HYBRID)

    @property
    def wants_keyword(self) -> bool:
        return self.strategy in (RetrievalStrategy.KEYWORD, RetrievalStrategy.HYBRID)


class MemoryQueryPlanner:
    """Builds a `RetrievalPlan` from a `MemoryQuery`. Pure + deterministic."""

    def plan(self, query: MemoryQuery, *, agent_id: str | None = None) -> RetrievalPlan:
        text = (query.text or "").strip() or None
        terms = tuple(_tokenize(text)) if text else ()
        strategy = self.choose_strategy(query, has_text=text is not None)
        filter = MemoryFilter(
            scope=query.scope,
            tier=query.tier,
            tags=query.tags,
            agent_id=agent_id,
            states=(MemoryState.ACTIVE,),
            limit=None,  # rank first, then select — never truncate candidates early
        )
        return RetrievalPlan(
            strategy=strategy,
            filter=filter,
            text=text,
            terms=terms,
            limit=query.limit,
            min_score=query.min_score,
            scope=query.scope,
        )

    def choose_strategy(self, query: MemoryQuery, *, has_text: bool) -> RetrievalStrategy:
        if not has_text:
            return RetrievalStrategy.KEYWORD
        if query.tier is MemoryTier.SEMANTIC:
            return RetrievalStrategy.SEMANTIC
        if query.tags:
            return RetrievalStrategy.HYBRID
        return RetrievalStrategy.KEYWORD


def _tokenize(text: str) -> list[str]:
    import re

    return re.findall(r"[a-z0-9]+", text.lower())
