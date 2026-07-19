"""Retrieval Engine (M5.1B-2) — orchestrates intelligent retrieval.

Executes the flow: **tenancy → planner → repositories → ranking → selection →
MemoryResult**. It never touches a provider or a store directly (repositories only),
and isolation is enforced *first* (before any candidate is fetched).

Supports Top-K, keyword, semantic, and hybrid retrieval across conversation / session /
user / organization / agent scopes. Deterministic throughout (injected clock in the
ranker; ids break every tie).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from core.policies import SelectionPolicy, SelectionStrategy
from tenant import TenantContext

from .domain import MemoryHit, MemoryQuery, MemoryRecord, MemoryResult
from .planner import MemoryQueryPlanner, RetrievalPlan
from .ranking import RankingEngine
from .repositories import MemoryRepository, VectorRepository
from .serializer import MemorySerializer
from .tenancy import MemoryTenancy


class RetrievalEngine:
    def __init__(
        self,
        *,
        memory_repo: MemoryRepository,
        vector_repo: VectorRepository,
        planner: MemoryQueryPlanner,
        ranking: RankingEngine,
        tenancy: MemoryTenancy,
        serializer: MemorySerializer,
        selection: SelectionPolicy | None = None,
        vector_k: int = 100,
    ) -> None:
        self._memory = memory_repo
        self._vectors = vector_repo
        self._planner = planner
        self._ranking = ranking
        self._tenancy = tenancy
        self._serializer = serializer
        self._selection = selection or SelectionPolicy(strategy=SelectionStrategy.TOP_K)
        self._vector_k = vector_k

    async def retrieve(
        self, query: MemoryQuery, *, tenant: TenantContext | None = None
    ) -> MemoryResult:
        # 1. isolation FIRST — resolve the effective, tenant-safe scope
        resolved = self._tenancy.guard(query.scope, tenant, action="retrieve")
        scoped_query = _rescope(query, resolved.scope)

        # 2. plan (keyword / semantic / hybrid)
        plan = self._planner.plan(scoped_query, agent_id=resolved.agent_id)

        # 3. scoped candidate set (isolation lives in the filter)
        candidates: Sequence[MemoryRecord] = await self._memory.query(plan.filter)
        if not candidates:
            return MemoryResult(hits=(), total=0, query=query)

        # 4. per-strategy signals
        similarity = await self._semantic_scores(plan, candidates)
        keyword = _keyword_scores(plan, candidates)

        # 5. rank
        scored = self._ranking.rank(
            candidates,
            similarity=similarity,
            keyword=keyword,
            conversation_scope=plan.scope,
        )

        # 6. select (threshold by min_score, then top-k)
        hits = self._select(scored, plan)
        return MemoryResult(hits=tuple(hits), total=len(hits), query=query)

    # ── strategy signal generation (via repositories only) ───────────────────
    async def _semantic_scores(
        self, plan: RetrievalPlan, candidates: Sequence[MemoryRecord]
    ) -> Mapping[str, float]:
        if not plan.wants_semantic or not plan.text:
            return {}
        ids = {r.id.value for r in candidates}  # isolation: only scoped ids survive
        matches = await self._vectors.search(plan.text, k=self._vector_k)
        return {m.id: m.score for m in matches if m.id in ids}

    def _select(self, scored, plan: RetrievalPlan) -> list[MemoryHit]:
        out: list[MemoryHit] = []
        for s in scored:
            if s.score < plan.min_score:
                continue
            out.append(self._serializer.to_hit(s.record, score=s.score))
        if self._selection.strategy is SelectionStrategy.TOP_K:
            out = out[: plan.limit]
        return out


def _keyword_scores(
    plan: RetrievalPlan, candidates: Sequence[MemoryRecord]
) -> Mapping[str, float]:
    """Lexical term-overlap score in [0,1] per candidate. Empty terms → no signal
    (pure filter)."""
    if not plan.wants_keyword or not plan.terms:
        return {}
    import re

    wanted = set(plan.terms)
    scores: dict[str, float] = {}
    for r in candidates:
        tokens = set(re.findall(r"[a-z0-9]+", r.content.lower()))
        if tokens:
            scores[r.id.value] = len(wanted & tokens) / len(wanted)
    return scores


def _rescope(query: MemoryQuery, scope) -> MemoryQuery:
    from dataclasses import replace

    return replace(query, scope=scope)
