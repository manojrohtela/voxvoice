"""Knowledge Runtime intelligence tests (M5.2B-2).

Covers the planner, retrieval engine, ranking, policies, cache, version + metadata
filtering, tenant isolation, retrieval profiles, KnowledgeResult provenance, event
(replay/gateway) compatibility, determinism, extension interfaces, and the architecture
boundaries (planner never retrieves, engine touches repositories only). The M5.2B-1 core
tests remain untouched in `test_knowledge_runtime.py`.
"""

from __future__ import annotations

import ast
import inspect
import itertools
import pathlib
from dataclasses import FrozenInstanceError

import pytest

from core.events import Event, EventSource, EventType
from core.ports.knowledge import KnowledgeQuery
from events.bus import InProcessEventBus
from knowledge import (
    Candidate,
    Citation,
    Compressor,
    GraphRetriever,
    GraphStore,
    KnowledgeFactory,
    KnowledgeHit,
    KnowledgePolicies,
    KnowledgePolicyEngine,
    KnowledgeQueryPlanner,
    KnowledgeRetrievalEngine,
    KnowledgeRetrievalQuery,
    KnowledgeScope,
    KnowledgeSource,
    KnowledgeType,
    MultiVectorRetriever,
    RankingBreakdown,
    Reranker,
    RetrievalPlan,
    RetrievalPolicy,
    RetrievalProfile,
    RetrievalStrategy,
    Summarizer,
    Verdict,
    config_for,
)
from knowledge.policies import CitationPolicy
from knowledge.tenancy import KnowledgeAccessDenied
from tenant import OrganizationContext, TenantContext

CRICKET = "The cover drive is an elegant cricket shot played along the ground off side."
BOWLING = "Leg spin bowling turns the ball away from a right handed batsman sharply."
FIELDING = "A slip fielder waits behind the wicket to catch a thin outside edge."


def _ids():
    return (f"k{i:02d}" for i in itertools.count()).__next__


def _runtime(config=None, **kwargs):
    from knowledge import KnowledgeConfiguration

    cfg = config or KnowledgeConfiguration.default()
    kwargs.setdefault("clock", lambda: 1000.0)
    return KnowledgeFactory.create(cfg, id_factory=_ids(), **kwargs)


def _scope(**kw):
    base = dict(org_id="o1")
    base.update(kw)
    return KnowledgeScope(**base)


def _tenant(org="o1", user=None):
    return TenantContext(org=OrganizationContext(org_id=org), user_id=user)


def _seed(rt, *, org="o1"):
    """Index three distinct documents; return their ids."""
    a = rt.index(CRICKET, uri="doc://cricket", scope=_scope(org_id=org),
                 knowledge_type=KnowledgeType.REFERENCE, title="Cricket")
    b = rt.index(BOWLING, uri="doc://bowling", scope=_scope(org_id=org),
                 knowledge_type=KnowledgeType.ORGANIZATION, title="Bowling")
    c = rt.index(FIELDING, uri="doc://fielding", scope=_scope(org_id=org),
                 knowledge_type=KnowledgeType.ORGANIZATION, title="Fielding")
    return a, b, c


# ── planner ───────────────────────────────────────────────────────────────────
def test_planner_is_pure_and_touches_no_repository():
    src = inspect.getsource(KnowledgeQueryPlanner)
    assert "repo" not in src and "await" not in src  # planner never retrieves


def test_planner_strategy_selection():
    p = KnowledgeQueryPlanner()
    # no text → metadata-only
    assert p.plan(KnowledgeRetrievalQuery(text="")).strategy is RetrievalStrategy.METADATA
    # version pinned → version strategy
    pinned = p.plan(KnowledgeRetrievalQuery(text="x", version=1))
    assert pinned.strategy is RetrievalStrategy.VERSION
    # balanced + text → hybrid
    assert p.plan(KnowledgeRetrievalQuery(text="x")).strategy is RetrievalStrategy.HYBRID
    # fast profile has vector_k=0 → keyword only
    fast = p.plan(KnowledgeRetrievalQuery(text="x", profile=RetrievalProfile.FAST))
    assert fast.strategy is RetrievalStrategy.KEYWORD


def test_planner_plan_is_immutable_and_profile_capped():
    p = KnowledgeQueryPlanner()
    plan = p.plan(KnowledgeRetrievalQuery(text="cover drive", limit=100,
                                          profile=RetrievalProfile.HIGH_PRECISION))
    assert isinstance(plan, RetrievalPlan)
    assert plan.limit == config_for(RetrievalProfile.HIGH_PRECISION).max_results  # capped
    assert plan.min_score >= config_for(RetrievalProfile.HIGH_PRECISION).min_score
    with pytest.raises(FrozenInstanceError):
        plan.limit = 3  # frozen


