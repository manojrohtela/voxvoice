"""Memory Runtime intelligence tests (M5.1B-2).

Covers the planner, retrieval (keyword/semantic/hybrid + all scopes), ranking, the
policy engine (validation/privacy/merge/expiration/capacity/retention), the cache,
tenant isolation, event ingestion, metadata evolution, observability, extension points,
replay/gateway compatibility, and determinism.
"""

from __future__ import annotations

import itertools

import pytest

from core.events import Event, EventSource, EventType
from core.policies import BudgetPolicy
from events.bus import InProcessEventBus
from memory import (
    CacheConfig,
    ConflictStrategy,
    ExpirationPolicy,
    LRUCacheBackend,
    MemoryAccessDenied,
    MemoryCache,
    MemoryConfiguration,
    MemoryFactory,
    MemoryFilter,
    MemoryId,
    MemoryPolicies,
    MemoryQuery,
    MemoryQueryPlanner,
    MemoryRecord,
    MemoryRepository,
    MemoryScope,
    MemoryState,
    MemoryTier,
    MergePolicy,
    PolicyEngine,
    PrivacyPolicy,
    RankingEngine,
    Redactor,
    RepositoryConfig,
    Reranker,
    RetentionPolicy,
    RetrievalStrategy,
    StoreConfig,
    TierConfig,
)
from memory.serializer import MemorySerializer
from memory.stores import InMemoryDocumentStore
from tenant import OrganizationContext, TenantContext


def _ids():
    return (f"m{i:02d}" for i in itertools.count()).__next__


def _runtime(config=None, **kwargs):
    cfg = config or MemoryConfiguration.default()
    kwargs.setdefault("clock", lambda: 100.0)
    return MemoryFactory.create(cfg, id_factory=_ids(), **kwargs)


def _scope(**kw):
    base = dict(session_id="s1", conversation_id="c1", user_id="u1", org_id="o1")
    base.update(kw)
    return MemoryScope(**base)


# ── planner ──────────────────────────────────────────────────────────────────
def test_planner_strategy_selection():
    p = MemoryQueryPlanner()
    assert p.plan(MemoryQuery(scope=_scope())).strategy is RetrievalStrategy.KEYWORD
    sem = p.plan(MemoryQuery(text="x", tier=MemoryTier.SEMANTIC)).strategy
    assert sem is RetrievalStrategy.SEMANTIC
    assert p.plan(MemoryQuery(text="x", tags=("t",))).strategy is RetrievalStrategy.HYBRID
    assert p.plan(MemoryQuery(text="x")).strategy is RetrievalStrategy.KEYWORD


def test_planner_builds_scoped_active_filter():
    p = MemoryQueryPlanner()
    plan = p.plan(MemoryQuery(text="Cover Drive", scope=_scope(), tags=("a",)), agent_id="ag1")
    assert plan.filter.states == (MemoryState.ACTIVE,)
    assert plan.filter.agent_id == "ag1" and plan.filter.limit is None
    assert plan.terms == ("cover", "drive")  # normalized + tokenized


# ── ranking ──────────────────────────────────────────────────────────────────
def test_ranking_is_deterministic_and_ordered():
    eng = RankingEngine(clock=lambda: 1000.0)
    recs = [
        MemoryRecord(id=MemoryId("a"), content="x", tier=MemoryTier.SESSION, scope=_scope()),
        MemoryRecord(id=MemoryId("b"), content="y", tier=MemoryTier.SESSION, scope=_scope()),
    ]
    sim = {"a": 0.1, "b": 0.9}
    r1 = eng.rank(recs, similarity=sim, conversation_scope=_scope())
    r2 = eng.rank(recs, similarity=sim, conversation_scope=_scope())
    assert [s.record.id.value for s in r1] == [s.record.id.value for s in r2]
    assert r1[0].record.id.value == "b"  # higher similarity wins


