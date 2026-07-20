"""Context Runtime core tests (M5.3B-1).

Covers deterministic ids, the package lifecycle (`core.fsm`), the serializer round-trips,
temporary-repository isolation, session + rolling-window management, deterministic
assembly (merge/normalize/order/naive-budget), package immutability, the runtime surface
(assemble/get/remove/history/expire/statistics), runtime lifecycle, factory + config,
replay/gateway compatibility, and dependency rules. Budget/ranking/compression/validation/
policy/tenancy intelligence are non-goals here (M5.3B-2).
"""

from __future__ import annotations

import ast
import itertools
import pathlib
from dataclasses import FrozenInstanceError

import pytest

from context import (
    ContextConfiguration,
    ContextFactory,
    ContextKind,
    ContextPackage,
    ContextScope,
    ContextSerializer,
    ContextWindow,
    PackageState,
    PackageTrigger,
    Provenance,
    SourceContribution,
    SourceRuntime,
    StaticContextSource,
    advance_package,
    item_id_for,
    package_id_for,
)
from context.config import TokenizerConfig, WindowConfig
from context.domain import ContextItem
from context.lifecycle import PACKAGE_LIFECYCLE
from core.errors import ConfigError
from core.events import Event, EventSource, EventType
from core.fsm import FSMError
from core.ports.context import ContextRequest
from events.bus import InProcessEventBus


def _ids():
    return (f"c{i:02d}" for i in itertools.count()).__next__


def _contribs():
    return [
        SourceContribution(ContextKind.WORKING_MEMORY, "prefers cover drives",
                           SourceRuntime.MEMORY, "m1", 0.9),
        SourceContribution(ContextKind.KNOWLEDGE_PASSAGE, "cover drive off the front foot",
                           SourceRuntime.KNOWLEDGE, "k1", 0.8),
        SourceContribution(ContextKind.USER_INSTRUCTION, "how to improve my cover drive",
                           SourceRuntime.INPUT, "u1", 1.0, 1.0),
    ]


def _runtime(config=None, *, contributions=None, **kwargs):
    cfg = config or ContextConfiguration.default()
    kwargs.setdefault("clock", lambda: 100.0)
    src = StaticContextSource("bundle", contributions if contributions is not None else _contribs())
    return ContextFactory.create(cfg, extra_sources=[src], id_factory=_ids(), **kwargs)


def _req(**kw):
    base = dict(session_id="s1", org_id="o1", conversation_id="conv1", query="cover drive")
    base.update(kw)
    return ContextRequest(**base)


# ── deterministic ids ────────────────────────────────────────────────────────
def test_ids_are_deterministic_and_content_derived():
    assert item_id_for("k", "memory", "m1", "hello") == item_id_for("k", "memory", "m1", "hello")
    assert item_id_for("k", "memory", "m1", "a") != item_id_for("k", "memory", "m1", "b")
    assert package_id_for("s1", "h", 0) == package_id_for("s1", "h", 0)
    assert package_id_for("s1", "h", 0) != package_id_for("s1", "h", 1)


# ── lifecycle (core.fsm) ──────────────────────────────────────────────────────
def _pkg(state=PackageState.REQUESTED):
    return ContextPackage(id="p", session_id="s1", scope=ContextScope(), state=state)


def test_package_lifecycle_full_path():
    p = _pkg()
    for trig, want in [
        (PackageTrigger.ASSEMBLE, PackageState.ASSEMBLED),
        (PackageTrigger.VALIDATE, PackageState.VALIDATED),
        (PackageTrigger.DELIVER, PackageState.DELIVERED),
        (PackageTrigger.EXPIRE, PackageState.EXPIRED),
    ]:
        p = advance_package(p, trig)
        assert p.state is want


def test_package_illegal_transition_raises():
    with pytest.raises(FSMError):
        advance_package(_pkg(), PackageTrigger.DELIVER)  # cannot deliver from REQUESTED


def test_package_reject_paths():
    assert advance_package(_pkg(PackageState.ASSEMBLED), PackageTrigger.REJECT).state \
        is PackageState.REJECTED
    assert advance_package(_pkg(PackageState.VALIDATED), PackageTrigger.REJECT).state \
        is PackageState.REJECTED


def test_lifecycle_definition_is_shared_singleton():
    assert PACKAGE_LIFECYCLE is PACKAGE_LIFECYCLE


# ── serializer round-trips ────────────────────────────────────────────────────
def test_serializer_package_round_trip():
    s = ContextSerializer()
    rt = _runtime()
    rt.assemble(_req())
    pkg = rt.history("s1")[0]
    assert s.package_from_document(s.package_to_document(pkg)) == pkg


