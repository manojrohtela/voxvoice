"""AI Runtime Foundation tests (M5.0).

Covers the shared infrastructure every future module reuses: the deterministic FSM,
RuntimeModule lifecycle, BusConsumer (attach/publish/isolation + replay/gateway
compatibility), shared policies, telemetry, content-event vocabulary, tenancy, the
storage/port contracts, and the acyclic dependency rules.
"""

from __future__ import annotations

import ast
import itertools
import pathlib

import pytest

# ── core.fsm ─────────────────────────────────────────────────────────────────
from core.fsm import (
    DuplicateTransition,
    GuardRejected,
    InvalidTransition,
    StateMachine,
    StateMachineDefinition,
    TerminalStateError,
    TransitionEvent,
    define,
)


def _clock():
    return itertools.count(1).__next__


def test_fsm_deterministic_transitions_and_history():
    d = define([("idle", "go", "run"), ("run", "stop", "idle")])
    m = StateMachine(d, "idle", clock=lambda t=itertools.count(1): float(next(t)))
    assert m.fire("go") == "run"
    assert m.fire("stop") == "idle"
    assert [(r.source, r.trigger, r.target) for r in m.history] == [
        ("idle", "go", "run"), ("run", "stop", "idle"),
    ]
    assert m.history[0].seq == 1 and m.history[1].seq == 2


def test_fsm_invalid_and_duplicate_and_terminal():
    d = StateMachineDefinition()
    d.add("a", "x", "b")
    with pytest.raises(DuplicateTransition):
        d.add("a", "x", "c")  # non-deterministic edge rejected
    m = StateMachine(d, "a")
    with pytest.raises(InvalidTransition):
        m.fire("nope")
    m.fire("x")  # a -> b (terminal, no outgoing)
    assert m.is_terminal
    with pytest.raises(TerminalStateError):
        m.fire("x")


def test_fsm_guard_and_action():
    seen = []
    d = StateMachineDefinition()
    d.add("a", "go", "b", guard=lambda ctx: ctx == "ok", action=lambda ctx: seen.append(ctx))
    m = StateMachine(d, "a")
    assert m.can("go", "ok") and not m.can("go", "no")
    with pytest.raises(GuardRejected):
        m.fire("go", "no")
    m.fire("go", "ok")
    assert seen == ["ok"] and m.state == "b"


def test_fsm_deterministic_replay_and_events():
    events: list[TransitionEvent] = []
    d = define([("a", "g", "b"), ("b", "g", "c")])
    m = StateMachine(d, "a", clock=_clock(), on_transition=events.append)
    assert m.replay(["g", "g"]) == ("b", "c")
    # replay resets, so a second identical replay yields identical states
    assert m.replay(["g", "g"]) == ("b", "c")
    assert all(isinstance(e, TransitionEvent) for e in events)


# ── core.module: RuntimeModule + BusConsumer ────────────────────────────────
from core.events import EventSource, EventType  # noqa: E402
from core.module import BusConsumer, ModuleError, ModuleState, RuntimeModule  # noqa: E402
from events import InProcessEventBus  # noqa: E402


async def test_runtime_module_lifecycle():
    calls = []

    class M(RuntimeModule):
        async def on_initialize(self): calls.append("init")
        async def on_start(self): calls.append("start")
        async def on_stop(self): calls.append("stop")

    m = M(name="demo")
    assert m.state is ModuleState.CREATED and not m.health().healthy
    await m.initialize()
    assert m.state is ModuleState.READY and m.health().healthy
    await m.start()
    assert m.state is ModuleState.RUNNING and m.health().healthy
    await m.stop()
    assert m.state is ModuleState.STOPPED and not m.health().healthy
    assert calls == ["init", "start", "stop"]


async def test_runtime_module_failure_routes_to_failed():
    class Boom(RuntimeModule):
        async def on_initialize(self): raise RuntimeError("boom")

    m = Boom(name="boom")
    with pytest.raises(ModuleError):
        await m.initialize()
    assert m.state is ModuleState.FAILED and m.metrics.failures == 1


