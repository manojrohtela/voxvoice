"""Memory Runtime core tests (M5.1B-1).

Covers the domain model, the `core.fsm` record lifecycle, the serializer round-trip,
the local storage adapters + deterministic embedder, the repository layer, and the
`MemoryRuntime` (sync `MemoryPort` + async core, event emission, module lifecycle,
determinism, and non-ingestion of content events). Retrieval intelligence, ranking,
policies, expiration, and cache are non-goals here and are not tested.
"""

from __future__ import annotations

import ast
import itertools
import pathlib
from dataclasses import FrozenInstanceError

import pytest

from core.errors import ConfigError
from core.events import Event, EventSource, EventType
from core.fsm import FSMError
from events.bus import InProcessEventBus
from memory import (
    LIFECYCLE,
    MemoryConfiguration,
    MemoryFactory,
    MemoryFilter,
    MemoryId,
    MemoryMetadata,
    MemoryQuery,
    MemoryRecord,
    MemoryRepository,
    MemoryScope,
    MemorySerializer,
    MemoryState,
    MemoryTier,
    MemoryTrigger,
    MemoryType,
    MetadataRepository,
    VectorRepository,
    advance,
)
from memory.stores import (
    DeterministicLocalEmbedder,
    InMemoryDocumentStore,
    InMemoryMetadataStore,
    InMemoryVectorStore,
)


def _ids():
    return (f"m{i:02d}" for i in itertools.count()).__next__


def _runtime(**kwargs):
    return MemoryFactory.default(id_factory=_ids(), clock=lambda: 100.0, **kwargs)


def _scope():
    return MemoryScope(session_id="s1", conversation_id="c1", user_id="u1", org_id="o1")


# ── domain ───────────────────────────────────────────────────────────────────
def test_memory_record_is_immutable_and_derives_new_copies():
    r = MemoryRecord(id=MemoryId("a"), content="x", tier=MemoryTier.SESSION, scope=_scope())
    r2 = r.with_state(MemoryState.ACTIVE)
    assert r.state is MemoryState.CREATED and r2.state is MemoryState.ACTIVE
    assert r2 is not r
    with pytest.raises(FrozenInstanceError):
        r.content = "y"  # frozen  # type: ignore[misc]


def test_memory_id_new_uses_injected_factory():
    gen = _ids()
    assert MemoryId.new(gen).value == "m00"
    assert str(MemoryId("z")) == "z"


# ── lifecycle FSM (core.fsm) ─────────────────────────────────────────────────
def test_lifecycle_full_legal_path():
    r = MemoryRecord(id=MemoryId("a"), content="x", tier=MemoryTier.SESSION, scope=_scope())
    for trig, want in [
        (MemoryTrigger.STORE, MemoryState.STORED),
        (MemoryTrigger.ACTIVATE, MemoryState.ACTIVE),
        (MemoryTrigger.ARCHIVE, MemoryState.ARCHIVED),
        (MemoryTrigger.EXPIRE, MemoryState.EXPIRED),
        (MemoryTrigger.DELETE, MemoryState.DELETED),
    ]:
        r = advance(r, trig)
        assert r.state is want


def test_lifecycle_illegal_transition_raises():
    r = MemoryRecord(id=MemoryId("a"), content="x", tier=MemoryTier.SESSION, scope=_scope())
    with pytest.raises(FSMError):
        advance(r, MemoryTrigger.DELETE)  # cannot delete from CREATED


def test_lifecycle_definition_has_deleted_terminal():
    assert MemoryState.DELETED in LIFECYCLE.terminal_states()


# ── serializer ───────────────────────────────────────────────────────────────
def test_serializer_document_round_trip():
    s = MemorySerializer()
    r = MemoryRecord(
        id=MemoryId("a"),
        content="hello",
        tier=MemoryTier.SEMANTIC,
        scope=_scope(),
        state=MemoryState.ACTIVE,
        metadata=MemoryMetadata(
            type=MemoryType.FACT, importance=0.7, tags=("t1", "t2"), created_at=1.0, updated_at=2.0
        ),
        embedded=True,
    )
    assert s.from_document(s.to_document(r)) == r


