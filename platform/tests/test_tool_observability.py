"""Tool Runtime Observability & Replay (M6.1B-3) tests.

Verifies `ExecutionTrace`/`ToolGovernanceDecisionTrace` (pure projections),
`DecisionReason`, `ReplayRecord` + `ReplayEngine` (deterministic replay +
mismatch detection), `ToolRuntimeMetrics`, `ToolRuntimeDiagnostics`,
`FailureAnalysis`, `ToolAuditRecord`, serialization round-trips, the temporary
`ReplayRepository`, expanded statistics, replay determinism, dependency
boundaries, and no ownership leakage into execution/governance. Streaming, async
execution, cancellation propagation, and Workflow/Agent/MCP/A2A integration are
non-goals here (M6.1B-4+).
"""

from __future__ import annotations

import ast
import pathlib
from dataclasses import replace

import pytest

from core.errors import ConfigError
from core.events import Event, EventSource, EventType
from tool import (
    Classification,
    EchoToolAdapter,
    ExecutionState,
    ToolConfiguration,
    ToolExecutionOutcome,
    ToolFactory,
    ToolRequest,
)
from tool.extensions import PolicyVerdict
from tool.governance import (
    Effect,
    GovernanceDecision,
    PolicyLevel,
    PolicyRule,
    ToolGovernanceTrace,
)
from tool.governance.domain import AuthorizationDecision, TenantDecision
from tool.observability import (
    DecisionReason,
    FailureCategory,
    ObservabilityConfiguration,
    ObservabilityFactory,
    ObservabilitySerializer,
    ReasonCode,
    SourceLevel,
    ToolObservability,
    ToolRuntimeMetrics,
    analyze_failure,
    classify_error,
    compute_statistics,
    execution_trace_for,
    governance_trace_for,
    reason_for_decision,
    replay_record_for,
)
from tool.observability.replay_engine import ReplayEngine
from tool.observability.repository import ReplayRepository
from tool.stores import InMemoryDocumentStore


def _req(**kw) -> ToolRequest:
    base = dict(request_id="r1", tool="echo", namespace="internal", arguments={"x": 1})
    base.update(kw)
    return ToolRequest(**base)


def _runtime(**kwargs):
    kwargs.setdefault("clock", lambda: 100.0)
    return ToolFactory.create(ToolConfiguration.default(), **kwargs)


def _obs(**kwargs):
    kwargs.setdefault("clock", lambda: 100.0)
    return ObservabilityFactory.create(ObservabilityConfiguration.default(), **kwargs)


# ── ExecutionTrace ─────────────────────────────────────────────────────────────
def test_execution_trace_success_transitions():
    rt = _runtime()
    request = _req()
    result = rt.execute(request)
    trace = execution_trace_for(request, result, started_at=100.0, ended_at=100.5)
    assert trace.transitions == ("received", "validating", "executing", "completed")
    assert trace.duration_ms == pytest.approx(500.0)
    assert trace.classification == Classification.READ_ONLY.value
    assert trace.output_hash == result.result_hash


def test_execution_trace_failure_transitions_validation_phase():
    rt = _runtime()
    request = _req(tool="unknown")
    result = rt.execute(request)
    trace = execution_trace_for(request, result)
    assert trace.transitions == ("received", "validating", "failed")


def test_execution_trace_never_stores_raw_arguments():
    rt = _runtime()
    request = _req(arguments={"secret": "do-not-leak"})
    result = rt.execute(request)
    trace = execution_trace_for(request, result)
    assert "do-not-leak" not in repr(trace)
    assert trace.arguments_hash and trace.arguments_hash != "do-not-leak"


def test_execution_trace_is_deterministic():
    rt = _runtime()
    request = _req()
    result = rt.execute(request)
    t1 = execution_trace_for(request, result, started_at=1.0, ended_at=2.0)
    t2 = execution_trace_for(request, result, started_at=1.0, ended_at=2.0)
    assert t1 == t2


