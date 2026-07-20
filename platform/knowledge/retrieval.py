"""Knowledge Retrieval Engine (M5.2B-2) — orchestrates intelligent retrieval.

Executes the flow: **tenancy → planner → repositories → merge → tenant/version/metadata
filtering → policy validation → ranking → selection → KnowledgeResult**. It owns
orchestration only — it never touches a provider or a store directly (repositories only),
and isolation is enforced *first* (before any candidate is fetched).

Deterministic throughout: injected clock in the ranker, chunk-ids break every tie, no
randomness. GraphRAG / MultiVector / Federated land as new strategies + injected sources
(`extensions`) without changing this engine's shape.
"""

from __future__ import annotations

from collections.abc import Sequence

from tenant import TenantContext

from .domain import Chunk, Document, DocumentState
from .planner import KnowledgeQueryPlanner, RetrievalPlan, RetrievalStrategy
from .policies import KnowledgePolicyEngine
from .query import KnowledgeRetrievalQuery
from .ranking import Candidate, KnowledgeRankingEngine, ScoredChunk
from .repositories import KnowledgeRepository
from .results import Citation, KnowledgeHit, KnowledgeResult, RankingBreakdown
from .tenancy import KnowledgeTenancy


class KnowledgeRetrievalEngine:
    def __init__(
        self,
        *,
        repository: KnowledgeRepository,
        planner: KnowledgeQueryPlanner,
        ranking: KnowledgeRankingEngine,
        policy: KnowledgePolicyEngine,
        tenancy: KnowledgeTenancy,
    ) -> None:
        self._repo = repository
        self._planner = planner
        self._ranking = ranking
        self._policy = policy
        self._tenancy = tenancy

    async def retrieve(
        self, query: KnowledgeRetrievalQuery, *, tenant: TenantContext | None = None
    ) -> KnowledgeResult:
        # 1. isolation FIRST — resolve the effective, tenant-safe scope
        resolved = self._tenancy.guard(query.scope, tenant, action="retrieve")
        query = query.with_scope(resolved.scope)

        # 2. plan (keyword / semantic / hybrid / metadata / version)
        plan = self._planner.plan(query)

        # 3. resolve the in-scope, filtered candidate documents (tenant + metadata + type)
        documents = await self._candidate_documents(plan)
        if not documents:
            return KnowledgeResult(strategy=plan.strategy.value, profile=plan.profile.value)

        # 4. gather candidate chunks + per-strategy signals (via repositories only)
        candidates = await self._candidates(plan, documents)
        if not candidates:
            return KnowledgeResult(strategy=plan.strategy.value, profile=plan.profile.value)

        # 5. rank (deterministic, profile-weighted)
        scored = self._ranking.rank(candidates, weights=plan.weights)

        # 6. policy validation + selection (score floor, type allow-list, citation, top-k)
        hits = self._select(scored, plan)
        return KnowledgeResult(
            hits=tuple(hits),
            total=len(hits),
            strategy=plan.strategy.value,
            profile=plan.profile.value,
            candidates=len(candidates),
        )

    # ── candidate documents: tenant + metadata + type + source filtering ─────
    async def _candidate_documents(self, plan: RetrievalPlan) -> list[Document]:
        docs = await self._repo.list_documents(org_id=plan.scope.org_id)
        # metadata pre-filter via the inverted index (doc-id set; None → no constraint)
        meta_ids = self._repo.index.match_metadata(plan.metadata_filters)
        source_ids = set(plan.source_ids)
        types = set(plan.knowledge_types)

        out: list[Document] = []
        for d in docs:
            if d.state is not DocumentState.ACTIVE:
                continue
            if source_ids and d.id not in source_ids:
                continue
            if types and d.knowledge_type not in types:
                continue
            if meta_ids is not None and d.id not in meta_ids:
                continue
            if not self._tenancy.admits(d, plan.scope):
                continue
            out.append(d)
        return out

    # ── candidate chunks + strategy signals ───────────────────────────────────
    async def _candidates(
        self, plan: RetrievalPlan, documents: Sequence[Document]
    ) -> list[Candidate]:
        doc_by_id = {d.id: d for d in documents}

        # semantic signal: content-hash → similarity (mapped onto chunks below)
        similarity: dict[str, float] = {}
        if plan.wants_semantic and plan.text:
            matches = await self._repo.embeddings.search(plan.text, k=plan.config.vector_k)
            similarity = {m.id: m.score for m in matches}

        # keyword signal: candidate chunk-id set from the inverted index (lexical OR)
        keyword_ids = self._repo.index.match_terms(plan.terms) if plan.wants_keyword else set()
        wanted = set(plan.terms)

        candidates: list[Candidate] = []
        for doc in documents:
            version = plan.version if plan.version is not None else doc.version
            chunks = await self._repo.chunks.for_document(doc.id, version=version)
            for ch in chunks:
                sim = similarity.get(ch.content_hash, 0.0)
                kw = _keyword_overlap(ch, wanted) if plan.wants_keyword else 0.0
                # keyword strategy only admits chunks the index matched (unless hybrid/sem)
                if plan.strategy is RetrievalStrategy.KEYWORD and ch.id not in keyword_ids:
                    continue
                candidates.append(Candidate(chunk=ch, document=doc_by_id[doc.id],
                                            similarity=sim, keyword=kw))
        return candidates

    # ── policy validation + selection ─────────────────────────────────────────
    def _select(self, scored: Sequence[ScoredChunk], plan: RetrievalPlan) -> list[KnowledgeHit]:
        out: list[KnowledgeHit] = []
        limit = self._policy.cap_results(plan.limit)
        for s in scored:
            if s.score < plan.min_score:
                continue
            hit = self._to_hit(s)
            verdict = self._policy.admit_hit(hit)
            if not verdict.ok:
                continue
            out.append(hit)
            if len(out) >= limit:
                break
        return out

    def _to_hit(self, s: ScoredChunk) -> KnowledgeHit:
        ch: Chunk = s.candidate.chunk
        doc: Document = s.candidate.document
        content = self._policy.apply_privacy(ch.content)
        citation = Citation(
            document_id=doc.id,
            version=ch.version,
            chunk_id=ch.id,
            source_uri=doc.source.uri or doc.metadata.source_uri,
            title=doc.metadata.title or doc.source.title,
            author=doc.metadata.author,
            content_hash=ch.content_hash,
            char_start=ch.start,
            char_end=ch.end,
        )
        return KnowledgeHit(
            chunk_id=ch.id,
            document_id=doc.id,
            version=ch.version,
            content=content,
            content_hash=ch.content_hash,
            score=s.score,
            knowledge_type=doc.knowledge_type,
            scope=doc.scope,
            citation=citation,
            breakdown=RankingBreakdown(signals=dict(s.signals), total=s.score),
            source_metadata={
                "mime": doc.source.mime,
                "origin": doc.source.origin.value,
                "lang": doc.metadata.lang,
            },
        )


def _keyword_overlap(chunk: Chunk, wanted: set[str]) -> float:
    """Lexical term-overlap score in [0,1]: fraction of query terms present in the chunk."""
    if not wanted:
        return 0.0
    import re

    tokens = set(re.findall(r"[a-z0-9]+", chunk.content.lower()))
    return len(wanted & tokens) / len(wanted) if tokens else 0.0
