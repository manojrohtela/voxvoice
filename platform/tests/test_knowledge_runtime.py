"""Knowledge Runtime core tests (M5.2B-1).

Covers deterministic ids, the document + chunk lifecycles (`core.fsm`), the serializer
round-trips, repository isolation, versioning + dedup, the document pipeline, the
runtime surface (index/get/remove/retrieve-placeholder/sources/statistics), runtime
lifecycle, factory + configuration, replay/gateway compatibility, and dependency rules.
Retrieval intelligence, ranking, policies, cache, and ingestion are non-goals here.
"""

from __future__ import annotations

import ast
import itertools
import pathlib

import pytest

from core.errors import ConfigError
from core.events import Event, EventSource, EventType
from core.fsm import FSMError
from core.ports.knowledge import KnowledgeQuery
from events.bus import InProcessEventBus
from knowledge import (
    Chunk,
    ChunkState,
    ChunkTrigger,
    Document,
    DocumentState,
    DocumentTrigger,
    EmbedderConfig,
    KnowledgeConfiguration,
    KnowledgeFactory,
    KnowledgeScope,
    KnowledgeSerializer,
    KnowledgeType,
    SourceDescriptor,
    advance_chunk,
    advance_document,
    chunk_id_for,
    content_hash,
    document_id_for,
    version_id_for,
)
from knowledge.config import ChunkConfig
from knowledge.pipeline import SlidingWindowChunker
from knowledge.stores import InMemoryDocumentStore, InMemoryObjectStore

TEXT = "The cover drive is an elegant cricket shot played along the ground off side. " * 4


def _ids():
    return (f"k{i:02d}" for i in itertools.count()).__next__


def _runtime(config=None, **kwargs):
    cfg = config or KnowledgeConfiguration.default()
    kwargs.setdefault("clock", lambda: 100.0)
    return KnowledgeFactory.create(cfg, id_factory=_ids(), **kwargs)


def _scope(**kw):
    base = dict(org_id="o1")
    base.update(kw)
    return KnowledgeScope(**base)


# ── deterministic ids ────────────────────────────────────────────────────────
def test_ids_are_deterministic_and_content_derived():
    assert document_id_for("doc://a") == document_id_for("doc://a")
    assert document_id_for("doc://a") != document_id_for("doc://b")
    assert content_hash("x", "y") == content_hash("x", "y")
    assert content_hash("x", "y") != content_hash("y", "x")
    assert version_id_for("doc_1", 2) == "doc_1.v2"
    assert chunk_id_for("d", 1, 0, "hello") == chunk_id_for("d", 1, 0, "hello")
    assert chunk_id_for("d", 1, 0, "a") != chunk_id_for("d", 1, 1, "a")


# ── lifecycles (core.fsm) ────────────────────────────────────────────────────
def _doc(state=DocumentState.IMPORTED):
    return Document(id="d", scope=_scope(), knowledge_type=KnowledgeType.STATIC,
                    source=SourceDescriptor(uri="u"), text="t", content_hash="h", state=state)


def test_document_lifecycle_full_path():
    d = _doc()
    for trig, want in [
        (DocumentTrigger.VALIDATE, DocumentState.VALIDATED),
        (DocumentTrigger.PARSE, DocumentState.PARSED),
        (DocumentTrigger.CHUNK, DocumentState.CHUNKED),
        (DocumentTrigger.EMBED, DocumentState.EMBEDDED),
        (DocumentTrigger.INDEX, DocumentState.INDEXED),
        (DocumentTrigger.ACTIVATE, DocumentState.ACTIVE),
        (DocumentTrigger.ARCHIVE, DocumentState.ARCHIVED),
        (DocumentTrigger.DELETE, DocumentState.DELETED),
    ]:
        d = advance_document(d, trig)
        assert d.state is want


def test_document_illegal_transition_raises():
    with pytest.raises(FSMError):
        advance_document(_doc(), DocumentTrigger.INDEX)  # cannot index from IMPORTED


