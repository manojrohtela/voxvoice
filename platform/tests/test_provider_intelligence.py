"""Provider Runtime Intelligence & Closure (M5.6B-2) tests.

Verifies the additive intelligence layer: deterministic routing (13 strategies), failover,
circuit breaker, provider health, the policy engine, the ProviderModelBridge (Reasoning
integration without touching Reasoning), execution trace, provider neutrality, tenant
isolation, replay/gateway compatibility, serializer round-trips, architecture boundaries,
determinism, and the intelligence-off invariant (the M5.6B-1 core path is unchanged).
"""

from __future__ import annotations

import ast
import pathlib

import pytest

from core.errors import PlatformError
from provider import (
    BreakerState,
    CircuitBreaker,
    CircuitBreakerPolicy,
    FailoverController,
    FailoverReason,
    OrganizationPolicy,
    ProviderAccessPolicy,
    ProviderCapabilities,
    ProviderConfiguration,
    ProviderDescriptor,
    ProviderFactory,
    ProviderHealthManager,
    ProviderHealthState,
    ProviderPolicies,
    ProviderPolicyEngine,
    ProviderRegistry,
    ProviderRequest,
    ProviderScope,
    ProviderSerializer,
    ProviderTenancy,
    RegionPolicy,
    RoutingContext,
    RoutingStrategy,
)
from provider.catalog import CapabilityCatalog
from provider.domain import ProviderMessage
from provider.router import ProviderRouter
from provider.stores import EchoProviderAdapter


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


def _reg_with(*names_costs_latency):
    reg = ProviderRegistry()
    for name, cost, latency in names_costs_latency:
        reg.register(
            ProviderDescriptor(name=name, cost_class=cost, latency_class=latency,
                              capabilities=ProviderCapabilities(chat=True)),
            EchoProviderAdapter(),
        )
    return reg


def _intel(config=None, **kwargs):
    cfg = config or ProviderConfiguration.default()
    kwargs.setdefault("clock", lambda: 100.0)
    return ProviderFactory.create(cfg, intelligence=True, **kwargs)


# ── the core path stays unchanged when intelligence is off ───────────────────────
def test_core_path_unchanged_when_intelligence_off():
    core = ProviderFactory.create(ProviderConfiguration.default(), clock=lambda: 100.0)
    intel = _intel()
    p_core = core.execute(_req())
    p_intel = intel.execute(_req())
    assert p_core.text == p_intel.text
    assert p_core.response_hash == p_intel.response_hash
    assert p_core.trace.events == ()


# ── deterministic routing (13 strategies) ─────────────────────────────────────────
def test_router_cheapest_and_lowest_latency():
    reg = _reg_with(("alpha", 2, 1), ("beta", 1, 2))
    router = ProviderRouter(reg, CapabilityCatalog(reg))
    req = _req()
    assert router.route(req, strategy=RoutingStrategy.CHEAPEST).candidates == ("beta", "alpha")
    assert router.route(req, strategy=RoutingStrategy.LOWEST_LATENCY).candidates == \
        ("alpha", "beta")


def test_router_highest_quality():
    reg = _reg_with(("alpha", 1, 1), ("beta", 1, 1))
    router = ProviderRouter(reg, CapabilityCatalog(reg))
    ctx = RoutingContext(quality_rank={"beta": 0, "alpha": 5})
    decision = router.route(_req(), strategy=RoutingStrategy.HIGHEST_QUALITY, context=ctx)
    assert decision.candidates[0] == "beta"


def test_router_region_aware_prefers_matching_region():
    reg = ProviderRegistry()
    reg.register(
        ProviderDescriptor(name="us", region="us", capabilities=ProviderCapabilities(chat=True)),
        EchoProviderAdapter(),
    )
    reg.register(
        ProviderDescriptor(name="eu", region="eu", capabilities=ProviderCapabilities(chat=True)),
        EchoProviderAdapter(),
    )
    router = ProviderRouter(reg, CapabilityCatalog(reg))
    req = _req(scope=ProviderScope(session_id="s1", region="eu"))
    decision = router.route(req, strategy=RoutingStrategy.REGION_AWARE)
    assert decision.candidates[0] == "eu"


def test_router_weighted_is_deterministic():
    reg = _reg_with(("alpha", 1, 1), ("beta", 1, 1))
    router = ProviderRouter(reg, CapabilityCatalog(reg))
    ctx = RoutingContext(weights={"alpha": 9, "beta": 1})
    a = ProviderRouter(reg, CapabilityCatalog(reg)).route(
        _req(), strategy=RoutingStrategy.WEIGHTED, context=ctx)
    b = router.route(_req(), strategy=RoutingStrategy.WEIGHTED, context=ctx)
    # deterministic given the same call sequence (same internal counter state)
    assert a.candidates == b.candidates


