"""Reasoning Runtime Intelligence (M5.5B-2) tests.

Verifies the additive intelligence layer: streaming lifecycle + backpressure, retry budget +
history + circuit breaker, cancellation (terminal), tool coordination over an injected
ToolPort, scheduler ordering, policy verdicts, provider neutrality, tenant isolation, the
execution trace, complete reasoning lineage, replay/gateway compatibility, extension
interfaces, architecture boundaries, and determinism. The core path stays byte-for-byte
unchanged when intelligence is off (asserted here).
"""

from __future__ import annotations

import asyncio

import pytest

from core.errors import PlatformError
from core.ports.tool import ToolPort, ToolResult, ToolSpec
from reasoning import (
    CancellationManager,
    CancelReason,
    ChunkKind,
    InferenceMessage,
    InferenceRequest,
    InferenceState,
    ReasoningConfiguration,
    ReasoningFactory,
    ReasoningPolicies,
    ReasoningPolicyEngine,
    ReasoningScheduler,
    ReasoningScope,
    ReasoningTenancy,
    ResponseChunk,
    ResponsePackage,
    RetryController,
    RetryExhausted,
    StreamingController,
)
from reasoning.policies import (
    ProviderPolicy,
    RetryControlPolicy,
    StreamingPolicy,
    ToolPolicy,
)
from reasoning.serializer import ReasoningSerializer


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


def _intel(config=None, **kwargs):
    cfg = config or ReasoningConfiguration.default()
    kwargs.setdefault("clock", lambda: 100.0)
    return ReasoningFactory.create(cfg, intelligence=True, **kwargs)


# ── the core path stays unchanged when intelligence is off ───────────────────────
def test_core_path_unchanged_when_intelligence_off():
    core = ReasoningFactory.create(ReasoningConfiguration.default(), clock=lambda: 100.0)
    intel = _intel()
    p_core = core.infer(_req())
    p_intel = intel.infer(_req())
    # same assembled text + response hash; the core path carries no trace
    assert p_core.text == p_intel.text
    assert p_core.response_hash == p_intel.response_hash
    assert p_core.lineage.execution_trace.events == ()


# ── streaming lifecycle + backpressure ───────────────────────────────────────────
def _chunks(n, *, final=True):
    out = [ResponseChunk(id=f"c{i}", index=i, delta=f"w{i}", kind=ChunkKind.TOKEN)
           for i in range(n)]
    if final and out:
        out[-1] = ResponseChunk(id="cf", index=n - 1, delta="w", kind=ChunkKind.FINAL, done=True)
    return out


def test_streaming_controller_lifecycle_and_partials():
    sc = StreamingController()
    session = sc.run(_chunks(3))
    assert session.metadata.chunk_count == 3
    assert session.final is True
    assert list(sc.partials(_chunks(3))) == ["w0", "w0w1", "w0w1w"]


def test_streaming_backpressure_triggers_on_cap():
    pe = ReasoningPolicyEngine(ReasoningPolicies(streaming=StreamingPolicy(max_buffered_chunks=2)))
    sc = StreamingController(pe)
    session = sc.run(_chunks(5, final=False))
    assert session.backpressure_events >= 1
    assert sc.backpressure_verdict(10).ok is False


def test_streaming_timeout_hook_is_deterministic():
    pe = ReasoningPolicyEngine(ReasoningPolicies(streaming=StreamingPolicy()))
    sc = StreamingController(pe)
    assert sc.run(_chunks(2), elapsed_s=0.0).timed_out is False
    assert sc.run(_chunks(2), elapsed_s=9999.0).timed_out is True


# ── retry budget + history + circuit breaker ─────────────────────────────────────
def test_retry_records_history_then_succeeds():
    pe = ReasoningPolicyEngine(ReasoningPolicies(retry=RetryControlPolicy()))
    rc = RetryController(pe)
    calls = {"n": 0}

    async def flaky(attempt):
        calls["n"] += 1
        if calls["n"] < 3:
            raise RuntimeError("provider glitch")
        return "ok"

    value, outcome = asyncio.run(rc.run(flaky, kind="provider", now=100.0))
    assert value == "ok"
    assert outcome.attempts == 3
    assert len(outcome.history) == 2
    # deterministic backoff recorded (exponential from core RetryPolicy)
    assert outcome.history[0].delay_s == pytest.approx(0.1)
    assert outcome.history[1].delay_s == pytest.approx(0.2)


def test_retry_budget_exhausts():
    rc = RetryController(ReasoningPolicyEngine(ReasoningPolicies(
        retry=RetryControlPolicy(backoff=__import__("core.policies", fromlist=["RetryPolicy"])
                                 .RetryPolicy(max_attempts=2)))))

    async def always_fail(attempt):
        raise RuntimeError("down")

    with pytest.raises(RetryExhausted):
        asyncio.run(rc.run(always_fail, kind="provider"))