def test_chunk_lifecycle_and_illegal():
    c = Chunk(id="c", document_id="d", version=1, index=0, content="x", content_hash="h",
              start=0, end=1, token_count=1, strategy="sliding_window")
    c = advance_chunk(c, ChunkTrigger.EMBED)
    c = advance_chunk(c, ChunkTrigger.INDEX)
    assert c.state is ChunkState.INDEXED
    with pytest.raises(FSMError):
        advance_chunk(Chunk(id="c2", document_id="d", version=1, index=0, content="x",
                            content_hash="h", start=0, end=1, token_count=1,
                            strategy="s"), ChunkTrigger.INDEX)  # cannot index from CREATED


# ── serializer round-trips ───────────────────────────────────────────────────
def test_serializer_document_round_trip():
    s = KnowledgeSerializer()
    d = _doc(DocumentState.ACTIVE).with_chunks(("c1", "c2"))
    assert s.document_from_document(s.document_to_document(d)) == d


def test_serializer_chunk_and_version_round_trip():
    s = KnowledgeSerializer()
    c = Chunk(id="c", document_id="d", version=2, index=1, content="x", content_hash="h",
              start=3, end=4, token_count=1, strategy="sliding_window",
              state=ChunkState.INDEXED, embedded=True, prev_id="c0")
    assert s.chunk_from_document(s.chunk_to_document(c)) == c


# ── chunker (deterministic) ──────────────────────────────────────────────────
def test_sliding_window_chunker_is_deterministic():
    ch = SlidingWindowChunker(size=5, overlap=2)
    a = ch.chunk("one two three four five six seven eight")
    b = ch.chunk("one two three four five six seven eight")
    assert [s.content for s in a] == [s.content for s in b]
    assert len(a) >= 2 and a[0].token_count == 5


# ── repositories: isolation + versioning ─────────────────────────────────────
async def test_document_repository_persists_and_isolates_collections():
    from knowledge.repositories import DocumentRepository
    repo = DocumentRepository(InMemoryDocumentStore(), InMemoryObjectStore(), KnowledgeSerializer())
    d = _doc(DocumentState.ACTIVE)
    await repo.add(d)
    await repo.put_blob(d.id, b"raw-bytes", content_type="text/plain")
    assert (await repo.get(d.id)) == d
    assert (await repo.get_blob(d.id)) == b"raw-bytes"
    await repo.remove(d.id)
    assert await repo.get(d.id) is None


# ── runtime behaviour ────────────────────────────────────────────────────────
def test_runtime_index_get_and_lifecycle_reaches_active():
    rt = _runtime()
    did = rt.index(TEXT, uri="doc://a", scope=_scope(), title="A")
    doc = rt.get(did)
    assert doc.state is DocumentState.ACTIVE and doc.version == 1 and len(doc.chunk_ids) >= 1


def test_runtime_dedup_on_identical_reimport():
    rt = _runtime()
    a = rt.index(TEXT, uri="doc://a", scope=_scope())
    b = rt.index(TEXT, uri="doc://a", scope=_scope())
    assert a == b and rt.get(a).version == 1  # no new version


def test_runtime_versions_on_changed_reimport():
    rt = _runtime()
    rt.index(TEXT, uri="doc://a", scope=_scope())
    did = rt.index(TEXT + " Extra content here.", uri="doc://a", scope=_scope())
    assert rt.get(did).version == 2


async def test_version_manager_history_and_current():
    rt = _runtime()
    rt.index(TEXT, uri="doc://a", scope=_scope())
    rt.index(TEXT + " more", uri="doc://a", scope=_scope())
    did = document_id_for("doc://a")
    history = await rt._repo.versions_of(did)
    assert [v.number for v in history] == [1, 2]
    current = await rt._repo.versions.current(did)
    assert current.number == 2


def test_runtime_remove_is_idempotent():
    rt = _runtime()
    did = rt.index(TEXT, uri="doc://a", scope=_scope())
    rt.remove(did)
    assert rt.get(did) is None
    rt.remove(did)  # idempotent — no raise


