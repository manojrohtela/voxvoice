"""Knowledge Query Planner (M5.2B-2).

Turns a `KnowledgeRetrievalQuery` into an immutable `RetrievalPlan` — it decides *how*
retrieval runs (keyword / semantic / hybrid / metadata-only / version) and the resolved
knobs the engine will use. It **never performs retrieval** (the Retrieval Engine does),
touches no repository, and is fully deterministic.

Strategy decision (deterministic, profile-driven — never a per-profile code branch):
- version pinned            → VERSION
- no query text             → METADATA   (pure scoped/metadata filter, ranked by priors)
- text + semantic + keyword → HYBRID
- text + semantic only      → SEMANTIC
- text + keyword only       → KEYWORD

Subclass `choose_strategy` to add strategies (GRAPH, MULTI_VECTOR) without touching the
engine — the extension seam for future retrieval modes.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import StrEnum

from .domain import KnowledgeScope, KnowledgeType
from .profiles import ProfileConfig, RetrievalProfile, config_for
from .query import KnowledgeRetrievalQuery

_TOKEN = re.compile(r"[a-z0-9]+")


class RetrievalStrategy(StrEnum):
    KEYWORD = "keyword"
    SEMANTIC = "semantic"
    HYBRID = "hybrid"
    METADATA = "metadata"
    VERSION = "version"


@dataclass(frozen=True, slots=True)
class RetrievalPlan:
    """A fully-resolved execution plan. Immutable; the engine reads, never mutates."""

    strategy: RetrievalStrategy
    text: str | None
    terms: tuple[str, ...]
    scope: KnowledgeScope
    source_ids: tuple[str, ...]
    tags: tuple[str, ...]
    knowledge_types: tuple[KnowledgeType, ...]
    metadata_filters: dict[str, str]
    version: int | None
    limit: int
    min_score: float
    profile: RetrievalProfile
    config: ProfileConfig
    weights: dict[str, float] = field(default_factory=dict)

    @property
    def wants_semantic(self) -> bool:
        return self.strategy in (RetrievalStrategy.SEMANTIC, RetrievalStrategy.HYBRID)

    @property
    def wants_keyword(self) -> bool:
        return self.strategy in (RetrievalStrategy.KEYWORD, RetrievalStrategy.HYBRID)

    @property
    def current_version_only(self) -> bool:
        return self.version is None and self.config.current_version_only


class KnowledgeQueryPlanner:
    """Builds a `RetrievalPlan` from a `KnowledgeRetrievalQuery`. Pure + deterministic."""

    def plan(self, query: KnowledgeRetrievalQuery) -> RetrievalPlan:
        cfg = config_for(query.profile)
        text = (query.text or "").strip() or None
        terms = tuple(_TOKEN.findall(text.lower())) if text else ()
        strategy = self.choose_strategy(query, cfg, has_text=text is not None)
        # profile caps the caller's limit / raises the score floor (never lowers intent)
        limit = min(query.limit, cfg.max_results) if query.limit > 0 else cfg.max_results
        min_score = max(query.min_score, cfg.min_score)
        return RetrievalPlan(
            strategy=strategy,
            text=text,
            terms=terms,
            scope=query.scope,
            source_ids=query.source_ids,
            tags=query.tags,
            knowledge_types=query.knowledge_types,
            metadata_filters=dict(query.metadata_filters),
            version=query.version,
            limit=limit,
            min_score=min_score,
            profile=query.profile,
            config=cfg,
            weights=cfg.weight_map(),
        )

    def choose_strategy(
        self, query: KnowledgeRetrievalQuery, cfg: ProfileConfig, *, has_text: bool
    ) -> RetrievalStrategy:
        if query.version is not None:
            return RetrievalStrategy.VERSION
        if not has_text:
            return RetrievalStrategy.METADATA
        semantic = cfg.prefer_semantic and cfg.vector_k > 0
        keyword = cfg.prefer_keyword
        if semantic and keyword:
            return RetrievalStrategy.HYBRID
        if semantic:
            return RetrievalStrategy.SEMANTIC
        return RetrievalStrategy.KEYWORD


__all__ = ["RetrievalStrategy", "RetrievalPlan", "KnowledgeQueryPlanner", "RetrievalProfile"]