def test_router_canary_and_ab_testing_deterministic_bucket():
    reg = _reg_with(("primary", 1, 1), ("canary", 1, 1))
    router = ProviderRouter(reg, CapabilityCatalog(reg))
    ctx = RoutingContext(canary="canary", canary_percent=100)  # always bucket in
    decision = router.route(
        _req(session_id="fixed"), strategy=RoutingStrategy.CANARY, context=ctx
    )
    assert decision.candidates[0] == "canary"

    ctx0 = RoutingContext(canary="canary", canary_percent=0)  # never bucket in
    decision0 = router.route(
        _req(session_id="fixed"), strategy=RoutingStrategy.CANARY, context=ctx0
    )
    assert decision0.candidates  # deterministic, non-empty either way

    ctx_ab = RoutingContext(ab_variants=("primary", "canary"))
    d1 = router.route(_req(session_id="fixed"), strategy=RoutingStrategy.AB_TESTING, context=ctx_ab)
    d2 = router.route(_req(session_id="fixed"), strategy=RoutingStrategy.AB_TESTING, context=ctx_ab)
    assert d1.candidates == d2.candidates  # deterministic for the same session_id


def test_router_backup_provider_appended():
    reg = _reg_with(("primary", 1, 1), ("backup", 2, 2))
    router = ProviderRouter(reg, CapabilityCatalog(reg))
    ctx = RoutingContext(backup_of={"primary": "backup"})
    decision = router.route(_req(), strategy=RoutingStrategy.BACKUP_PROVIDER, context=ctx)
    assert decision.candidates == ("primary", "backup")


def test_router_manual_and_failover_strategy():
    reg = _reg_with(("alpha", 1, 1))
    router = ProviderRouter(reg, CapabilityCatalog(reg))
    manual = router.route(_req(provider="alpha"), strategy=RoutingStrategy.MANUAL)
    assert manual.candidates == ("alpha",)
    auto_chain = router.route(_req(), strategy=RoutingStrategy.FAILOVER)
    assert auto_chain.candidates == ("alpha",)


def test_router_no_random_import():
    tree = ast.parse(pathlib.Path("provider/router.py").read_text())
    tops = {n.names[0].name for n in ast.walk(tree) if isinstance(n, ast.Import)}
    assert "random" not in tops


# ── failover ──────────────────────────────────────────────────────────────────────
def test_failover_walks_chain_and_exhausts():
    reg = _reg_with(("alpha", 1, 1), ("beta", 2, 2))
    router = ProviderRouter(reg, CapabilityCatalog(reg))
    decision = router.route(_req(), strategy=RoutingStrategy.AUTO)
    fc = FailoverController()
    first = fc.next(decision, attempted=(), reason=FailoverReason.UNAVAILABLE)
    assert first.candidate == "alpha" and not first.exhausted
    second = fc.next(decision, attempted=first.attempted, reason=FailoverReason.UNAVAILABLE)
    assert second.candidate == "beta"
    third = fc.next(decision, attempted=second.attempted, reason=FailoverReason.UNAVAILABLE)
    assert third.exhausted
    assert fc.statistics["selections"] == 1 and fc.statistics["failovers"] == 1
    assert fc.statistics["exhausted"] == 1


def test_runtime_fails_over_to_next_candidate_on_adapter_error():
    class BoomAdapter:
        name = "boom"
        capabilities = ProviderCapabilities(chat=True)

        def stream(self, request):
            raise RuntimeError("boom")
            yield  # pragma: no cover — makes this a generator function

    # cost_class=0 ties with the auto-registered "echo" adapter; "boom" < "echo" alphabetically
    # so boom is tried first, fails, and the chain fails over to the healthy "echo" adapter.
    rt = _intel(adapters=[
        (ProviderDescriptor(name="boom", cost_class=0, capabilities=BoomAdapter.capabilities),
         BoomAdapter()),
    ])
    resp = rt.execute(_req())
    assert resp.provider == "echo"
    assert any(e.stage == "health_change" for e in resp.trace.events)


# ── circuit breaker ───────────────────────────────────────────────────────────────
def test_circuit_breaker_opens_and_half_opens_deterministically():
    cb = CircuitBreaker(CircuitBreakerPolicy(failure_threshold=2, reset_timeout_s=10.0),
                        clock=lambda: 0.0)
    assert cb.allow("x", now=0.0) is True
    cb.record_failure("x", now=0.0)
    assert cb.state_for("x") is BreakerState.CLOSED
    cb.record_failure("x", now=0.0)
    assert cb.state_for("x") is BreakerState.OPEN
    assert cb.allow("x", now=5.0) is False  # within reset window
    assert cb.allow("x", now=11.0) is True  # reset window elapsed -> half-open
    assert cb.state_for("x") is BreakerState.HALF_OPEN
    cb.record_success("x")
    assert cb.state_for("x") is BreakerState.CLOSED