# ── DecisionReason + Governance trace ─────────────────────────────────────────
def _allow_decision(request_id="r1") -> GovernanceDecision:
    trace = ToolGovernanceTrace(
        tenant=TenantDecision(allowed=True),
        authorization=AuthorizationDecision(effect=Effect.ALLOW, identity=None, action="execute",
                                            resource="internal.echo"),
    )
    return GovernanceDecision(request_id=request_id, verdict=PolicyVerdict.ALLOW, trace=trace)


def _deny_decision_at_policy() -> GovernanceDecision:
    from tool.governance.domain import PolicyDecision

    trace = ToolGovernanceTrace(
        tenant=TenantDecision(allowed=True),
        policy=PolicyDecision(
            verdict=PolicyVerdict.DENY, level=PolicyLevel.SYSTEM, reason="blocked",
            trail=(PolicyRule(level=PolicyLevel.SYSTEM, verdict=PolicyVerdict.DENY),),
        ),
    )
    return GovernanceDecision(
        request_id="r1", verdict=PolicyVerdict.DENY, reason="blocked", trace=trace
    )


def test_reason_for_decision_allowed():
    reason = reason_for_decision(_allow_decision())
    assert reason.reason_code is ReasonCode.ALLOWED
    assert reason.source_level is SourceLevel.TOOL


def test_reason_for_decision_policy_denied_carries_matched_rule():
    reason = reason_for_decision(_deny_decision_at_policy())
    assert reason.reason_code is ReasonCode.POLICY_DENIED
    assert reason.source_level is SourceLevel.SYSTEM
    assert reason.matched_rule is not None


def test_reason_for_decision_tenant_denied_short_circuits_before_policy():
    from tool.governance.domain import PolicyDecision

    trace = ToolGovernanceTrace(
        tenant=TenantDecision(allowed=False, reason="cross-org"),
        policy=PolicyDecision(verdict=PolicyVerdict.ALLOW),  # would never actually run
    )
    decision = GovernanceDecision(request_id="r1", verdict=PolicyVerdict.DENY, trace=trace)
    reason = reason_for_decision(decision)
    assert reason.reason_code is ReasonCode.TENANT_DENIED
    assert reason.source_level is SourceLevel.TENANT


def test_decision_reason_is_deterministic_and_immutable():
    r1 = reason_for_decision(_deny_decision_at_policy())
    r2 = reason_for_decision(_deny_decision_at_policy())
    assert r1 == r2
    from dataclasses import FrozenInstanceError

    with pytest.raises(FrozenInstanceError):
        r1.reason_code = ReasonCode.ALLOWED  # type: ignore[misc]


def test_governance_trace_for_projects_no_execution_data():
    trace = governance_trace_for(_allow_decision())
    assert trace.policy_verdict is PolicyVerdict.ALLOW
    assert trace.tenant_allowed is True
    assert trace.authorization_verdict is Effect.ALLOW
    assert not hasattr(trace, "output")  # never execution data


# ── ReplayRecord + ReplayEngine ────────────────────────────────────────────────
def test_replay_record_round_trips_and_validates():
    rt = _runtime()
    request = _req()
    result = rt.execute(request)
    trace = execution_trace_for(request, result)
    record = replay_record_for(request, result, trace, now=100.0)

    engine = ReplayEngine()
    comparison = engine.validate(record)
    assert comparison.matches
    assert comparison.mismatches == ()


def test_replay_engine_detects_mismatch_on_tampered_record():
    rt = _runtime()
    request = _req()
    result = rt.execute(request)
    trace = execution_trace_for(request, result)
    record = replay_record_for(request, result, trace, now=100.0)

    tampered_trace = replace(trace, output_hash="deliberately-wrong")
    tampered = replace(record, execution_trace=tampered_trace, replay_hash="also-wrong")

    engine = ReplayEngine()
    comparison = engine.validate(tampered)
    assert not comparison.matches
    assert "output_hash" in comparison.mismatches