def test_serializer_event_payload_is_privacy_lean():
    s = MemorySerializer()
    r = MemoryRecord(id=MemoryId("a"), content="secret text", tier=MemoryTier.USER, scope=_scope())
    payload = s.to_event_payload(r)
    assert payload["id"] == "a" and payload["content_length"] == len("secret text")
    assert "content" not in payload  # content is not leaked onto the bus


# ── local adapters + embedder ────────────────────────────────────────────────
async def test_document_store_crud_and_query_isolated():
    store = InMemoryDocumentStore()
    await store.upsert("c", "1", {"tier": "session", "state": "active"})
    got = await store.get("c", "1")
    assert got == {"tier": "session", "state": "active"}
    got["tier"] = "mutated"  # must not affect stored copy
    assert (await store.get("c", "1"))["tier"] == "session"
    assert len(await store.query("c", {"tier": "session"})) == 1
    assert len(await store.query("c", {"tier": "user"})) == 0
    await store.delete("c", "1")
    assert await store.get("c", "1") is None


async def test_vector_store_upsert_search_remove():
    store = InMemoryVectorStore()
    await store.upsert("a", (1.0, 0.0), metadata={"k": "v"})
    await store.upsert("b", (0.0, 1.0))
    matches = await store.search((1.0, 0.0), k=2)
    assert matches[0].id == "a" and matches[0].score > matches[1].score
    await store.remove("a")
    assert [m.id for m in await store.search((1.0, 0.0), k=2)] == ["b"]


def test_deterministic_embedder_is_pure_and_normalized():
    e = DeterministicLocalEmbedder(dimensions=32)
    v1 = e.embed("cover drive")
    v2 = e.embed("cover drive")
    assert v1 == v2 and len(v1) == 32
    assert abs(sum(x * x for x in v1) ** 0.5 - 1.0) < 1e-9
    assert e.embed("pull shot") != v1
    assert e.embed("") == tuple([0.0] * 32)


# ── repositories ─────────────────────────────────────────────────────────────
async def test_memory_repository_crud_and_filter():
    repo = MemoryRepository(InMemoryDocumentStore(), MemorySerializer())
    a = MemoryRecord(
        id=MemoryId("a"), content="x", tier=MemoryTier.SESSION, scope=_scope(),
        state=MemoryState.ACTIVE, metadata=MemoryMetadata(tags=("k",), created_at=2.0),
    )
    b = MemoryRecord(
        id=MemoryId("b"), content="y", tier=MemoryTier.USER, scope=_scope(),
        state=MemoryState.ACTIVE, metadata=MemoryMetadata(created_at=1.0),
    )
    await repo.add(a)
    await repo.add(b)
    assert (await repo.get(MemoryId("a"))) == a
    # tier filter
    got = await repo.query(MemoryFilter(tier=MemoryTier.SESSION, states=(MemoryState.ACTIVE,)))
    assert [r.id.value for r in got] == ["a"]
    # tag filter
    tagged = await repo.query(MemoryFilter(tags=("k",)))
    assert [r.id.value for r in tagged] == ["a"]
    # ordering newest-first across tiers
    ordered = await repo.query(MemoryFilter(states=(MemoryState.ACTIVE,)))
    assert [r.id.value for r in ordered] == ["a", "b"]
    await repo.remove(MemoryId("a"))
    assert await repo.get(MemoryId("a")) is None


async def test_memory_repository_scope_isolation():
    repo = MemoryRepository(InMemoryDocumentStore(), MemorySerializer())
    mine = MemoryScope(user_id="u1", org_id="o1")
    other = MemoryScope(user_id="u2", org_id="o1")
    await repo.add(MemoryRecord(id=MemoryId("a"), content="x", tier=MemoryTier.USER, scope=mine,
                               state=MemoryState.ACTIVE))
    await repo.add(MemoryRecord(id=MemoryId("b"), content="y", tier=MemoryTier.USER, scope=other,
                               state=MemoryState.ACTIVE))
    got = await repo.query(MemoryFilter(scope=mine))
    assert [r.id.value for r in got] == ["a"]  # u2's record is not visible