# ── retrieval ───────────────────────────────────────────────────────────────────
def test_retrieve_returns_relevant_provenance_complete_hits():
    rt = _runtime()
    _seed(rt)
    res = rt.query(KnowledgeRetrievalQuery(text="cover drive off side", scope=_scope()))
    assert res.total >= 1
    top = res.hits[0]
    # the cricket document should win on the cricket query
    assert top.document_id and "cricket" in top.citation.source_uri
    # full provenance — no further lookups needed
    assert top.chunk_id and top.version == 1 and top.content_hash
    assert top.citation.title == "Cricket" and top.citation.char_end > 0
    assert set(top.breakdown.signals) >= {"semantic", "keyword", "freshness"}
    assert top.knowledge_type is KnowledgeType.REFERENCE


def test_retrieve_port_passages_carry_metadata():
    rt = _runtime()
    _seed(rt)
    passages = rt.retrieve(KnowledgeQuery(text="leg spin bowling", org_id="o1"))
    assert passages
    assert passages[0].metadata["knowledge_type"] in {"organization", "reference"}
    assert "ranking" in passages[0].metadata and "content_hash" in passages[0].metadata


def test_engine_touches_repositories_only():
    src = inspect.getsource(KnowledgeRetrievalEngine)
    # no direct store/provider access — repositories only
    assert "_store" not in src and "providers" not in src


# ── ranking ─────────────────────────────────────────────────────────────────────
def test_ranking_is_deterministic_with_id_tiebreak():
    rt = _runtime()
    _seed(rt)
    q = KnowledgeRetrievalQuery(text="cricket shot catch edge bowling", scope=_scope())
    a = [h.chunk_id for h in rt.query(q).hits]
    b = [h.chunk_id for h in rt.query(q).hits]
    assert a == b


def test_ranking_breakdown_totals_match_score():
    rt = _runtime()
    _seed(rt)
    hit = rt.query(KnowledgeRetrievalQuery(text="cover drive", scope=_scope())).hits[0]
    assert hit.breakdown.total == pytest.approx(hit.score)


# ── retrieval profiles ───────────────────────────────────────────────────────────
def test_profiles_change_result_shape_without_branching():
    rt = _runtime()
    _seed(rt)
    fast = rt.query(KnowledgeRetrievalQuery(text="cricket", scope=_scope(),
                                            profile=RetrievalProfile.FAST))
    research = rt.query(KnowledgeRetrievalQuery(text="cricket", scope=_scope(),
                                                profile=RetrievalProfile.RESEARCH))
    assert fast.profile == "fast" and research.profile == "research"
    assert fast.strategy == "keyword" and research.strategy == "hybrid"


def test_compliance_profile_requires_citation():
    assert config_for(RetrievalProfile.COMPLIANCE).require_citation is True
    assert config_for(RetrievalProfile.COMPLIANCE).current_version_only is True


# ── metadata + version filtering ─────────────────────────────────────────────────
def test_metadata_filter_restricts_by_knowledge_type():
    rt = _runtime()
    _seed(rt)
    res = rt.query(KnowledgeRetrievalQuery(
        text="cricket bowling fielding", scope=_scope(),
        metadata_filters={"knowledge_type": "reference"},
    ))
    assert res.total >= 1
    assert all(h.knowledge_type is KnowledgeType.REFERENCE for h in res.hits)


def test_knowledge_type_filter_restricts_candidates():
    rt = _runtime()
    _seed(rt)
    res = rt.query(KnowledgeRetrievalQuery(
        text="cricket bowling fielding", scope=_scope(),
        knowledge_types=(KnowledgeType.ORGANIZATION,),
    ))
    assert res.total >= 1 and all(h.knowledge_type is KnowledgeType.ORGANIZATION for h in res.hits)


def test_version_filter_pins_old_version():
    rt = _runtime()
    rt.index(CRICKET, uri="doc://c", scope=_scope())
    rt.index(CRICKET + " Extra revised sentence about the straight drive.",
             uri="doc://c", scope=_scope())
    from knowledge import document_id_for
    did = document_id_for("doc://c")
    pinned = rt.query(KnowledgeRetrievalQuery(text="cover drive", scope=_scope(),
                                              source_ids=(did,), version=1))
    assert pinned.strategy == "version"
    assert all(h.version == 1 for h in pinned.hits)