def test_replay_engine_compare_two_records():
    rt = _runtime()
    request = _req()
    result = rt.execute(request)
    trace = execution_trace_for(request, result)
    a = replay_record_for(request, result, trace, now=100.0)
    b = replay_record_for(request, result, trace, now=200.0)  # same content, different timestamp

    engine = ReplayEngine()
    comparison = engine.compare(a, b)
    assert comparison.matches


def test_replay_uses_only_echo_adapter_never_the_registered_adapter():
    """Even if the runtime executed a *different* deterministic adapter, replay
    always re-verifies via EchoToolAdapter — never a live registry lookup."""
    class _DoubleAdapter(EchoToolAdapter):
        name = "double"
        capabilities = frozenset({"double"})

        def execute(self, request):
            return ToolExecutionOutcome(output={"doubled": request.arguments.get("x", 0) * 2})

    from tool import ToolManifest

    manifest = ToolManifest(name="double", namespace="internal", version="1.0.0",
                            capabilities=frozenset({"double"}))
    rt = _runtime(tools=[(manifest, _DoubleAdapter())])
    request = _req(tool="double", arguments={"x": 5})
    result = rt.execute(request)
    trace = execution_trace_for(request, result)
    record = replay_record_for(request, result, trace, now=100.0)

    comparison = ReplayEngine().validate(record)
    assert not comparison.matches  # EchoToolAdapter output differs from DoubleAdapter's


# ── Metrics ────────────────────────────────────────────────────────────────────
def test_metrics_records_execution_and_replay():
    metrics = ToolRuntimeMetrics()
    rt = _runtime()
    request = _req()
    result = rt.execute(request)
    metrics.record_execution(result, duration_ms=50.0)
    snapshot = metrics.snapshot()
    assert snapshot["executions"] == 1
    assert snapshot["success"] == 1
    assert snapshot["average_latency_ms"] == 50.0
    assert snapshot["max_latency_ms"] == 50.0
    assert snapshot["tool_usage"] == {"internal.echo": 1}


def test_metrics_tracks_failure_and_classification_usage():
    metrics = ToolRuntimeMetrics()
    rt = _runtime()
    result = rt.execute(_req(tool="unknown"))
    metrics.record_execution(result)
    snapshot = metrics.snapshot()
    assert snapshot["failure"] == 1
    assert snapshot["classification_usage"][Classification.READ_ONLY.value] == 1


# ── FailureAnalysis ────────────────────────────────────────────────────────────
def test_failure_analysis_none_on_success():
    rt = _runtime()
    result = rt.execute(_req())
    assert analyze_failure(result) is None


@pytest.mark.parametrize("tool,expected_category", [
    ("unknown", FailureCategory.UNKNOWN_TOOL),
])
def test_failure_analysis_classifies_unknown_tool(tool, expected_category):
    rt = _runtime()
    result = rt.execute(_req(tool=tool))
    analysis = analyze_failure(result)
    assert analysis.category is expected_category
    assert analysis.recoverable is False


def test_failure_analysis_validation_error_is_recoverable():
    rt = _runtime()
    result = rt.execute(_req(arguments={}))  # missing required "name"? echo has no required args
    # echo tool has no required fields, so force a validation failure via a strict tool instead
    from tool import ToolManifest

    manifest = ToolManifest(name="strict", namespace="internal", version="1.0.0",
                            capabilities=frozenset({"strict"}),
                            inputs={"type": "object", "properties": {"name": {"type": "string"}},
                                    "required": ["name"]})
    rt2 = _runtime(tools=[(manifest, EchoToolAdapter())])
    bad = rt2.execute(_req(tool="strict", arguments={}))
    analysis = analyze_failure(bad)
    assert analysis.category is FailureCategory.VALIDATION
    assert analysis.recoverable is True
    del result