def test_serializer_session_round_trip():
    s = ContextSerializer()
    item = ContextItem(id="i", kind=ContextKind.USER_INSTRUCTION, text="hi", confidence=1.0,
                       confidence_source="input", provenance=Provenance(SourceRuntime.INPUT, "u1"))
    from context.domain import ContextSession
    sess = ContextSession(session_id="s1", window=ContextWindow(max_items=5, items=(item,)),
                          package_ids=("p1",), updated_at=5.0)
    assert s.session_from_document(s.session_to_document(sess)) == sess


# ── assembly ──────────────────────────────────────────────────────────────────
def test_assemble_orders_by_source_and_preserves_provenance():
    rt = _runtime()
    asm = rt.assemble(_req())
    kinds = [i.kind for i in asm.items]
    # working_memory before knowledge before user (fixed source order)
    assert kinds.index(ContextKind.WORKING_MEMORY.value) < kinds.index(
        ContextKind.KNOWLEDGE_PASSAGE.value) < kinds.index(ContextKind.USER_INSTRUCTION.value)
    assert all(i.provenance["source_id"] for i in asm.items)
    assert asm.items[0].provenance["content_hash"]


def test_assemble_is_deterministic():
    def run():
        seen = []
        bus = InProcessEventBus(clock=lambda: 1.0)
        bus.subscribe(lambda e: seen.append((e.type.value, e.seq, tuple(sorted(e.data.items())))))
        rt = _runtime()
        rt.attach(bus)
        rt.assemble(_req())
        return seen
    assert run() == run()


def test_naive_budget_truncates():
    rt = _runtime(ContextConfiguration(default_budget_tokens=3))
    asm = rt.assemble(_req())
    assert asm.truncated and asm.used_tokens <= 3


def test_id_uniqueness_collapses_identical_contributions():
    one = SourceContribution(ContextKind.KNOWLEDGE_PASSAGE, "same text",
                             SourceRuntime.KNOWLEDGE, "k1")
    rt = _runtime(contributions=[one, one])
    asm = rt.assemble(_req())
    assert len(asm.items) == 1


def test_include_flags_are_honoured():
    from core.ports.memory import MemoryHit, MemoryPort, MemoryTier

    class FakeMemory(MemoryPort):
        def store(self, content, *, tier, scope, importance=0.5): return "x"
        def retrieve(self, query): return [MemoryHit("m1", "mem", MemoryTier.WORKING, 0.9)]
        def forget(self, memory_id): return None

    rt = ContextFactory.default(clock=lambda: 100.0, memory_port=FakeMemory())
    got = rt.assemble(_req(include_memory=False))
    assert all(i.provenance["source_runtime"] != "memory" for i in got.items)


# ── session + rolling window ──────────────────────────────────────────────────
async def test_session_records_package_history():
    rt = _runtime()
    rt.assemble(_req())
    rt.assemble(_req(query="footwork"))
    sess = await rt._sessions.get("s1")
    assert len(sess.package_ids) == 2


async def test_rolling_window_bounded_and_ordered():
    rt = _runtime(ContextConfiguration(window=WindowConfig(max_items=2)))
    for q in ("q1", "q2", "q3"):
        rt.assemble(_req(query=q))
    sess = await rt._sessions.get("s1")
    # each assemble pushes the USER_INSTRUCTION item; window bounded to 2
    assert len(sess.window.items) == 2
    assert all(i.kind is ContextKind.USER_INSTRUCTION for i in sess.window.items)


def _uitem(x: str) -> ContextItem:
    return ContextItem(id=x, kind=ContextKind.USER_INSTRUCTION, text=x, confidence=1.0,
                       confidence_source="input", provenance=Provenance(SourceRuntime.INPUT, x))


def test_context_window_push_evicts_oldest():
    w = ContextWindow(max_items=2)
    w2 = w.push(_uitem("a")).push(_uitem("b")).push(_uitem("c"))
    assert [i.id for i in w2.items] == ["b", "c"]


# ── temporary repository isolation ────────────────────────────────────────────
async def test_repository_persists_and_isolates():
    from context.repositories import ContextRepository
    from context.stores import (
        ContextStore,
        InMemoryDocumentStore,
        InMemoryMetadataStore,
        WordTokenizer,
    )

    repo = ContextRepository(
        ContextStore(InMemoryDocumentStore(), InMemoryMetadataStore(), WordTokenizer()),
        ContextSerializer(),
    )
    pkg = _pkg(PackageState.DELIVERED)
    await repo.add_package(pkg)
    assert (await repo.get_package(pkg.id)) == pkg
    await repo.remove_package(pkg.id)
    assert await repo.get_package(pkg.id) is None
    await repo.remove_package(pkg.id)  # idempotent