def test_ranking_recency_decays_with_clock():
    eng = RankingEngine(clock=lambda: 1000.0, half_life_s=100.0)
    fresh = MemoryRecord(id=MemoryId("a"), content="x", tier=MemoryTier.SESSION, scope=_scope())
    old = MemoryRecord(id=MemoryId("b"), content="x", tier=MemoryTier.SESSION, scope=_scope())
    from dataclasses import replace
    fresh = replace(fresh, metadata=replace(fresh.metadata, created_at=1000.0))
    old = replace(old, metadata=replace(old.metadata, created_at=900.0))  # one half-life older
    scored = {s.record.id.value: s.signals["recency"] for s in eng.rank([fresh, old])}
    assert scored["a"] == pytest.approx(1.0) and scored["b"] == pytest.approx(0.5)


# ── retrieval: keyword / semantic / hybrid ───────────────────────────────────
def test_keyword_retrieval_ranks_overlap():
    rt = _runtime()
    rt.store("the cover drive shot", tier=MemoryTier.SESSION, scope=_scope(), importance=0.5)
    rt.store("pull to the leg side", tier=MemoryTier.SESSION, scope=_scope(), importance=0.5)
    hits = rt.retrieve(MemoryQuery(text="cover drive", tier=MemoryTier.SESSION, scope=_scope()))
    assert hits[0].content == "the cover drive shot"


def test_semantic_retrieval_uses_vectors_and_isolates_scope():
    rt = _runtime()
    a = _scope(org_id="o1", user_id="u1")
    b = _scope(org_id="o2", user_id="u2")
    rt.store("elegant cover drive", tier=MemoryTier.SEMANTIC, scope=a, importance=0.5)
    rt.store("elegant cover drive", tier=MemoryTier.SEMANTIC, scope=b, importance=0.5)
    hits = rt.retrieve(MemoryQuery(text="cover drive", tier=MemoryTier.SEMANTIC, scope=a))
    assert [h.id for h in hits] == ["m00"]  # b's identical-vector record is isolated out


async def test_hybrid_retrieval_combines_signals():
    cfg = MemoryConfiguration(
        tiers=(TierConfig(MemoryTier.CONVERSATION, vectorize=True),),
        cache=CacheConfig(enabled=True),
    )
    rt = _runtime(cfg)
    await rt.astore("cover drive masterclass", tier=MemoryTier.CONVERSATION, scope=_scope(),
                    importance=0.5, tags=("cricket",))
    q = MemoryQuery(text="cover drive", tier=MemoryTier.CONVERSATION, scope=_scope(),
                    tags=("cricket",))
    plan = MemoryQueryPlanner().plan(q)
    assert plan.strategy is RetrievalStrategy.HYBRID
    res = await rt.aretrieve(q)
    assert len(res.hits) == 1


def test_topk_limits_results():
    rt = _runtime()
    for i in range(5):
        rt.store(f"note {i}", tier=MemoryTier.SESSION, scope=_scope(), importance=0.5)
    assert len(rt.retrieve(MemoryQuery(tier=MemoryTier.SESSION, scope=_scope(), limit=3))) == 3


def test_retrieval_scopes_conversation_session_user_org():
    rt = _runtime()
    rt.store("conv-a", tier=MemoryTier.SESSION, scope=_scope(conversation_id="cA"))
    rt.store("conv-b", tier=MemoryTier.SESSION, scope=_scope(conversation_id="cB"))
    hits = rt.retrieve(MemoryQuery(tier=MemoryTier.SESSION, scope=_scope(conversation_id="cA")))
    assert [h.content for h in hits] == ["conv-a"]


async def test_agent_scope_isolation():
    rt = _runtime()
    await rt.astore("agent-1 memory", tier=MemoryTier.SESSION, scope=_scope(), agent_id="ag1")
    await rt.astore("agent-2 memory", tier=MemoryTier.SESSION, scope=_scope(), agent_id="ag2")
    # query as agent ag1 via tenant context → only ag1's memory
    tenant = TenantContext(org=OrganizationContext(org_id="o1"), user_id="u1", agent_id="ag1")
    res = await rt.aretrieve(MemoryQuery(tier=MemoryTier.SESSION, scope=_scope()), tenant=tenant)
    assert [h.content for h in res.hits] == ["agent-1 memory"]


# ── tenancy / isolation ──────────────────────────────────────────────────────
async def test_tenancy_denies_cross_org():
    rt = _runtime()
    tenant = TenantContext(org=OrganizationContext(org_id="o1"), user_id="u1")
    with pytest.raises(MemoryAccessDenied):
        await rt.aretrieve(MemoryQuery(scope=MemoryScope(org_id="o2")), tenant=tenant)