def test_runtime_module_dependency_injection():
    m = RuntimeModule(name="di")
    m.add_dependency("port", object())
    assert m.dependency("port") is not None
    with pytest.raises(ModuleError):
        m.require("missing")


async def test_bus_consumer_publish_and_isolation():
    bus = InProcessEventBus()
    seen = []
    bus.subscribe(seen.append)  # a stand-in for Replay/Gateway (any bus consumer)

    class Mem(BusConsumer):
        handled = 0
        def handle_event(self, event):
            Mem.handled += 1
            if event.type is EventType.RUNTIME_ERROR:
                raise RuntimeError("consumer boom")  # must be isolated

    mem = Mem(name="mem", source=EventSource.MEMORY)
    mem.attach(bus)
    # publish → the standard envelope reaches every bus subscriber (replay/gateway free)
    ev = mem.publish(EventType.MEMORY_STORED, {"k": 1}, session_id="s1", org_id="o")
    assert ev.type is EventType.MEMORY_STORED and ev.source is EventSource.MEMORY
    assert ev in seen and mem.metrics.published == 1
    # a raising handler is isolated + counted, bus stays healthy
    from core.events import EventMetadata
    bus.publisher(EventMetadata(session_id="s1", source=EventSource.RUNTIME)).publish(
        EventType.RUNTIME_ERROR
    )
    assert mem.metrics.handler_errors == 1
    assert Mem.handled >= 2  # handled its own publish + the error event


async def test_bus_consumer_detaches_on_shutdown():
    bus = InProcessEventBus()
    mem = BusConsumer(name="mem", source=EventSource.MEMORY)
    mem.attach(bus)
    assert mem.attached
    await mem.shutdown()
    assert not mem.attached


# ── shared policies ──────────────────────────────────────────────────────────
from core.policies import (  # noqa: E402
    BudgetPolicy,
    InvalidPolicy,
    RankingPolicy,
    RetryPolicy,
    SelectionPolicy,
    TimeoutPolicy,
    ValidationPolicy,
)


def test_policies_generic_behaviour_and_validation():
    assert RankingPolicy({"recency": 2.0, "relevance": 1.0}).score(
        {"recency": 0.5, "relevance": 0.2}
    ) == pytest.approx(1.2)
    r = RetryPolicy(base_delay_s=0.1, multiplier=2.0, max_delay_s=1.0)
    assert [r.delay_for(a) for a in (1, 2, 3, 10)] == [0.1, 0.2, 0.4, 1.0]  # capped
    b = BudgetPolicy(limit=100, unit="tokens")
    assert b.fits(90, 10) and not b.fits(95, 10)
    assert BudgetPolicy(limit=0).unbounded
    for bad in (
        lambda: RetryPolicy(max_attempts=0),
        lambda: TimeoutPolicy(seconds=0),
        lambda: BudgetPolicy(limit=-1),
        lambda: SelectionPolicy(k=-1),
        lambda: ValidationPolicy(max_size=-1),
        lambda: RankingPolicy({"x": -1.0}),
    ):
        with pytest.raises(InvalidPolicy):
            bad()


# ── telemetry ────────────────────────────────────────────────────────────────
from telemetry import Telemetry, Tracer  # noqa: E402


def test_telemetry_metrics_and_meter_protocol():
    from core.module.metrics import Meter

    t = Telemetry(clock=lambda c=itertools.count(): next(c) / 1.0)
    assert isinstance(t, Meter)  # structurally satisfies the core Meter protocol
    t.incr("memory.stored", 2, tier="working")
    t.incr("memory.stored", 1, tier="working")
    assert t.counter("memory.stored", tier="working") == 3.0
    t.observe("latency", 0.02)
    assert t.histogram("latency").n == 1
    with t.timer("op"):
        pass
    assert t.histogram("op").n == 1