async def test_vector_and_metadata_repositories():
    vrepo = VectorRepository(InMemoryVectorStore(), DeterministicLocalEmbedder(dimensions=16))
    vec = await vrepo.index("a", "cover drive")
    assert len(vec) == 16
    await vrepo.remove("a")  # idempotent
    mrepo = MetadataRepository(InMemoryMetadataStore())
    assert await mrepo.incr("count") == 1
    assert await mrepo.incr("count", 2) == 3
    assert await mrepo.get_int("missing", default=7) == 7


# ── runtime: sync MemoryPort ─────────────────────────────────────────────────
def test_runtime_store_retrieve_forget():
    rt = _runtime()
    mid = rt.store("cover drive", tier=MemoryTier.SEMANTIC, scope=_scope(), importance=0.9)
    hits = rt.retrieve(MemoryQuery(tier=MemoryTier.SEMANTIC, scope=_scope()))
    # score is now a ranked blend (M5.1B-2), not raw importance
    assert [(h.id, h.content) for h in hits] == [(mid, "cover drive")]
    assert hits[0].score > 0
    rt.forget(mid)
    assert list(rt.retrieve(MemoryQuery(tier=MemoryTier.SEMANTIC, scope=_scope()))) == []
    rt.forget(mid)  # idempotent: no raise


def test_runtime_retrieve_respects_ranking_min_score_and_scope():
    rt = _runtime()
    rt.store("low", tier=MemoryTier.SESSION, scope=_scope(), importance=0.2)
    rt.store("high", tier=MemoryTier.SESSION, scope=_scope(), importance=0.8)
    # ranked by importance-weighted score → high before low
    hits = rt.retrieve(MemoryQuery(tier=MemoryTier.SESSION, scope=_scope()))
    assert [h.content for h in hits] == ["high", "low"]
    # min_score thresholds the ranked score; an unreachable threshold drops all
    dropped = rt.retrieve(MemoryQuery(tier=MemoryTier.SESSION, scope=_scope(), min_score=1e9))
    assert list(dropped) == []
    # other user sees nothing (scope isolation)
    other = rt.retrieve(MemoryQuery(tier=MemoryTier.SESSION, scope=MemoryScope(user_id="ux")))
    assert list(other) == []


async def test_runtime_semantic_tier_is_embedded():
    rt = _runtime()
    mid = await rt.astore("indexed", tier=MemoryTier.SEMANTIC, scope=_scope())
    got = await rt._memory.get(MemoryId(mid))
    assert got is not None and got.embedded is True


# ── runtime: event emission (Replay/Gateway compatible) ──────────────────────
def test_runtime_emits_memory_events_on_bus():
    seen: list[Event] = []
    bus = InProcessEventBus(clock=lambda: 1.0)
    bus.subscribe(seen.append)
    rt = _runtime()
    rt.attach(bus)
    mid = rt.store("x", tier=MemoryTier.SEMANTIC, scope=_scope())
    rt.retrieve(MemoryQuery(tier=MemoryTier.SEMANTIC, scope=_scope()))
    rt.forget(mid)
    types = [e.type for e in seen]
    assert types == [EventType.MEMORY_STORED, EventType.MEMORY_RETRIEVED, EventType.MEMORY_CLEARED]
    # standard envelopes: source stamped, seq assigned monotonically (replay/gateway see them free)
    assert all(e.source is EventSource.MEMORY for e in seen)
    assert [e.seq for e in seen] == [1, 2, 3]
    # retrieved payload carries ids, not content (privacy)
    assert seen[1].data["ids"] == [mid] and "content" not in seen[1].data


def test_runtime_retrieve_event_carries_scope_ids():
    seen: list[Event] = []
    bus = InProcessEventBus()
    bus.subscribe(seen.append)
    rt = _runtime()
    rt.attach(bus)
    rt.store("x", tier=MemoryTier.SESSION, scope=_scope())
    assert seen[-1].session_id == "s1" and seen[-1].org_id == "o1"
    assert seen[-1].conversation_id == "c1"