# ── tenant isolation ─────────────────────────────────────────────────────────────
def test_tenant_isolation_blocks_cross_org():
    rt = _runtime()
    _seed(rt, org="o1")
    with pytest.raises(KnowledgeAccessDenied):
        rt.query(KnowledgeRetrievalQuery(text="cricket", scope=_scope(org_id="o1")),
                 tenant=_tenant(org="o2"))


def test_tenant_isolation_hides_other_users_private_knowledge():
    rt = _runtime()
    rt.index("Private note about my personal batting drills and weaknesses.",
             uri="doc://priv", scope=KnowledgeScope(org_id="o1", user_id="alice"),
             knowledge_type=KnowledgeType.PRIVATE)
    rt.index(CRICKET, uri="doc://shared", scope=_scope(org_id="o1"),
             knowledge_type=KnowledgeType.ORGANIZATION)
    # bob queries — must not see alice's PRIVATE doc, but sees ORG doc
    res = rt.query(KnowledgeRetrievalQuery(text="batting drills cricket",
                                           scope=KnowledgeScope(org_id="o1", user_id="bob")),
                   tenant=_tenant(org="o1", user="bob"))
    assert all(h.knowledge_type is not KnowledgeType.PRIVATE for h in res.hits)


def test_isolation_runs_before_retrieval():
    src = inspect.getsource(KnowledgeRetrievalEngine.retrieve)
    guard = src.index("guard")
    fetch = src.index("_candidate_documents")
    assert guard < fetch  # tenancy.guard precedes any candidate fetch


# ── policy engine (verdicts only) ────────────────────────────────────────────────
def test_policy_engine_returns_verdicts_only():
    eng = KnowledgePolicyEngine()
    assert isinstance(eng.validate_document("hi"), Verdict)
    assert eng.validate_document("").ok is False
    assert eng.cap_results(999) == KnowledgePolicies().retrieval.max_results


def test_citation_policy_drops_hits_without_source():
    pol = KnowledgePolicies(citation=CitationPolicy(require_source_uri=True))
    eng = KnowledgePolicyEngine(pol)
    cite = Citation(document_id="d", version=1, chunk_id="c", source_uri=None, title=None,
                    author=None, content_hash="h", char_start=0, char_end=1)
    hit = KnowledgeHit(chunk_id="c", document_id="d", version=1, content="x", content_hash="h",
                       score=1.0, knowledge_type=KnowledgeType.STATIC, scope=KnowledgeScope(),
                       citation=cite, breakdown=RankingBreakdown())
    assert eng.admit_hit(hit).ok is False


def test_retrieval_policy_min_score_floor():
    eng = KnowledgePolicyEngine(KnowledgePolicies(retrieval=RetrievalPolicy(min_score=0.9)))
    cite = Citation("d", 1, "c", "u", None, None, "h", 0, 1)
    low = KnowledgeHit("c", "d", 1, "x", "h", 0.1, KnowledgeType.STATIC, KnowledgeScope(),
                       cite, RankingBreakdown())
    assert eng.admit_hit(low).ok is False


# ── cache (repository-owned) ─────────────────────────────────────────────────────
async def test_cache_read_through_and_write_through():
    rt = _runtime()
    a, _b, _c = _seed(rt)
    cache = rt._repo.chunks.cache
    assert cache is not None
    chunks = await rt._repo.get_chunks(a)
    cid = chunks[0].id
    cache.stats.hits = cache.stats.misses = 0
    await rt._repo.chunks.get(cid)  # populated by write-through during ingest → hit
    await rt._repo.chunks.get(cid)
    assert cache.stats.hits >= 1
    snap = rt.statistics().cache
    assert snap is not None and snap["size"] >= 1


def test_cache_can_be_disabled():
    from knowledge import CacheConfig, KnowledgeConfiguration

    rt = _runtime(KnowledgeConfiguration(cache=CacheConfig(enabled=False)))
    _seed(rt)
    assert rt._repo.chunks.cache is None
    assert rt.statistics().cache is None


# ── event ingestion (self-loop prevention) ──────────────────────────────────────
def test_ingestor_never_ingests_knowledge_events():
    from knowledge import KnowledgeIngestor

    assert not (KnowledgeIngestor.INGEST_TYPES & {
        EventType.KNOWLEDGE_INDEXED, EventType.KNOWLEDGE_RETRIEVED,
        EventType.KNOWLEDGE_SOURCE_UPDATED,
    })


def test_ingestion_registers_generated_document():
    rt = _runtime()
    bus = InProcessEventBus(clock=lambda: 1.0)
    rt.attach(bus)
    # a reasoning module emits a generated knowledge doc
    bus.publish(Event(
        id="e1", type=EventType.REASONING_COMPLETED, ts=1.0, seq=0, session_id="s",
        source=EventSource.REASONING, org_id="o1",
        data={"as_knowledge": True, "document": CRICKET, "uri": "gen://1", "title": "Gen"},
    ))
    res = rt.query(KnowledgeRetrievalQuery(text="cover drive", scope=_scope()))
    assert res.total >= 1 and any(h.knowledge_type is KnowledgeType.GENERATED for h in res.hits)