async def test_tenancy_denies_cross_user():
    rt = _runtime()
    tenant = TenantContext(org=OrganizationContext(org_id="o1"), user_id="u1")
    with pytest.raises(MemoryAccessDenied):
        await rt.aretrieve(MemoryQuery(scope=MemoryScope(org_id="o1", user_id="u2")), tenant=tenant)


# ── policy engine ────────────────────────────────────────────────────────────
def test_policy_rejects_empty_content():
    rt = _runtime()
    assert rt.store("   ", tier=MemoryTier.SESSION, scope=_scope()) == ""
    assert rt.metrics.extra.get("rejected") == 1


def test_policy_privacy_redacts_before_store():
    class UpperRedactor(Redactor):
        def redact(self, content: str) -> str:
            return content.replace("secret", "[REDACTED]")

    policy = PolicyEngine(
        MemoryPolicies(privacy=PrivacyPolicy(enabled=True)), redactor=UpperRedactor()
    )
    rt = _runtime(policy=policy)
    mid = rt.store("my secret pin", tier=MemoryTier.SESSION, scope=_scope())
    hits = rt.retrieve(MemoryQuery(tier=MemoryTier.SESSION, scope=_scope()))
    assert hits[0].content == "my [REDACTED] pin" and hits[0].id == mid


def test_policy_merge_dedupes_identical_content():
    seen: list[Event] = []
    bus = InProcessEventBus()
    bus.subscribe(seen.append)
    rt = _runtime()
    rt.attach(bus)
    a = rt.store("same text", tier=MemoryTier.SESSION, scope=_scope(), importance=0.4)
    b = rt.store("same text", tier=MemoryTier.SESSION, scope=_scope(), importance=0.9)
    assert a == b  # merged into the same record
    hits = rt.retrieve(MemoryQuery(tier=MemoryTier.SESSION, scope=_scope()))
    assert len(hits) == 1
    types = [e.type for e in seen if e.source is EventSource.MEMORY]
    assert EventType.MEMORY_STORED in types and EventType.MEMORY_UPDATED in types


async def test_policy_expiration_sweep():
    policy = PolicyEngine(
        MemoryPolicies(expiration={MemoryTier.SESSION: ExpirationPolicy(ttl_seconds=10.0)})
    )
    clock = {"t": 100.0}
    rt = _runtime(policy=policy, clock=lambda: clock["t"])
    seen: list[Event] = []
    bus = InProcessEventBus()
    bus.subscribe(seen.append)
    rt.attach(bus)
    rt.store("ephemeral", tier=MemoryTier.SESSION, scope=_scope())
    clock["t"] = 200.0  # well past ttl
    expired = await rt.expire(tier=MemoryTier.SESSION)
    assert expired == 1
    assert any(e.type is EventType.MEMORY_EXPIRED for e in seen)
    assert list(rt.retrieve(MemoryQuery(tier=MemoryTier.SESSION, scope=_scope()))) == []


def test_policy_capacity_evicts_lowest_importance():
    seen: list[Event] = []
    bus = InProcessEventBus()
    bus.subscribe(seen.append)
    policy = PolicyEngine(
        MemoryPolicies(capacity={MemoryTier.SESSION: BudgetPolicy(limit=1, unit="items")})
    )
    rt = _runtime(policy=policy)
    rt.attach(bus)
    rt.store("keep-me", tier=MemoryTier.SESSION, scope=_scope(), importance=0.9)
    rt.store("evict-me", tier=MemoryTier.SESSION, scope=_scope(), importance=0.1)
    assert any(e.type is EventType.MEMORY_EVICTED for e in seen)
    hits = rt.retrieve(MemoryQuery(tier=MemoryTier.SESSION, scope=_scope()))
    assert [h.content for h in hits] == ["keep-me"]