def test_runtime_ingests_content_events():
    # M5.1B-2: the runtime now forms memory from content events (was deferred in -1)
    bus = InProcessEventBus()
    rt = _runtime()
    rt.attach(bus)
    bus.publish(Event(id="x", type=EventType.TRANSCRIPT_COMPLETED, ts=1.0, seq=0,
                      session_id="s1", source=EventSource.INTERACTION, data={"text": "hi there"}))
    assert rt.metrics.handled == 1
    assert rt.metrics.extra.get("ingested") == 1


# ── runtime: module lifecycle ────────────────────────────────────────────────
async def test_runtime_module_lifecycle_and_health():
    from core.module import ModuleState
    rt = _runtime()
    assert not rt.health().healthy
    await rt.initialize()
    await rt.start()
    assert rt.state is ModuleState.RUNNING and rt.health().healthy
    await rt.shutdown()
    assert rt.state is ModuleState.STOPPED


async def test_runtime_shutdown_detaches_from_bus():
    bus = InProcessEventBus()
    rt = _runtime()
    rt.attach(bus)
    assert rt.attached
    await rt.initialize()
    await rt.shutdown()
    assert not rt.attached


# ── determinism ──────────────────────────────────────────────────────────────
def test_two_runtimes_same_seed_produce_identical_events():
    def run() -> list[tuple]:
        seen: list[tuple] = []
        bus = InProcessEventBus(clock=lambda: 1.0)
        bus.subscribe(lambda e: seen.append((e.type.value, e.seq, tuple(sorted(e.data.items())))))
        rt = MemoryFactory.default(id_factory=_ids(), clock=lambda: 100.0)
        rt.attach(bus)
        mid = rt.store("cover drive", tier=MemoryTier.SEMANTIC, scope=_scope(), importance=0.5)
        rt.retrieve(MemoryQuery(tier=MemoryTier.SEMANTIC, scope=_scope()))
        rt.forget(mid)
        return seen

    assert run() == run()


# ── configuration + factory ──────────────────────────────────────────────────
def test_config_default_is_in_memory_and_vectorizes_semantic():
    cfg = MemoryConfiguration.default()
    assert cfg.repository.document.kind == "in_memory"
    assert cfg.tier(MemoryTier.SEMANTIC).vectorize is True
    assert cfg.tier(MemoryTier.SESSION).vectorize is False


def test_config_validation_rejects_bad_values():
    from memory import EmbedderConfig, TierConfig
    with pytest.raises(ConfigError):
        EmbedderConfig(dimensions=0)
    with pytest.raises(ConfigError):
        MemoryConfiguration(tiers=(TierConfig(MemoryTier.USER), TierConfig(MemoryTier.USER)))


def test_factory_rejects_unknown_store_kind():
    from memory import RepositoryConfig, StoreConfig
    cfg = MemoryConfiguration(repository=RepositoryConfig(document=StoreConfig(kind="postgres")))
    with pytest.raises(ConfigError):
        MemoryFactory.create(cfg)


# ── dependency rules (memory stays acyclic) ──────────────────────────────────
def _imports(pkg: str) -> set[str]:
    tops: set[str] = set()
    for p in pathlib.Path(pkg).rglob("*.py"):
        for n in ast.walk(ast.parse(p.read_text())):
            if isinstance(n, ast.ImportFrom) and n.module and not n.level:
                # skip relative imports (level>0): they're within-package by
                # definition and may shadow a top-level name (e.g. memory/runtime.py)
                tops.add(n.module.split(".")[0])
            elif isinstance(n, ast.Import):
                for a in n.names:
                    tops.add(a.name.split(".")[0])
    return tops


def test_memory_dependency_rules():
    # memory depends only on core (+ its own package); never on sibling runtimes,
    # the bus implementation, the API, gateway, or replay.
    forbidden = {"runtime", "api", "gateway", "replay", "events", "voicebot", "observability"}
    assert not (_imports("memory") & forbidden), _imports("memory")