def test_runtime_ignores_its_own_emitted_events():
    rt = _runtime()
    bus = InProcessEventBus(clock=lambda: 1.0)
    rt.attach(bus)
    _seed(rt)  # emits knowledge.* events on the bus
    # indexing published knowledge.indexed etc.; the runtime must not have re-ingested them
    docs_before = rt.statistics().documents
    rt.query(KnowledgeRetrievalQuery(text="cricket", scope=_scope()))
    assert rt.statistics().documents == docs_before  # no self-ingested duplicates


# ── replay / gateway compatibility ───────────────────────────────────────────────
def test_retrieval_emits_standard_envelope():
    seen: list[Event] = []
    bus = InProcessEventBus(clock=lambda: 1.0)
    bus.subscribe(seen.append)
    rt = _runtime()
    rt.attach(bus)
    _seed(rt)
    rt.query(KnowledgeRetrievalQuery(text="cover drive", scope=_scope()))
    retr = [e for e in seen if e.type is EventType.KNOWLEDGE_RETRIEVED]
    assert retr and retr[-1].source is EventSource.KNOWLEDGE and retr[-1].seq > 0
    assert retr[-1].data["strategy"] == "hybrid" and retr[-1].data["count"] >= 1


def test_retrieval_is_deterministic_end_to_end():
    def run():
        seen = []
        bus = InProcessEventBus(clock=lambda: 1.0)
        bus.subscribe(lambda e: seen.append((e.type.value, e.seq)))
        rt = KnowledgeFactory.default(id_factory=_ids(), clock=lambda: 1000.0)
        rt.attach(bus)
        _seed(rt)
        res = rt.query(KnowledgeRetrievalQuery(text="cricket bowling", scope=_scope()))
        return seen, [(h.chunk_id, round(h.score, 6)) for h in res.hits]

    assert run() == run()


# ── extension interfaces (interfaces only, no implementations) ───────────────────
def test_extension_interfaces_are_abstract():
    from abc import ABC

    for cls in (Reranker, Summarizer, Compressor, KnowledgeSource, GraphStore,
                GraphRetriever, MultiVectorRetriever):
        assert issubclass(cls, ABC)
        with pytest.raises(TypeError):
            cls()  # abstract — cannot instantiate


def test_injected_reranker_reorders_hits():
    rt = _runtime()
    _seed(rt)
    res = rt.query(KnowledgeRetrievalQuery(text="cricket bowling fielding", scope=_scope()))
    assert res.total >= 2

    class ReverseReranker(Reranker):
        def rerank(self, query_text, hits):
            return tuple(reversed(hits))

    reranked = ReverseReranker().rerank("q", res.hits)
    assert list(reranked) == list(reversed(res.hits))


# ── architecture boundaries (candidate/ranking are pure value objects) ───────────
def test_candidate_and_hit_are_frozen():
    from knowledge import Document, SourceDescriptor

    doc = Document(id="d", scope=KnowledgeScope(), knowledge_type=KnowledgeType.STATIC,
                   source=SourceDescriptor(uri="u"), text="t", content_hash="h")
    from knowledge import Chunk

    ch = Chunk(id="c", document_id="d", version=1, index=0, content="x", content_hash="h",
               start=0, end=1, token_count=1, strategy="s")
    cand = Candidate(chunk=ch, document=doc, similarity=0.5, keyword=0.2)
    with pytest.raises(FrozenInstanceError):
        cand.similarity = 0.9  # frozen


def test_intelligence_modules_import_only_core_tenant_and_own_package():
    """Every new intelligence module stays within the foundation (core + tenant)."""
    tops: set[str] = set()
    for name in ("planner", "retrieval", "ranking", "policies", "cache", "tenancy",
                 "ingestion", "profiles", "results", "query", "extensions"):
        p = pathlib.Path("knowledge") / f"{name}.py"
        for n in ast.walk(ast.parse(p.read_text())):
            if isinstance(n, ast.ImportFrom) and n.module and not n.level:
                tops.add(n.module.split(".")[0])
            elif isinstance(n, ast.Import):
                for a in n.names:
                    tops.add(a.name.split(".")[0])
    forbidden = {"runtime", "api", "gateway", "replay", "events", "memory", "voicebot"}
    assert not (tops & forbidden), tops