def test_classify_error_matches_runtime_error_prefixes():
    assert classify_error("unknown_tool: x")[0] is FailureCategory.UNKNOWN_TOOL
    assert classify_error("tool_retired")[0] is FailureCategory.RETIRED_TOOL
    assert classify_error("validation_error: x")[0] is FailureCategory.VALIDATION
    assert classify_error("execution_error: x")[0] is FailureCategory.EXECUTION
    assert classify_error(None)[0] is FailureCategory.UNKNOWN


# ── ToolObservability facade ───────────────────────────────────────────────────
def test_observability_trace_execution_updates_metrics():
    obs = _obs()
    rt = _runtime()
    request = _req()
    result = rt.execute(request)
    obs.trace_execution(request, result, started_at=100.0, ended_at=100.1)
    assert obs.metrics.snapshot()["executions"] == 1


def test_observability_record_and_validate_replay_round_trip():
    obs = _obs()
    rt = _runtime()
    request = _req()
    result = rt.execute(request)
    trace = obs.trace_execution(request, result)
    record = obs.record_replay(request, result, trace)
    comparison = obs.validate_replay(record.replay_id)
    assert comparison.matches
    assert obs.metrics.snapshot()["replay_success"] == 1


def test_observability_validate_unknown_replay_raises():
    obs = _obs()
    with pytest.raises(ConfigError):
        obs.validate_replay("never-recorded")


def test_observability_record_audit_never_includes_secrets():
    obs = _obs()
    audit = obs.record_audit(
        request_id="r1", tool="internal.echo", action="execute", decision="allow",
    )
    assert not hasattr(audit, "secret")
    assert not hasattr(audit, "arguments")
    assert audit.tool == "internal.echo"


def test_observability_diagnostics_reports_registry_and_metrics():
    obs = _obs()
    rt = _runtime()
    result = rt.execute(_req())
    obs.trace_execution(_req(), result)
    diag = obs.diagnostics(registry=rt.registry, catalog=rt.catalog, repository_result_count=1)
    assert diag.health_summary == "healthy"
    assert "internal.echo" in diag.registry_stats["tools"]
    assert diag.metric_snapshot["executions"] == 1


def test_observability_statistics_expands_per_dimension():
    obs = _obs()
    rt = _runtime()
    results = [rt.execute(_req(request_id=f"r{i}")) for i in range(3)]
    stats = obs.statistics(results)
    assert stats.per_tool["internal.echo"] == 3
    assert stats.per_namespace["internal"] == 3
    assert stats.failure_ratio == 0.0


def test_observability_events_emitted_through_injected_publisher():
    captured: list[Event] = []

    def publish(type: EventType, payload: dict, *, session_id: str = "", org_id: str = ""):
        event = Event(id=f"e{len(captured)}", type=type, ts=1.0, seq=len(captured),
                      session_id=session_id, source=EventSource.TOOL, org_id=org_id, data=payload)
        captured.append(event)
        return event

    obs = _obs(publish=publish)
    rt = _runtime()
    request = _req()
    result = rt.execute(request)
    obs.trace_execution(request, result)
    types = {e.type for e in captured}
    assert EventType.TOOL_EXECUTION_TRACED in types
    assert EventType.TOOL_METRICS_UPDATED in types


def test_observability_no_publisher_is_a_safe_no_op():
    obs = _obs(publish=None)
    rt = _runtime()
    request = _req()
    result = rt.execute(request)
    trace = obs.trace_execution(request, result)
    assert trace is not None


# ── Repository (temporary) ────────────────────────────────────────────────────
async def test_replay_repository_round_trip():
    from tool.observability.config import ObservabilityCollectionsConfig

    store = InMemoryDocumentStore()
    repo = ReplayRepository(store, ObservabilitySerializer(),
                           collections=ObservabilityCollectionsConfig())
    rt = _runtime()
    request = _req()
    result = rt.execute(request)
    trace = execution_trace_for(request, result)
    record = replay_record_for(request, result, trace, now=100.0)

    await repo.add_replay(record)
    fetched = await repo.get_replay(record.replay_id)
    assert fetched == record
    assert await repo.replay_count() == 1