def test_telemetry_tracing_with_correlation():
    tr = Tracer(clock=lambda c=itertools.count(1): float(next(c)))
    with tr.span("assemble", correlation_id="turn-2", session_id="s1") as s:
        s.set("items", 3)
    assert len(tr.finished) == 1
    fin = tr.finished[0]
    assert fin.correlation_id == "turn-2" and fin.attributes["items"] == 3
    assert fin.duration > 0


# ── content events (defined, provider-neutral) ──────────────────────────────
def test_content_event_vocabulary_defined():
    for name in (
        "INPUT_RECEIVED", "TRANSCRIPT_COMPLETED", "MEMORY_STORED", "MEMORY_RETRIEVED",
        "PROMPT_RENDERED", "KNOWLEDGE_RETRIEVED", "CONTEXT_ASSEMBLED",
        "REASONING_STARTED", "TOOL_REQUESTED", "MODEL_TOKEN", "MODEL_USAGE", "COST_UPDATED",
    ):
        assert hasattr(EventType, name)
    for src in ("MEMORY", "PROMPT", "KNOWLEDGE", "CONTEXT", "REASONING", "TOOL", "AGENT"):
        assert hasattr(EventSource, src)


# ── tenancy ──────────────────────────────────────────────────────────────────
from tenant import (  # noqa: E402
    AccessDecision,
    Authorizer,
    Effect,
    OrganizationContext,
    QuotaEnforcer,
    TenantContext,
)


def test_tenant_context_and_interfaces():
    tc = TenantContext(org=OrganizationContext(org_id="acme", region="eu"), user_id="u1")
    assert tc.org_id == "acme" and tc.org.region == "eu"
    assert AccessDecision(Effect.ALLOW).allowed
    assert not AccessDecision(Effect.DENY).allowed
    assert Authorizer.__abstractmethods__ and QuotaEnforcer.__abstractmethods__  # not implemented


# ── ports + storage contracts are interfaces (not implemented at M5.0) ──────
def test_ports_and_stores_are_abstract():
    from core.ports import ContextPort, MemoryPort, ToolPort
    from core.providers import Embedder, ObjectStore, VectorStore

    for iface in (MemoryPort, ContextPort, ToolPort, ObjectStore, VectorStore, Embedder):
        assert iface.__abstractmethods__  # abstract → cannot instantiate
        with pytest.raises(TypeError):
            iface()


def test_memory_port_dtos_are_neutral():
    from core.ports import MemoryQuery, MemoryScope, MemoryTier

    q = MemoryQuery(text="hi", tier=MemoryTier.EPISODIC, scope=MemoryScope(org_id="acme"))
    assert q.tier is MemoryTier.EPISODIC and q.scope.org_id == "acme" and q.limit == 10


# ── dependency rules (acyclic; foundation stays clean) ──────────────────────
def _imports(pkg: str) -> set[str]:
    tops: set[str] = set()
    for p in pathlib.Path(pkg).rglob("*.py"):
        for n in ast.walk(ast.parse(p.read_text())):
            if isinstance(n, ast.ImportFrom) and n.module:
                tops.add(n.module.split(".")[0])
            elif isinstance(n, ast.Import):
                for a in n.names:
                    tops.add(a.name.split(".")[0])
    return tops


def test_foundation_dependency_rules():
    # core imports nothing above it (no runtime/api/gateway/replay/memory/events/telemetry/tenant)
    forbidden_for_core = {"runtime", "api", "gateway", "replay", "events", "telemetry", "tenant"}
    assert not (_imports("core") & forbidden_for_core), _imports("core")
    # telemetry + tenant are floor packages: no upward imports (incl. not core)
    assert not (_imports("telemetry") & {"runtime", "api", "gateway", "replay", "core", "events"})
    assert not (_imports("tenant") & {"runtime", "api", "gateway", "replay", "core", "events"})