def test_circuit_breaker_reopens_on_half_open_failure():
    cb = CircuitBreaker(CircuitBreakerPolicy(failure_threshold=1, reset_timeout_s=1.0),
                        clock=lambda: 0.0)
    cb.record_failure("x", now=0.0)
    assert cb.state_for("x") is BreakerState.OPEN
    assert cb.allow("x", now=2.0) is True
    assert cb.state_for("x") is BreakerState.HALF_OPEN
    cb.record_failure("x", now=2.0)
    assert cb.state_for("x") is BreakerState.OPEN


# ── provider health ────────────────────────────────────────────────────────────────
def test_health_manager_degrades_and_recovers_conservatively():
    reg = _reg_with(("alpha", 1, 1))
    hm = ProviderHealthManager(reg)
    assert reg.entry("alpha").health is ProviderHealthState.HEALTHY
    hm.record_failure("alpha")
    assert reg.entry("alpha").health is ProviderHealthState.DEGRADED
    hm.record_failure("alpha")
    assert reg.entry("alpha").health is ProviderHealthState.UNAVAILABLE
    assert "alpha" in hm.excluded()
    hm.reactivate("alpha")
    assert reg.entry("alpha").health is ProviderHealthState.DEGRADED
    hm.record_success("alpha")
    assert reg.entry("alpha").health is ProviderHealthState.HEALTHY


def test_health_manager_threshold_debounces_single_blip():
    reg = _reg_with(("alpha", 1, 1))
    hm = ProviderHealthManager(reg, policy=__import__("provider.health", fromlist=["HealthPolicy"])
                              .HealthPolicy(failure_threshold=3))
    hm.record_failure("alpha")
    hm.record_failure("alpha")
    assert reg.entry("alpha").health is ProviderHealthState.HEALTHY  # not yet degraded
    hm.record_failure("alpha")
    assert reg.entry("alpha").health is ProviderHealthState.DEGRADED


# ── policy engine (verdicts only) ──────────────────────────────────────────────────
def test_policy_engine_verdicts():
    pe = ProviderPolicyEngine(ProviderPolicies(
        access=ProviderAccessPolicy(denied_providers=frozenset({"blocked"})),
        organization=OrganizationPolicy(allowed_orgs=frozenset({"o1"})),
        region=RegionPolicy(allowed_regions=frozenset({"us"})),
    ))
    assert pe.admit_provider("blocked").ok is False
    assert pe.admit_provider("echo").ok is True
    assert pe.admit_organization("o1").ok is True
    assert pe.admit_organization("o2").ok is False
    assert pe.admit_region("us").ok is True
    assert pe.admit_region("eu").ok is False


def test_policy_conflicting_allow_deny_rejected():
    from core.policies import InvalidPolicy

    with pytest.raises(InvalidPolicy):
        ProviderPolicies(access=ProviderAccessPolicy(
            allowed_providers=frozenset({"a"}), denied_providers=frozenset({"a"})))


def test_router_excludes_denied_and_unavailable_providers():
    reg = _reg_with(("alpha", 1, 1), ("beta", 1, 1))
    reg.mark_outcome_failure("beta")
    reg.mark_outcome_failure("beta")  # -> UNAVAILABLE
    pe = ProviderPolicyEngine(ProviderPolicies(
        access=ProviderAccessPolicy(denied_providers=frozenset({"alpha"}))))
    router = ProviderRouter(reg, CapabilityCatalog(reg), policy_engine=pe)
    decision = router.route(_req(), strategy=RoutingStrategy.AUTO)
    assert decision.candidates == ()  # alpha denied, beta unavailable


# ── ProviderModelBridge (Reasoning integration; Reasoning untouched) ──────────────
def test_provider_model_bridge_streams_via_reasoning():
    from bridge import ProviderModelBridge
    from reasoning import ReasoningConfiguration, ReasoningFactory
    from reasoning.domain import InferenceMessage, InferenceRequest

    provider_rt = ProviderFactory.default()
    bridge = ProviderModelBridge(provider_rt)
    reasoning_rt = ReasoningFactory.create(ReasoningConfiguration.default(), providers=[bridge])

    req = InferenceRequest(
        session_id="s1", provider="provider-runtime",
        messages=(InferenceMessage(role="user", content="How do I cover drive?"),),
    )
    pkg = reasoning_rt.infer(req)
    assert pkg.text == "echo: How do I cover drive?"
    assert pkg.provider == "provider-runtime"