# ── Statistics (pure function) ────────────────────────────────────────────────
def test_compute_statistics_failure_ratio():
    rt = _runtime()
    ok = rt.execute(_req(request_id="r1"))
    bad = rt.execute(_req(request_id="r2", tool="unknown"))
    stats = compute_statistics([ok, bad])
    assert stats.failure_ratio == pytest.approx(0.5)


# ── Serialization round-trips ──────────────────────────────────────────────────
def test_serializer_execution_trace_round_trip():
    rt = _runtime()
    request = _req()
    result = rt.execute(request)
    trace = execution_trace_for(request, result)
    s = ObservabilitySerializer()
    doc = s.execution_trace_to_dict(trace)
    assert s.execution_trace_from_dict(doc) == trace


def test_serializer_governance_trace_round_trip():
    s = ObservabilitySerializer()
    trace = governance_trace_for(_deny_decision_at_policy())
    doc = s.governance_trace_to_dict(trace)
    assert s.governance_trace_from_dict(doc) == trace


def test_serializer_replay_record_round_trip():
    rt = _runtime()
    request = _req()
    result = rt.execute(request)
    trace = execution_trace_for(request, result)
    record = replay_record_for(request, result, trace, now=100.0)
    s = ObservabilitySerializer()
    doc = s.replay_record_to_document(record)
    assert s.replay_record_from_document(doc) == record


def test_serializer_reason_round_trip():
    s = ObservabilitySerializer()
    reason = DecisionReason(reason_code=ReasonCode.POLICY_DENIED, source_level=SourceLevel.SYSTEM,
                            matched_rule="system:*:deny")
    doc = s.reason_to_dict(reason)
    assert s.reason_from_dict(doc) == reason


def test_serializer_audit_record_round_trip():
    from tool.observability import audit_record_for

    s = ObservabilitySerializer()
    record = audit_record_for(request_id="r1", tool="internal.echo", action="execute",
                              decision="allow", now=1.0)
    doc = s.audit_record_to_document(record)
    assert s.audit_record_from_document(doc) == record


def test_serializer_failure_analysis_round_trip():
    rt = _runtime()
    result = rt.execute(_req(tool="unknown"))
    analysis = analyze_failure(result)
    s = ObservabilitySerializer()
    doc = s.failure_analysis_to_dict(analysis)
    assert s.failure_analysis_from_dict(doc) == analysis


# ── Configuration ──────────────────────────────────────────────────────────────
def test_observability_configuration_rejects_non_positive_ttl():
    with pytest.raises(ConfigError):
        ObservabilityConfiguration(replay_ttl_s=0.0)


def test_observability_configuration_fingerprint_is_deterministic():
    a = ObservabilityConfiguration.default().fingerprint()
    b = ObservabilityConfiguration.default().fingerprint()
    assert a == b


# ── Dependency rules (no ownership leakage) ────────────────────────────────────
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


def test_tool_dependency_rules_still_hold_with_observability():
    forbidden = {"runtime", "api", "gateway", "replay", "events", "memory", "knowledge",
                 "context", "prompt", "reasoning", "provider", "bridge", "pipeline",
                 "voicebot", "observability", "tenant"}
    assert not (_imports("tool") & forbidden), _imports("tool")


def test_observability_owns_no_execution_or_governance_logic():
    """`ToolObservability` never exposes execute/invoke/run/evaluate/authorize —
    those stay `ToolRuntime`'s and `ToolGovernance`'s jobs respectively."""
    surface = {name for name in dir(ToolObservability) if not name.startswith("_")}
    forbidden_methods = {"execute", "invoke", "run", "aexecute", "evaluate", "authorize"}
    assert not (forbidden_methods & surface)


def test_execution_state_import_is_available_for_reference():
    # sanity: ExecutionState remains importable from tool (used implicitly by
    # runtime.py's own state machine); observability never imports it to drive
    # execution, only to read `ToolResult.state` values it's handed.
    assert ExecutionState.COMPLETED.value == "completed"