async def test_policy_retention_pins_protect_from_eviction():
    policy = PolicyEngine(MemoryPolicies(
        capacity={MemoryTier.SESSION: BudgetPolicy(limit=1, unit="items")},
        retention=RetentionPolicy(pinned_tags=("pinned",)),
    ))
    rt = _runtime(policy=policy)
    await rt.astore("pinned one", tier=MemoryTier.SESSION, scope=_scope(), importance=0.1,
                    tags=("pinned",))
    await rt.astore("normal two", tier=MemoryTier.SESSION, scope=_scope(), importance=0.9)
    res = await rt.aretrieve(MemoryQuery(tier=MemoryTier.SESSION, scope=_scope()))
    contents = {h.content for h in res.hits}
    assert "pinned one" in contents  # protected despite lowest importance


async def test_conflict_strategy_keep_highest_importance():
    from memory import ConflictPolicy
    policy = PolicyEngine(MemoryPolicies(
        merge=MergePolicy(enabled=True),
        conflict=ConflictPolicy(strategy=ConflictStrategy.KEEP_HIGHEST_IMPORTANCE),
    ))
    rt = _runtime(policy=policy)
    rt.store("dup", tier=MemoryTier.SESSION, scope=_scope(), importance=0.3)
    rt.store("dup", tier=MemoryTier.SESSION, scope=_scope(), importance=0.7)
    rec = await rt._memory.get(MemoryId("m00"))
    assert rec.metadata.importance == 0.7


# ── cache ────────────────────────────────────────────────────────────────────
def test_lru_cache_backend_evicts_least_recently_used():
    c = LRUCacheBackend(maxsize=2)
    c.put("a", {"v": 1})
    c.put("b", {"v": 2})
    c.get("a")  # a is now MRU
    c.put("c", {"v": 3})  # evicts b (LRU)
    assert c.get("a") is not None and c.get("c") is not None and c.get("b") is None
    assert c.evictions == 1


def test_memory_cache_tracks_hits_misses_and_invalidation():
    cache = MemoryCache(maxsize=8)
    assert cache.get("x") is None  # miss
    cache.put("x", {"id": "x"})
    assert cache.get("x") == {"id": "x"}  # hit
    cache.invalidate("x")
    assert cache.get("x") is None  # miss again
    snap = cache.snapshot()
    assert snap["hits"] == 1 and snap["misses"] == 2 and snap["invalidations"] == 1


async def test_repository_read_through_and_invalidation():
    cache = MemoryCache(maxsize=8)
    repo = MemoryRepository(InMemoryDocumentStore(), MemorySerializer(), cache=cache)
    rec = MemoryRecord(id=MemoryId("a"), content="x", tier=MemoryTier.SESSION, scope=_scope(),
                       state=MemoryState.ACTIVE)
    await repo.add(rec)  # write-through
    assert cache.get("a") is not None
    await repo.remove(MemoryId("a"))  # invalidates
    assert cache.get("a") is None


# ── event ingestion ──────────────────────────────────────────────────────────
def _publish(bus, type, data, **kw):
    bus.publish(Event(id=kw.get("id", "e"), type=type, ts=1.0, seq=0,
                      session_id=kw.get("session_id", "s1"), source=EventSource.INTERACTION,
                      org_id="o1", conversation_id="c1", data=data))


async def test_ingestion_forms_conversation_semantic_episodic():
    rt = _runtime()
    bus = InProcessEventBus()
    rt.attach(bus)
    _publish(bus, EventType.TRANSCRIPT_COMPLETED, {"text": "user said hello"}, id="e1")
    _publish(bus, EventType.REASONING_COMPLETED, {"summary": "assistant decided to greet"}, id="e2")
    _publish(bus, EventType.TOOL_COMPLETED, {"result": "weather is sunny"}, id="e3")
    tiers = set()
    for tier in (MemoryTier.CONVERSATION, MemoryTier.SEMANTIC, MemoryTier.EPISODIC):
        recs = await rt._memory.query(MemoryFilter(tier=tier, states=(MemoryState.ACTIVE,)))
        if recs:
            tiers.add(tier)
    assert tiers == {MemoryTier.CONVERSATION, MemoryTier.SEMANTIC, MemoryTier.EPISODIC}