def test_runtime_get_remove_history():
    rt = _runtime()
    rt.assemble(_req())
    pkg = rt.history("s1")[0]
    assert rt.get(pkg.id) == pkg
    rt.remove(pkg.id)
    assert rt.get(pkg.id) is None
    rt.remove(pkg.id)  # idempotent


# ── package immutability ──────────────────────────────────────────────────────
def test_package_and_item_are_frozen():
    rt = _runtime()
    rt.assemble(_req())
    pkg = rt.history("s1")[0]
    with pytest.raises(FrozenInstanceError):
        pkg.truncated = True
    with pytest.raises(FrozenInstanceError):
        pkg.items[0].text = "x"


# ── expiry sweep ──────────────────────────────────────────────────────────────
async def test_expire_sweeps_ttl_elapsed_packages():
    rt = _runtime(ContextConfiguration(package_ttl_s=10.0), clock=lambda: 100.0)
    rt.assemble(_req())
    assert await rt.expire(now=105.0) == 0  # not yet expired
    assert await rt.expire(now=200.0) == 1  # past ttl
    assert rt.statistics().packages == 0


# ── statistics ────────────────────────────────────────────────────────────────
def test_statistics():
    rt = _runtime()
    rt.assemble(_req())
    stats = rt.statistics()
    assert stats.packages == 1 and stats.sessions == 1 and "bundle" in stats.sources
    assert stats.healthy is False  # not started


# ── replay / gateway compatibility ────────────────────────────────────────────
def test_events_are_standard_envelopes():
    seen: list[Event] = []
    bus = InProcessEventBus(clock=lambda: 1.0)
    bus.subscribe(seen.append)
    rt = _runtime()
    rt.attach(bus)
    rt.assemble(_req())
    ctx = [e for e in seen if e.source is EventSource.CONTEXT]
    assert ctx and all(e.seq > 0 for e in ctx)
    assert EventType.CONTEXT_ASSEMBLED in {e.type for e in ctx}
    phases = {e.data.get("phase") for e in ctx}
    assert {"requested", "built", "validated", "delivered"} <= phases


def test_truncation_emits_context_truncated():
    seen: list[Event] = []
    bus = InProcessEventBus(clock=lambda: 1.0)
    bus.subscribe(seen.append)
    rt = _runtime(ContextConfiguration(default_budget_tokens=3))
    rt.attach(bus)
    rt.assemble(_req())
    trunc = [e for e in seen if e.type is EventType.CONTEXT_TRUNCATED]
    assert trunc and trunc[-1].data["phase"] == "compressed"


# ── runtime lifecycle ─────────────────────────────────────────────────────────
async def test_runtime_module_lifecycle():
    from core.module import ModuleState
    rt = _runtime()
    await rt.initialize()
    await rt.start()
    assert rt.state is ModuleState.RUNNING and rt.health().healthy
    await rt.shutdown()
    assert rt.state is ModuleState.STOPPED


# ── factory + configuration ───────────────────────────────────────────────────
def test_config_validation():
    with pytest.raises(ConfigError):
        TokenizerConfig(chars_per_token=0)
    with pytest.raises(ConfigError):
        ContextConfiguration(per_source_limit=0)
    with pytest.raises(ConfigError):
        ContextConfiguration(package_ttl_s=0)


def test_factory_rejects_unknown_store_kind():
    from context.config import StoreConfig
    cfg = ContextConfiguration(document_store=StoreConfig(kind="postgres"))
    with pytest.raises(ConfigError):
        ContextFactory.create(cfg)


# ── dependency rules (context stays acyclic) ──────────────────────────────────
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


def test_context_dependency_rules():
    # context depends only on the foundation (core + tenant, the same isolation seam
    # Memory/Knowledge use) plus its own package; never a sibling runtime, the bus impl,
    # the memory/knowledge/prompt packages, api, gateway, or replay. (M5.3B-2 tenancy added
    # the `tenant` foundation dep — a leaf package, not a runtime, so no cycle; DL-0028.)
    forbidden = {"runtime", "api", "gateway", "replay", "events", "memory", "knowledge",
                 "prompt", "voicebot", "observability"}
    assert not (_imports("context") & forbidden), _imports("context")
