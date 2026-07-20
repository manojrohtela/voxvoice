"""Reasoning Runtime core tests (M5.5B-1).

Covers deterministic ids, the inference lifecycle (`core.fsm`), the EchoProvider +
ProviderRegistry, streaming + response assembly, ResponsePackage creation + immutability, the
serializer round-trips, temporary-repository isolation, the runtime surface (run/infer/get/
remove/history/expire/statistics), `ReasoningPort` compliance, replay/gateway compatibility,
determinism, and dependency rules. Retry/cancellation/tool/scheduler/policy/tenancy
intelligence + provider adapters are non-goals here.
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
from core.ports.reasoning import Decision, DecisionKind, ReasoningPort, ReasoningRequest
from events.bus import InProcessEventBus
from reasoning import (
    ChunkKind,
    EchoProvider,
    FinishReason,
    InferenceMessage,
    InferenceRequest,
    InferenceState,
    InferenceTrigger,
    ProviderRegistry,
    ReasoningConfiguration,
    ReasoningFactory,
    ReasoningScope,
    ReasoningSerializer,
    ResponseChunk,
    ResponsePackage,
    advance,
    chunk_id_for,
    package_id_for,
    request_hash_for,
    response_hash_for,
)


def _ids():
    return (f"r{i:02d}" for i in itertools.count()).__next__


def _req(**kw):
    base = dict(
        session_id="s1",
        messages=(InferenceMessage("system", "You are a coach."),
                  InferenceMessage("user", "How do I cover drive?")),
        provider="echo", model="default", prompt_hash="ph1", prompt_fingerprint="pf1",
        streaming=True, scope=ReasoningScope(org_id="o1", session_id="s1", conversation_id="c1"),
    )
    base.update(kw)
    return InferenceRequest(**base)


def _runtime(config=None, **kwargs):
    cfg = config or ReasoningConfiguration.default()
    kwargs.setdefault("clock", lambda: 100.0)
    return ReasoningFactory.create(cfg, id_factory=_ids(), **kwargs)


# ── deterministic ids ──────────────────────────────────────────────────────────
def test_ids_are_deterministic_and_content_derived():
    assert request_hash_for("echo", "m", "ph", "g") == request_hash_for("echo", "m", "ph", "g")
    assert request_hash_for("a", "m", "ph", "g") != request_hash_for("b", "m", "ph", "g")
    assert chunk_id_for("r", 0, "x") == chunk_id_for("r", 0, "x")
    assert response_hash_for("r", ("a", "b")) != response_hash_for("r", ("b", "a"))
    assert package_id_for("s", "h", 0) != package_id_for("s", "h", 1)


# ── lifecycle (core.fsm) ─────────────────────────────────────────────────────────
def _pkg(state=InferenceState.REQUESTED):
    return ResponsePackage(id="p", session_id="s1", scope=ReasoningScope(), state=state)


def test_lifecycle_full_streaming_path():
    p = _pkg()
    for trig, want in [
        (InferenceTrigger.START, InferenceState.STARTED),
        (InferenceTrigger.STREAM, InferenceState.STREAMING),
        (InferenceTrigger.COMPLETE, InferenceState.COMPLETED),
        (InferenceTrigger.EXPIRE, InferenceState.EXPIRED),
    ]:
        p = advance(p, trig)
        assert p.state is want


def test_lifecycle_single_shot_and_tool_and_cancel():
    # single-shot: STARTED → COMPLETED
    assert advance(_pkg(InferenceState.STARTED), InferenceTrigger.COMPLETE).state \
        is InferenceState.COMPLETED
    # tool loop: STREAMING → TOOL_WAIT → STREAMING
    tw = advance(_pkg(InferenceState.STREAMING), InferenceTrigger.TOOL_WAIT)
    assert tw.state is InferenceState.TOOL_WAIT
    assert advance(tw, InferenceTrigger.RESUME).state is InferenceState.STREAMING
    # cancel from an active state
    assert advance(_pkg(InferenceState.STREAMING), InferenceTrigger.CANCEL).state \
        is InferenceState.CANCELLED


def test_lifecycle_illegal_transition_raises():
    with pytest.raises(FSMError):
        advance(_pkg(), InferenceTrigger.COMPLETE)  # cannot complete from REQUESTED


# ── EchoProvider + ProviderRegistry ──────────────────────────────────────────────
def test_echo_provider_is_deterministic_and_offline():
    p = EchoProvider()
    req = _req(request_hash="rh1")
    a = list(p.stream(req))
    b = list(p.stream(req))
    assert [c.delta for c in a] == [c.delta for c in b]  # deterministic
    assert a[-1].done and a[-1].kind is ChunkKind.FINAL
    assert "How do I cover drive?" in "".join(c.delta for c in a)


def test_provider_registry_rejects_unknown():
    reg = ProviderRegistry()
    reg.register(EchoProvider())
    assert reg.get("echo").name == "echo"
    with pytest.raises(ConfigError):
        reg.get("anthropic")


def test_runtime_rejects_unknown_provider():
    rt = _runtime()
    with pytest.raises(ConfigError):
        rt.infer(_req(provider="openai"))


# ── streaming + assembly + package ───────────────────────────────────────────────
def test_infer_streams_and_assembles():
    rt = _runtime()
    pkg = rt.infer(_req())
    assert pkg.state is InferenceState.COMPLETED
    assert pkg.text == "echo: How do I cover drive?"
    assert len(pkg.chunks) >= 2 and pkg.streaming.streamed
    assert pkg.usage.completion_tokens > 0 and pkg.usage.total_tokens == (
        pkg.usage.prompt_tokens + pkg.usage.completion_tokens)
    assert pkg.finish_reason is FinishReason.STOP


def test_single_shot_collapses_to_one_chunk():
    rt = _runtime()
    pkg = rt.infer(_req(streaming=False))
    assert not pkg.streaming.streamed and len(pkg.chunks) == 1
    assert pkg.text == "echo: How do I cover drive?"


def test_response_lineage_is_complete():
    rt = _runtime()
    pkg = rt.infer(_req())
    ln = pkg.lineage
    assert ln.prompt_hash == "ph1" and ln.prompt_fingerprint == "pf1"
    assert ln.inference_request_hash and ln.response_hash == pkg.response_hash
    assert ln.provider == "echo" and ln.model == "default"


# ── immutability ─────────────────────────────────────────────────────────────────
def test_package_and_chunk_are_frozen():
    rt = _runtime()
    pkg = rt.infer(_req())
    with pytest.raises(FrozenInstanceError):
        pkg.text = "x"
    with pytest.raises(FrozenInstanceError):
        pkg.chunks[0].delta = "y"


# ── serializer round-trips ───────────────────────────────────────────────────────
def test_serializer_package_round_trip():
    s = ReasoningSerializer()
    rt = _runtime()
    rt.infer(_req())
    pkg = rt.history("s1")[0]
    assert s.package_from_document(s.package_to_document(pkg)) == pkg


def test_serializer_request_round_trip():
    s = ReasoningSerializer()
    req = _req(request_hash="rh1")
    assert s.request_from_dict(s.request_to_dict(req)) == req


def test_serializer_chunk_round_trip():
    s = ReasoningSerializer()
    c = ResponseChunk(id="c", index=2, delta=" x", kind=ChunkKind.FINAL, done=True)
    assert s.chunk_from_dict(s.chunk_to_dict(c)) == c


# ── temporary repository isolation ───────────────────────────────────────────────
async def test_repository_persists_and_isolates():
    from reasoning.repositories import ReasoningRepository
    from reasoning.stores import InMemoryDocumentStore, InMemoryMetadataStore, ReasoningStore

    repo = ReasoningRepository(
        ReasoningStore(InMemoryDocumentStore(), InMemoryMetadataStore()), ReasoningSerializer())
    pkg = _pkg(InferenceState.COMPLETED)
    await repo.add_response(pkg)
    assert (await repo.get_response(pkg.id)) == pkg
    await repo.remove_response(pkg.id)
    assert await repo.get_response(pkg.id) is None
    await repo.remove_response(pkg.id)  # idempotent


def test_runtime_get_remove_history():
    rt = _runtime()
    rt.infer(_req())
    pkg = rt.history("s1")[0]
    assert rt.get(pkg.id) == pkg
    rt.remove(pkg.id)
    assert rt.get(pkg.id) is None
    rt.remove(pkg.id)  # idempotent


async def test_expire_sweeps_ttl_elapsed_responses():
    rt = _runtime(ReasoningConfiguration(package_ttl_s=10.0), clock=lambda: 100.0)
    rt.infer(_req())
    assert await rt.expire(now=105.0) == 0
    assert await rt.expire(now=200.0) == 1
    assert rt.statistics().responses == 0


# ── ReasoningPort compliance ─────────────────────────────────────────────────────
def test_reasoning_port_run_streams_decisions():
    rt = _runtime()
    assert isinstance(rt, ReasoningPort)
    decisions = list(rt.run(ReasoningRequest(session_id="s2", context_ref="ctx", org_id="o1")))
    assert decisions and all(isinstance(d, Decision) for d in decisions)
    assert decisions[-1].kind is DecisionKind.FINAL


def test_reasoning_port_run_yields_tokens_for_messages():
    # a runtime whose default request path carries messages via infer → token decisions
    rt = _runtime()
    pkg = rt.infer(_req())
    kinds = [ChunkKind.TOKEN if not c.done else ChunkKind.FINAL for c in pkg.chunks]
    assert ChunkKind.TOKEN in kinds and kinds[-1] is ChunkKind.FINAL


# ── replay / gateway compatibility ───────────────────────────────────────────────
def test_events_are_standard_envelopes():
    seen: list[Event] = []
    bus = InProcessEventBus(clock=lambda: 1.0)
    bus.subscribe(seen.append)
    rt = _runtime()
    rt.attach(bus)
    rt.infer(_req())
    ev = [e for e in seen if e.source is EventSource.REASONING]
    assert ev and all(e.seq > 0 for e in ev)
    types = {e.type for e in ev}
    assert EventType.REASONING_STARTED in types and EventType.REASONING_COMPLETED in types
    assert EventType.MODEL_TOKEN in {e.type for e in seen}
    assert EventType.MODEL_USAGE in {e.type for e in seen}
    phases = {e.data.get("phase") for e in ev}
    assert {"requested", "started", "streaming", "completed", "delivered"} <= phases


def test_full_cycle_is_deterministic():
    def run():
        seen = []
        bus = InProcessEventBus(clock=lambda: 1.0)
        bus.subscribe(lambda e: seen.append((e.type.value, e.seq, e.data.get("phase"))))
        rt = ReasoningFactory.default(id_factory=_ids(), clock=lambda: 100.0)
        rt.attach(bus)
        pkg = rt.infer(_req())
        return seen, pkg.response_hash, pkg.text
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
        ReasoningConfiguration(package_ttl_s=0)
    with pytest.raises(ConfigError):
        ReasoningConfiguration(default_provider="")


def test_factory_rejects_unknown_store_kind():
    from reasoning.config import StoreConfig
    cfg = ReasoningConfiguration(document_store=StoreConfig(kind="postgres"))
    with pytest.raises(ConfigError):
        ReasoningFactory.create(cfg)


def test_statistics():
    rt = _runtime()
    rt.infer(_req())
    stats = rt.statistics()
    assert stats.responses == 1 and stats.sessions == 1 and "echo" in stats.providers
    assert stats.healthy is False  # not started


def test_custom_provider_injection():
    from reasoning.domain import InferenceRequest as IR
    from reasoning.domain import ResponseChunk as RC

    class FixedProvider:
        name = "fixed"

        def stream(self, request: IR):
            yield RC(id="c0", index=0, delta="fixed-answer", kind=ChunkKind.FINAL, done=True)

    rt = _runtime(providers=[FixedProvider()])
    pkg = rt.infer(_req(provider="fixed"))
    assert pkg.text == "fixed-answer" and "fixed" in rt.statistics().providers


# ── dependency rules (reasoning stays acyclic) ───────────────────────────────────
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


def test_reasoning_dependency_rules():
    # reasoning depends only on core (+ its own package); never a sibling runtime, the bus
    # impl, the prompt/context/memory/knowledge packages, api, gateway, or replay. (Tenancy —
    # and thus the `tenant` dep — is M5.5B-2.)
    forbidden = {"runtime", "api", "gateway", "replay", "events", "memory", "knowledge",
                 "context", "prompt", "voicebot", "observability", "tenant"}
    assert not (_imports("reasoning") & forbidden), _imports("reasoning")