def test_retry_circuit_breaker_trips():
    rc = RetryController(ReasoningPolicyEngine(ReasoningPolicies(
        retry=RetryControlPolicy(circuit_breaker_threshold=1))))

    async def always_fail(attempt):
        raise RuntimeError("down")

    with pytest.raises(RetryExhausted):
        asyncio.run(rc.run(always_fail, kind="provider"))
    assert rc.statistics["circuit_trips"] == 1


def test_retry_non_retryable_reraises():
    rc = RetryController()

    async def boom(attempt):
        raise ValueError("bad")

    with pytest.raises(ValueError):
        asyncio.run(rc.run(boom, retryable=lambda e: not isinstance(e, ValueError)))


# ── cancellation (always terminal) ───────────────────────────────────────────────
def test_cancellation_reaches_terminal_state():
    cm = CancellationManager()
    sig = cm.cancel("tok1", CancelReason.MANUAL, now=100.0)
    pkg = ResponsePackage(id="p", session_id="s1", scope=ReasoningScope(),
                          state=InferenceState.STREAMING)
    cancelled = cm.apply(pkg, sig, clock=lambda: 100.0)
    assert cancelled.state is InferenceState.CANCELLED
    assert cancelled.cancellation.reason == "manual"
    assert cancelled.lineage.cancellation_history[0].reason == "manual"
    # idempotent
    assert cm.apply(cancelled, sig).state is InferenceState.CANCELLED


def test_cancellation_session_scope_and_before_start():
    rt = _intel()
    rt.cancel_session("s9")
    resp = asyncio.run(rt.ainfer(_req(session_id="s9", cancellation_token="t9")))
    assert resp.delivered is False
    assert resp.package.state is InferenceState.CANCELLED
    assert resp.package.provider == "echo"  # scope recorded, but no streamed chunks
    assert resp.package.chunks == ()


# ── tool coordination over an injected ToolPort ──────────────────────────────────
class _StubToolPort(ToolPort):
    def __init__(self, ok=True):
        self._ok = ok
        self.invoked = []

    def register(self, spec: ToolSpec) -> None: ...
    def list(self):
        return ()

    async def invoke(self, call):
        self.invoked.append(call.name)
        return ToolResult(call_id=call.call_id, ok=self._ok,
                          output="42" if self._ok else None,
                          error=None if self._ok else "tool failed")


class _ToolProvider:
    name = "tooly"

    def stream(self, request):
        yield ResponseChunk(id="t0", index=0, delta="{}", kind=ChunkKind.TOOL_CALL,
                            metadata={"name": "lookup", "call_id": "call-1"})
        yield ResponseChunk(id="t1", index=1, delta="done", kind=ChunkKind.FINAL, done=True)


def test_tool_coordination_invokes_injected_port():
    port = _StubToolPort()
    rt = _intel(tool_port=port, providers=(_ToolProvider(),))
    pkg = rt.infer(_req(provider="tooly"))
    assert port.invoked == ["lookup"]  # Reasoning coordinated; the port executed
    names = [d["decision"] for d in pkg.tool_decisions]
    assert "requested" in names and "completed" in names
    assert any(t["name"] == "lookup" for t in pkg.lineage.tool_history)


def test_tool_policy_rejects_disallowed_tool():
    port = _StubToolPort()
    cfg = ReasoningConfiguration.default()
    cfg = ReasoningConfiguration(policies=ReasoningPolicies(
        tool=ToolPolicy(allowed_tools=frozenset({"other"}))))
    rt = ReasoningFactory.create(cfg, intelligence=True, tool_port=port,
                                 providers=(_ToolProvider(),), clock=lambda: 100.0)
    pkg = rt.infer(_req(provider="tooly"))
    assert port.invoked == []  # rejected before invocation
    assert any(d["decision"] == "rejected" for d in pkg.tool_decisions)


# ── scheduler ordering ───────────────────────────────────────────────────────────
def test_scheduler_orders_by_priority_then_fifo():
    sch = ReasoningScheduler()
    sch.submit(_req(session_id="a"), priority=1)
    sch.submit(_req(session_id="b"), priority=5)
    sch.submit(_req(session_id="c"), priority=5)
    order = [i.request.session_id for i in sch.drain()]
    assert order == ["b", "c", "a"]  # higher priority first; FIFO within a priority


def test_scheduler_defers_until_ready():
    sch = ReasoningScheduler()
    sch.submit(_req(session_id="later"), deferred_until=200.0)
    assert sch.pop(now=100.0) is None       # deferred, not yet ready
    assert sch.pop(now=250.0).request.session_id == "later"


# ── policy verdicts ──────────────────────────────────────────────────────────────
def test_policy_engine_verdicts():
    pe = ReasoningPolicyEngine(ReasoningPolicies(
        provider=ProviderPolicy(allowed_providers=frozenset({"echo"}))))
    assert pe.admit_provider("echo").ok is True
    assert pe.admit_provider("openai").ok is False
    assert pe.should_retry("provider", attempt=1).ok is True
    assert pe.should_retry("network", attempt=1).ok is False