def test_runtime_retrieve_is_placeholder():
    rt = _runtime()
    rt.index(TEXT, uri="doc://a", scope=_scope())
    # retrieval intelligence is M5.2B-2 → no passages yet
    assert list(rt.retrieve(KnowledgeQuery(text="cover drive", org_id="o1"))) == []


def test_runtime_sources_scoped_by_org():
    rt = _runtime()
    rt.index(TEXT, uri="doc://a", scope=_scope(org_id="o1"))
    rt.index(TEXT, uri="doc://b", scope=_scope(org_id="o2"))
    assert rt.sources(org_id="o1") == [document_id_for("doc://a")]


def test_runtime_statistics():
    rt = _runtime()
    rt.index(TEXT, uri="doc://a", scope=_scope())
    stats = rt.statistics()
    assert stats.documents == 1 and stats.index["indexed_documents"] == 1
    assert stats.embeddings["dimensions"] == 64 and stats.healthy is False


# ── replay / gateway compatibility ───────────────────────────────────────────
def test_events_are_standard_envelopes():
    seen: list[Event] = []
    bus = InProcessEventBus(clock=lambda: 1.0)
    bus.subscribe(seen.append)
    rt = _runtime()
    rt.attach(bus)
    rt.index(TEXT, uri="doc://a", scope=_scope())
    mem = [e for e in seen if e.source is EventSource.KNOWLEDGE]
    assert mem and all(e.seq > 0 for e in mem)
    assert EventType.KNOWLEDGE_INDEXED in {e.type for e in mem}
    assert EventType.KNOWLEDGE_SOURCE_UPDATED in {e.type for e in mem}


def test_full_cycle_is_deterministic():
    def run():
        seen = []
        bus = InProcessEventBus(clock=lambda: 1.0)
        bus.subscribe(lambda e: seen.append((e.type.value, e.seq, tuple(sorted(e.data.items())))))
        rt = KnowledgeFactory.default(id_factory=_ids(), clock=lambda: 100.0)
        rt.attach(bus)
        rt.index(TEXT, uri="doc://a", scope=_scope())
        return seen

    assert run() == run()


# ── runtime lifecycle ────────────────────────────────────────────────────────
async def test_runtime_module_lifecycle():
    from core.module import ModuleState
    rt = _runtime()
    await rt.initialize()
    await rt.start()
    assert rt.state is ModuleState.RUNNING and rt.health().healthy
    await rt.shutdown()
    assert rt.state is ModuleState.STOPPED


# ── factory + configuration ──────────────────────────────────────────────────
def test_config_validation():
    with pytest.raises(ConfigError):
        EmbedderConfig(dimensions=0)
    with pytest.raises(ConfigError):
        ChunkConfig(size=10, overlap=10)  # overlap must be < size


def test_factory_rejects_unknown_store_kind():
    from knowledge.config import StoreConfig
    cfg = KnowledgeConfiguration(document_store=StoreConfig(kind="postgres"))
    with pytest.raises(ConfigError):
        KnowledgeFactory.create(cfg)


# ── dependency rules (knowledge stays acyclic) ───────────────────────────────
def _imports(pkg: str) -> set[str]:
    tops: set[str] = set()
    for p in pathlib.Path(pkg).rglob("*.py"):
        for n in ast.walk(ast.parse(p.read_text())):
            if isinstance(n, ast.ImportFrom) and n.module and not n.level:
                tops.add(n.module.split(".")[0])
            elif isinstance(n, ast.Import):
                for a in n.names:
                    tops.add(a.name.split(".")[0])
    return tops


def test_knowledge_dependency_rules():
    # knowledge depends only on core (+ its own package); never a sibling runtime,
    # the bus impl, memory, api, gateway, or replay.
    forbidden = {"runtime", "api", "gateway", "replay", "events", "memory", "voicebot",
                 "observability", "tenant"}
    assert not (_imports("knowledge") & forbidden), _imports("knowledge")
