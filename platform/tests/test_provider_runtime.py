"""Provider Runtime Core (M5.6B-1) tests.

Verifies deterministic execution, the registry (registration/discovery/lifecycle — no
routing), the capability catalog (no intelligence), unified streaming/response/usage/
metadata normalization, serialization round-trips, replay/gateway-compatible standard
envelopes, provider neutrality, observability, architecture boundaries, and the acyclic
`provider → core` dependency rule. Routing strategies, health-outcome feedback, a policy
engine, and tenancy are non-goals here (M5.6B-2).
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
from provider import (
    Capability,
    ChunkKind,
    EchoProviderAdapter,
    FinishReason,
    HealthTrigger,
    ProviderCapabilities,
    ProviderChunk,
    ProviderConfiguration,
    ProviderDescriptor,
    ProviderFactory,
    ProviderHealthState,
    ProviderMessage,
    ProviderRegistry,
    ProviderRegistryEntry,
    ProviderRequest,
    ProviderRequestState,
    ProviderScope,
    ProviderSerializer,
    ProviderTrigger,
    advance,
    advance_health,
    chunk_id_for,
    request_hash_for,
    response_hash_for,
    response_id_for,
)


def _ids():
    return (f"p{i:02d}" for i in itertools.count()).__next__


def _req(**kw):
    base = dict(
        session_id="s1",
        messages=(ProviderMessage("system", "You are a coach."),
                  ProviderMessage("user", "How do I cover drive?")),
        capability="chat", model="default", streaming=True,
        scope=ProviderScope(org_id="o1", session_id="s1", conversation_id="c1"),
    )
    base.update(kw)
    return ProviderRequest(**base)


def _runtime(config=None, **kwargs):
    cfg = config or ProviderConfiguration.default()
    kwargs.setdefault("clock", lambda: 100.0)
    return ProviderFactory.create(cfg, id_factory=_ids(), **kwargs)


# ── deterministic ids ──────────────────────────────────────────────────────────
def test_ids_are_deterministic_and_content_derived():
    assert request_hash_for("echo", "m", "chat", "g") == request_hash_for("echo", "m", "chat", "g")
    assert request_hash_for("a", "m", "chat", "g") != request_hash_for("b", "m", "chat", "g")
    assert chunk_id_for("r", 0, "x") == chunk_id_for("r", 0, "x")
    assert response_hash_for("r", ("a", "b")) != response_hash_for("r", ("b", "a"))
    assert response_id_for("s", "h", 0) != response_id_for("s", "h", 1)


# ── request execution lifecycle (core.fsm) ───────────────────────────────────────
def _resp(state=ProviderRequestState.REQUESTED):
    return __import__("provider").ProviderResponse(
        id="p", session_id="s1", scope=ProviderScope(), state=state
    )


def test_execution_lifecycle_full_streaming_path():
    r = _resp()
    for trig, want in [
        (ProviderTrigger.ROUTE, ProviderRequestState.ROUTED),
        (ProviderTrigger.EXECUTE, ProviderRequestState.EXECUTING),
        (ProviderTrigger.STREAM, ProviderRequestState.STREAMING),
        (ProviderTrigger.COMPLETE, ProviderRequestState.COMPLETED),
        (ProviderTrigger.EXPIRE, ProviderRequestState.EXPIRED),
    ]:
        r = advance(r, trig)
        assert r.state is want


def test_execution_lifecycle_single_shot_and_failover_and_cancel():
    # single-shot: EXECUTING -> COMPLETED
    assert advance(_resp(ProviderRequestState.EXECUTING), ProviderTrigger.COMPLETE).state \
        is ProviderRequestState.COMPLETED
    # failover: EXECUTING -> FAILED_OVER -> (re-route) ROUTED
    fo = advance(_resp(ProviderRequestState.EXECUTING), ProviderTrigger.FAILOVER)
    assert fo.state is ProviderRequestState.FAILED_OVER
    assert advance(fo, ProviderTrigger.ROUTE).state is ProviderRequestState.ROUTED
    # cancel from an active state
    assert advance(_resp(ProviderRequestState.STREAMING), ProviderTrigger.CANCEL).state \
        is ProviderRequestState.CANCELLED


def test_execution_lifecycle_illegal_transition_raises():
    with pytest.raises(FSMError):
        advance(_resp(), ProviderTrigger.COMPLETE)  # cannot complete from REQUESTED


# ── adapter health lifecycle ──────────────────────────────────────────────────────
def test_health_lifecycle_degrade_and_conservative_recovery():
    entry = ProviderRegistryEntry(descriptor=ProviderDescriptor(name="x"))
    assert entry.health is ProviderHealthState.HEALTHY
    entry = advance_health(entry, HealthTrigger.OUTCOME_SUCCESS)  # self-loop no-op
    assert entry.health is ProviderHealthState.HEALTHY
    entry = advance_health(entry, HealthTrigger.OUTCOME_FAILURE)
    assert entry.health is ProviderHealthState.DEGRADED
    entry = advance_health(entry, HealthTrigger.OUTCOME_FAILURE)
    assert entry.health is ProviderHealthState.UNAVAILABLE
    # conservative recovery: UNAVAILABLE -> DEGRADED (not straight to HEALTHY)
    entry = advance_health(entry, HealthTrigger.RECOVER)
    assert entry.health is ProviderHealthState.DEGRADED
    entry = advance_health(entry, HealthTrigger.OUTCOME_SUCCESS)
    assert entry.health is ProviderHealthState.HEALTHY


def test_health_lifecycle_illegal_transition_raises():
    entry = ProviderRegistryEntry(descriptor=ProviderDescriptor(name="x"))
    with pytest.raises(FSMError):
        advance_health(entry, HealthTrigger.RECOVER)  # cannot recover from HEALTHY


# ── EchoProviderAdapter + ProviderRegistry (no routing) ──────────────────────────
def test_echo_adapter_is_deterministic_and_offline():
    a = EchoProviderAdapter()
    req = _req(request_hash="rh1")
    x = list(a.stream(req))
    y = list(a.stream(req))
    assert [c.delta for c in x] == [c.delta for c in y]  # deterministic
    assert x[-1].done and x[-1].kind is ChunkKind.FINAL
    assert "How do I cover drive?" in "".join(c.delta for c in x)


def test_registry_registration_lookup_and_duplicate_rejected():
    reg = ProviderRegistry()
    reg.register(ProviderDescriptor(name="echo", capabilities=EchoProviderAdapter.capabilities),
                 EchoProviderAdapter())
    assert reg.get("echo").name == "echo"
    assert reg.descriptor("echo").name == "echo"
    with pytest.raises(ConfigError):
        reg.get("anthropic")
    with pytest.raises(ConfigError):
        reg.register(ProviderDescriptor(name="echo"), EchoProviderAdapter())  # duplicate


def test_registry_update_requires_prior_registration():
    reg = ProviderRegistry()
    with pytest.raises(ConfigError):
        reg.update(ProviderDescriptor(name="ghost"))
    reg.register(ProviderDescriptor(name="echo"), EchoProviderAdapter())
    reg.update(ProviderDescriptor(name="echo", version="2"))
    assert reg.descriptor("echo").version == "2"


def test_registry_find_is_capability_filtered_and_sorted_no_scoring():
    reg = ProviderRegistry()
    reg.register(ProviderDescriptor(name="zeta", capabilities=ProviderCapabilities(chat=True)),
                 EchoProviderAdapter())
    reg.register(ProviderDescriptor(name="alpha", capabilities=ProviderCapabilities(chat=True)),
                 EchoProviderAdapter())
    reg.register(ProviderDescriptor(name="beta", capabilities=ProviderCapabilities(vision=True)),
                 EchoProviderAdapter())
    found = reg.find(Capability.CHAT)
    assert [d.name for d in found] == ["alpha", "zeta"]  # sorted by name — no scoring
    assert reg.find("vision")[0].name == "beta"
    assert reg.find("embeddings") == ()


def test_registry_health_partitions_and_manual_transitions():
    reg = ProviderRegistry()
    reg.register(ProviderDescriptor(name="echo"), EchoProviderAdapter())
    assert reg.healthy() == ("echo",)
    reg.mark_outcome_failure("echo")
    assert reg.degraded() == ("echo",)
    reg.mark_unavailable("echo")
    assert reg.unavailable() == ("echo",)
    reg.recover("echo")
    assert reg.degraded() == ("echo",)


def test_runtime_rejects_unknown_provider_and_unknown_capability():
    rt = _runtime()
    with pytest.raises(ConfigError):
        rt.execute(_req(provider="openai"))
    with pytest.raises(ConfigError):
        rt.execute(_req(capability="embeddings"))


# ── capability catalog (no intelligence) ──────────────────────────────────────────
def test_capability_catalog_discovery():
    rt = _runtime()
    assert rt.catalog.supports("echo", Capability.CHAT) is True
    assert rt.catalog.supports("echo", "vision") is False
    assert rt.catalog.discover("chat") == ("echo",)
    assert "echo" in rt.catalog.catalog()


# ── unified streaming + response assembly ────────────────────────────────────────
def test_execute_streams_and_assembles():
    rt = _runtime()
    resp = rt.execute(_req())
    assert resp.state is ProviderRequestState.COMPLETED
    assert resp.text == "echo: How do I cover drive?"
    assert len(resp.chunks) >= 2 and resp.streaming.streamed
    assert resp.usage.completion_tokens > 0 and resp.usage.total_tokens == (
        resp.usage.prompt_tokens + resp.usage.completion_tokens)
    assert resp.finish_reason is FinishReason.STOP
    assert resp.metadata.provider == "echo" and resp.metadata.request_id


def test_single_shot_collapses_to_one_chunk():
    rt = _runtime()
    resp = rt.execute(_req(streaming=False))
    assert not resp.streaming.streamed and len(resp.chunks) == 1
    assert resp.text == "echo: How do I cover drive?"


def test_streaming_partials_are_cumulative():
    from provider.streaming import UnifiedStreamingAssembler

    chunks = [
        ProviderChunk(id="a", index=0, delta="hi"),
        ProviderChunk(id="b", index=1, delta=" there", kind=ChunkKind.FINAL, done=True),
    ]
    assert list(UnifiedStreamingAssembler().partials(chunks)) == ["hi", "hi there"]


# ── immutability ─────────────────────────────────────────────────────────────────
def test_response_and_chunk_are_frozen():
    rt = _runtime()
    resp = rt.execute(_req())
    with pytest.raises(FrozenInstanceError):
        resp.text = "x"
    with pytest.raises(FrozenInstanceError):
        resp.chunks[0].delta = "y"


# ── serializer round-trips (replay-safe) ──────────────────────────────────────────
def test_serializer_response_round_trip():
    s = ProviderSerializer()
    rt = _runtime()
    rt.execute(_req())
    resp = rt.history("s1")[0]
    assert s.response_from_document(s.response_to_document(resp)) == resp


def test_serializer_request_round_trip():
    s = ProviderSerializer()
    req = _req(request_hash="rh1")
    assert s.request_from_dict(s.request_to_dict(req)) == req


def test_serializer_chunk_round_trip():
    s = ProviderSerializer()
    c = ProviderChunk(id="c", index=2, delta=" x", kind=ChunkKind.FINAL, done=True)
    assert s.chunk_from_dict(s.chunk_to_dict(c)) == c


# ── temporary repository isolation ───────────────────────────────────────────────
async def test_repository_persists_and_isolates():
    from provider.repositories import ProviderRepository
    from provider.stores import InMemoryDocumentStore, InMemoryMetadataStore, ProviderStore

    repo = ProviderRepository(
        ProviderStore(InMemoryDocumentStore(), InMemoryMetadataStore()), ProviderSerializer())
    resp = _resp(ProviderRequestState.COMPLETED)
    await repo.add_response(resp)
    assert (await repo.get_response(resp.id)) == resp
    await repo.remove_response(resp.id)
    assert await repo.get_response(resp.id) is None
    await repo.remove_response(resp.id)  # idempotent


def test_runtime_get_remove_history():
    rt = _runtime()
    rt.execute(_req())
    resp = rt.history("s1")[0]
    assert rt.get(resp.id) == resp
    rt.remove(resp.id)
    assert rt.get(resp.id) is None
    rt.remove(resp.id)  # idempotent


async def test_expire_sweeps_ttl_elapsed_responses():
    rt = _runtime(ProviderConfiguration(response_ttl_s=10.0), clock=lambda: 100.0)
    rt.execute(_req())
    assert await rt.expire(now=105.0) == 0
    assert await rt.expire(now=200.0) == 1
    assert rt.statistics().requests == 0


# ── replay / gateway compatibility ───────────────────────────────────────────────
def test_events_are_standard_envelopes():
    seen: list[Event] = []
    bus = InProcessEventBus(clock=lambda: 1.0)
    bus.subscribe(seen.append)
    rt = _runtime()
    rt.attach(bus)
    rt.execute(_req())
    ev = [e for e in seen if e.source is EventSource.PROVIDER_REGISTRY]
    assert ev and all(e.seq > 0 for e in ev)
    types = {e.type for e in ev}
    assert EventType.PROVIDER_CONNECTED in types
    assert EventType.MODEL_TOKEN in {e.type for e in seen}
    assert EventType.MODEL_USAGE in {e.type for e in seen}
    phases = {e.data.get("phase") for e in ev}
    assert {"requested", "routed", "executing", "streaming", "completed", "delivered"} <= phases


def test_full_cycle_is_deterministic():
    def run():
        seen = []
        bus = InProcessEventBus(clock=lambda: 1.0)
        bus.subscribe(lambda e: seen.append((e.type.value, e.seq, e.data.get("phase"))))
        rt = ProviderFactory.default(id_factory=_ids(), clock=lambda: 100.0)
        rt.attach(bus)
        resp = rt.execute(_req())
        return seen, resp.response_hash, resp.text
    assert run() == run()


# ── runtime lifecycle ────────────────────────────────────────────────────────────
async def test_runtime_module_lifecycle():
    from core.module import ModuleState
    rt = _runtime()
    await rt.initialize()
    await rt.start()
    assert rt.state is ModuleState.RUNNING and rt.health().healthy
    await rt.shutdown()
    assert rt.state is ModuleState.STOPPED


# ── factory + config ─────────────────────────────────────────────────────────────
def test_config_validation():
    with pytest.raises(ConfigError):
        ProviderConfiguration(response_ttl_s=0)
    with pytest.raises(ConfigError):
        ProviderConfiguration(default_provider="")


def test_factory_rejects_unknown_store_kind():
    from provider.config import StoreConfig
    cfg = ProviderConfiguration(document_store=StoreConfig(kind="postgres"))
    with pytest.raises(ConfigError):
        ProviderFactory.create(cfg)


def test_statistics():
    rt = _runtime()
    rt.execute(_req())
    stats = rt.statistics()
    assert stats.requests == 1 and stats.sessions == 1 and "echo" in stats.providers
    assert stats.healthy is False  # not started


def test_custom_adapter_injection_and_provider_neutrality():
    from provider.domain import ProviderChunk as PC
    from provider.domain import ProviderRequest as PR

    class FixedAdapter:
        name = "fixed"
        capabilities = ProviderCapabilities(chat=True)

        def stream(self, request: PR):
            yield PC(id="c0", index=0, delta="fixed-answer", kind=ChunkKind.FINAL, done=True)

    rt = _runtime(adapters=[(ProviderDescriptor(name="fixed",
                                                capabilities=FixedAdapter.capabilities),
                            FixedAdapter())])
    resp = rt.execute(_req(provider="fixed"))
    assert resp.text == "fixed-answer" and "fixed" in rt.statistics().providers


# ── extension interfaces (interfaces only; no implementations shipped) ───────────
def test_extension_interfaces_are_abstract():
    from provider import (
        AsyncAdapter,
        BatchAdapter,
        EnterpriseAdapter,
        LocalAdapter,
        StreamingAdapter,
    )

    for iface in (AsyncAdapter, BatchAdapter, StreamingAdapter, LocalAdapter, EnterpriseAdapter):
        with pytest.raises(TypeError):
            iface()  # abstract — cannot instantiate


# ── dependency rules (provider stays acyclic) ────────────────────────────────────
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


def test_provider_dependency_rules():
    # provider depends only on the foundation (core + tenant, the same isolation seam
    # Memory/Knowledge/Context/Prompt/Reasoning use) plus its own package; never a sibling
    # runtime, the bus impl, memory/knowledge/context/prompt/reasoning, api, gateway, or
    # replay. (M5.6B-2 tenancy added the `tenant` foundation dep — a leaf package, not a
    # runtime, so no cycle; DL-0039.)
    forbidden = {"runtime", "api", "gateway", "replay", "events", "memory", "knowledge",
                 "context", "prompt", "reasoning", "voicebot", "observability"}
    assert not (_imports("provider") & forbidden), _imports("provider")