async def test_ingestion_context_assembled_bumps_access():
    rt = _runtime()
    bus = InProcessEventBus()
    seen: list[Event] = []
    bus.subscribe(seen.append)
    rt.attach(bus)
    mid = rt.store("recallable", tier=MemoryTier.SESSION, scope=_scope())
    _publish(bus, EventType.CONTEXT_ASSEMBLED, {"memory_ids": [mid]}, id="e9")
    rec = await rt._memory.get(MemoryId(mid))
    assert rec.metadata.access_count == 1
    assert any(e.type is EventType.MEMORY_UPDATED for e in seen)


def test_ingestion_ignores_blank_content():
    rt = _runtime()
    bus = InProcessEventBus()
    rt.attach(bus)
    _publish(bus, EventType.TRANSCRIPT_COMPLETED, {"text": "   "})
    assert rt.metrics.extra.get("ingested") is None


# ── metadata evolution ───────────────────────────────────────────────────────
def test_metadata_new_fields_round_trip_and_backward_compatible():
    from memory import MemoryMetadata
    s = MemorySerializer()
    rec = MemoryRecord(
        id=MemoryId("a"), content="x", tier=MemoryTier.USER, scope=_scope(),
        metadata=MemoryMetadata(confidence=0.3, origin="tool", created_by="u1", agent_id="ag1"),
    )
    assert s.from_document(s.to_document(rec)) == rec
    # a legacy document without the new keys still deserializes with defaults
    legacy = {"id": "b", "content": "y", "tier": "user", "state": "active",
              "scope": {}, "metadata": {"type": "note", "importance": 0.5}}
    got = s.from_document(legacy)
    assert got.metadata.confidence == 1.0 and got.metadata.agent_id is None


# ── observability ────────────────────────────────────────────────────────────
def test_statistics_reports_cache_and_metrics():
    from telemetry import Telemetry
    rt = _runtime(meter=Telemetry(clock=lambda: 0.0))
    rt.store("x", tier=MemoryTier.SESSION, scope=_scope())
    rt.retrieve(MemoryQuery(tier=MemoryTier.SESSION, scope=_scope()))
    stats = rt.statistics()
    assert stats.healthy is False  # not started
    assert stats.cache is not None and "hits" in stats.cache
    assert stats.metrics["extra"]["stored"] == 1


# ── extension points ─────────────────────────────────────────────────────────
def test_extension_interfaces_are_abstract():
    for cls in (Reranker, Redactor):
        with pytest.raises(TypeError):
            cls()  # abstract — cannot instantiate


# ── replay / gateway compatibility + determinism ─────────────────────────────
def test_ingestion_and_retrieval_events_are_standard_envelopes():
    seen: list[Event] = []
    bus = InProcessEventBus(clock=lambda: 1.0)
    bus.subscribe(seen.append)
    rt = _runtime()
    rt.attach(bus)
    _publish(bus, EventType.TRANSCRIPT_COMPLETED, {"text": "hello"})
    rt.retrieve(MemoryQuery(text="hello", tier=MemoryTier.CONVERSATION, scope=_scope()))
    mem_events = [e for e in seen if e.source is EventSource.MEMORY]
    assert mem_events, "memory events must reach the bus for Replay/Gateway"
    assert all(e.seq > 0 for e in mem_events)  # bus-assigned ordering


def test_full_cycle_is_deterministic():
    def run():
        seen = []
        bus = InProcessEventBus(clock=lambda: 1.0)
        bus.subscribe(lambda e: seen.append((e.type.value, e.seq, tuple(sorted(e.data.items())))))
        rt = MemoryFactory.default(id_factory=_ids(), clock=lambda: 100.0)
        rt.attach(bus)
        _publish(bus, EventType.TRANSCRIPT_COMPLETED, {"text": "cover drive"})
        rt.retrieve(MemoryQuery(text="cover drive", tier=MemoryTier.CONVERSATION, scope=_scope()))
        return seen

    assert run() == run()


# ── config plumbing ──────────────────────────────────────────────────────────
def test_factory_wires_cache_when_enabled():
    cfg = MemoryConfiguration(
        repository=RepositoryConfig(document=StoreConfig(kind="in_memory")),
        cache=CacheConfig(enabled=True, max_items=4),
    )
    rt = _runtime(cfg)
    assert rt.statistics().cache is not None