def test_bridge_module_does_not_modify_reasoning_or_provider():
    # the bridge lives in its own package; neither sibling runtime imports it or the other
    import ast as _ast

    for pkg in ("provider", "reasoning"):
        tops: set[str] = set()
        for p in pathlib.Path(pkg).rglob("*.py"):
            for n in _ast.walk(_ast.parse(p.read_text())):
                if isinstance(n, _ast.ImportFrom) and n.module and not n.level:
                    tops.add(n.module.split(".")[0])
        assert "bridge" not in tops
    assert "reasoning" not in _imports("provider")
    assert "provider" not in _imports("reasoning")


# ── tenant isolation ──────────────────────────────────────────────────────────────
def _tenant(org_id="o1"):
    from tenant import OrganizationContext, TenantContext

    return TenantContext(org=OrganizationContext(org_id=org_id), user_id="u1")


def test_tenancy_blocks_cross_org():
    rt = _intel()
    with pytest.raises(PlatformError):
        rt.execute_scoped(_req(scope=ProviderScope(org_id="orgB", session_id="s1")),
                          _tenant(org_id="orgA"))


def test_tenancy_allows_same_org():
    rt = _intel()
    resp = rt.execute_scoped(_req(scope=ProviderScope(org_id="o1", session_id="s1")),
                             _tenant(org_id="o1"))
    assert resp.delivered is True
    assert resp.response.org_id == "o1"


def test_tenancy_provider_allow_list():
    tn = ProviderTenancy(allowed_providers=frozenset({"echo"}))
    with pytest.raises(PlatformError):
        tn.guard(ProviderScope(), None, provider="openai")
    assert tn.guard(ProviderScope(), None, provider="echo").scope is not None


# ── execution trace + provider neutrality ─────────────────────────────────────────
def test_execution_trace_records_stages():
    rt = _intel()
    resp = rt.execute(_req())
    stages = [e.stage for e in resp.trace.events]
    assert "routing_decision" in stages and "completion" in stages
    assert [e.seq for e in resp.trace.events] == list(range(len(resp.trace.events)))


def test_provider_neutrality_custom_adapter():
    rt = _intel(adapters=[
        (ProviderDescriptor(name="fixed", capabilities=ProviderCapabilities(chat=True)),
         _FixedAdapter()),
    ])
    resp = rt.execute(_req(provider="fixed"))
    assert resp.provider == "fixed" and resp.text == "fixed-answer"


class _FixedAdapter:
    name = "fixed"
    capabilities = ProviderCapabilities(chat=True)

    def stream(self, request):
        from provider.domain import ChunkKind, ProviderChunk
        yield ProviderChunk(id="c0", index=0, delta="fixed-answer",
                            kind=ChunkKind.FINAL, done=True)


# ── serializer round-trip (replay-safe) + determinism ─────────────────────────────
def test_serializer_round_trips_trace():
    rt = _intel()
    resp = rt.execute(_req())
    ser = ProviderSerializer()
    back = ser.response_from_document(ser.response_to_document(resp))
    assert back.trace.events == resp.trace.events
    assert back.response_hash == resp.response_hash


def test_determinism_intelligent_path():
    a = _intel().execute(_req())
    b = _intel().execute(_req())
    assert a.response_hash == b.response_hash
    assert [e.stage for e in a.trace.events] == [e.stage for e in b.trace.events]


# ── architecture boundary: dependency rules (mirrors the core test) ───────────────
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


def test_no_runtime_coupling_in_intelligence_modules():
    forbidden = {"memory", "knowledge", "context", "prompt", "reasoning", "runtime", "api",
                 "gateway", "replay", "voicebot"}
    tops: set[str] = set()
    for name in ("router", "health", "policies", "failover", "circuit_breaker", "tenancy"):
        tree = ast.parse(pathlib.Path(f"provider/{name}.py").read_text())
        for n in ast.walk(tree):
            if isinstance(n, ast.ImportFrom) and n.module and not n.level:
                tops.add(n.module.split(".")[0])
    assert not (tops & forbidden), tops


def test_bridge_dependency_rules():
    # the bridge may import core, provider, and reasoning (its whole purpose) — nothing else
    forbidden = {"memory", "knowledge", "context", "prompt", "runtime", "api", "gateway",
                 "replay", "voicebot", "events"}
    assert not (_imports("bridge") & forbidden), _imports("bridge")