# ── provider neutrality (custom provider, no SDK) ────────────────────────────────
def test_provider_neutrality_custom_provider():
    rt = _intel(providers=(_ToolProvider(),))
    pkg = rt.infer(_req(provider="tooly", streaming=True))
    assert pkg.provider == "tooly"
    assert pkg.text.endswith("done")


# ── tenant isolation (before provider selection) ─────────────────────────────────
def _tenant(org_id="o1", user_id="u1"):
    from tenant import OrganizationContext, TenantContext

    return TenantContext(org=OrganizationContext(org_id=org_id), user_id=user_id)


def test_tenancy_blocks_cross_org(monkeypatch):
    rt = _intel()
    # request scoped to a different org → denied before provider selection
    with pytest.raises(PlatformError):
        rt.infer_scoped(_req(scope=ReasoningScope(org_id="orgB", session_id="s1")),
                        _tenant(org_id="orgA"))


def test_tenancy_allows_same_org():
    rt = _intel()
    resp = rt.infer_scoped(_req(scope=ReasoningScope(org_id="o1", session_id="s1")),
                           _tenant(org_id="o1"))
    assert resp.delivered is True
    assert resp.package.org_id == "o1"


def test_tenancy_provider_allow_list():
    tn = ReasoningTenancy(allowed_providers=frozenset({"echo"}))
    with pytest.raises(PlatformError):
        tn.guard(ReasoningScope(), None, provider="openai")
    assert tn.guard(ReasoningScope(), None, provider="echo").scope is not None


# ── execution trace + complete lineage ───────────────────────────────────────────
def test_execution_trace_records_stages():
    rt = _intel()
    pkg = rt.infer(_req())
    stages = [e.stage for e in pkg.lineage.execution_trace.events]
    assert stages[0] == "provider_selection"
    assert "streaming" in stages and "completion" in stages
    # ordered + seq-stamped
    assert [e.seq for e in pkg.lineage.execution_trace.events] == \
        list(range(len(pkg.lineage.execution_trace.events)))


def test_reasoning_lineage_is_complete():
    rt = _intel()
    ln = rt.infer(_req()).lineage
    # every element the milestone requires is present
    assert ln.prompt_hash == "ph1"
    assert ln.prompt_fingerprint == "pf1"
    assert ln.inference_request_hash.startswith("ir_")
    assert ln.provider == "echo" and ln.model == "default"
    assert ln.retry_history == () and ln.tool_history == ()
    assert ln.cancellation_history == ()
    assert ln.streaming.streamed is True
    assert ln.execution_trace.events  # trace preserved
    assert ln.response_hash.startswith("rp_")
    assert ln.response_version == "1"


# ── replay/gateway compatibility (serializer round-trips the new lineage) ────────
def test_serializer_round_trips_trace_and_cancellation_history():
    rt = _intel()
    pkg = rt.infer(_req())
    ser = ReasoningSerializer()
    back = ser.package_from_document(ser.package_to_document(pkg))
    assert back.lineage.execution_trace.events == pkg.lineage.execution_trace.events
    assert back.lineage.response_version == pkg.lineage.response_version
    assert back.response_hash == pkg.response_hash


def test_determinism_intelligent_path():
    a = _intel().infer(_req())
    b = _intel().infer(_req())
    assert a.response_hash == b.response_hash
    assert [e.stage for e in a.lineage.execution_trace.events] == \
        [e.stage for e in b.lineage.execution_trace.events]


# ── extension interfaces (interfaces only; no implementations shipped) ───────────
def test_extension_interfaces_are_abstract():
    from reasoning import (
        AgentBridge,
        AsyncModelProvider,
        MultiModalResponder,
        ReasoningConsumer,
        ReasoningOptimizer,
        ResponseOptimizer,
        WorkflowBridge,
    )

    for iface in (AsyncModelProvider, ReasoningConsumer, ResponseOptimizer, ReasoningOptimizer,
                  MultiModalResponder, AgentBridge, WorkflowBridge):
        with pytest.raises(TypeError):
            iface()  # abstract — cannot instantiate


# ── architecture boundary: tenant is a leaf foundation dep, no runtime coupling ──
def test_no_runtime_coupling_in_intelligence_modules():
    import ast
    import pathlib

    forbidden = {"memory", "knowledge", "context", "prompt", "runtime", "api", "gateway",
                 "replay", "voicebot"}
    tops: set[str] = set()
    for name in ("streaming", "retry", "cancellation", "tools", "scheduler", "policies",
                 "tenancy", "extensions"):
        tree = ast.parse(pathlib.Path(f"reasoning/{name}.py").read_text())
        for n in ast.walk(tree):
            if isinstance(n, ast.ImportFrom) and n.module and not n.level:
                tops.add(n.module.split(".")[0])
    assert not (tops & forbidden), tops
