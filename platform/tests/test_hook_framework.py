"""Hook Framework (M6.3B) tests.

Verifies manifest/descriptor validation, the registry (registration/
discovery/namespacing/version-resolution/lifecycle — no execution, no
capability resolution), deterministic ordering (priority desc, sequence
asc), filtering, execution policy (fail-closed/fail-open, mandatory
isolation), the engine (verdict collection + aggregation), replay metadata,
diagnostics, statistics, serialization round-trips, determinism, and the
`hooks → core[, tenant]` dependency rule (zero import of Tool Runtime,
Skill Runtime, Workflow, or Agent — this bundle's explicit constraint).
`HookCategory` is verified to be purely descriptive (never read by ordering/
filtering/policy/engine code).
"""

from __future__ import annotations

import ast
import pathlib
from dataclasses import FrozenInstanceError

import pytest

from core.errors import ConfigError
from core.fsm import FSMError
from hooks import (
    HOOK_LIFECYCLE,
    HookAggregationRule,
    HookCapabilityRequirement,
    HookCategory,
    HookConfiguration,
    HookDescriptor,
    HookEngine,
    HookEvent,
    HookFactory,
    HookFailureMode,
    HookFilter,
    HookLifecycleState,
    HookManifest,
    HookOutcome,
    HookPoint,
    HookRegistry,
    HookReplayEngine,
    HookRepository,
    HookRuntime,
    HookRuntimeScope,
    HookSerializer,
    HookTrigger,
    HookVerdict,
    advance_hook,
    aggregate_verdicts,
    build_event,
    compute_statistics,
    content_hash,
    default_policy_for,
    descriptor_id_for,
    hook_replay_record_for,
    ordered_hooks,
    validate_manifest,
)
from hooks.extensions import Hook
from hooks.stores import InMemoryDocumentStore


def _manifest(id_: str, **kw) -> HookManifest:
    base = dict(
        id=id_, namespace="internal", version="1.0.0",
        points=frozenset({HookPoint.BEFORE_TOOL_CALL}),
        scope=frozenset({HookRuntimeScope.TOOL}),
    )
    base.update(kw)
    return HookManifest(**base)


class _Verdict(Hook):
    def __init__(self, outcome: HookOutcome, name: str) -> None:
        self._outcome = outcome
        self._name = name

    def evaluate(self, event: HookEvent) -> HookVerdict:
        return HookVerdict(outcome=self._outcome, hook=self._name)


class _Raises(Hook):
    def evaluate(self, event: HookEvent) -> HookVerdict:
        raise ValueError("boom")


def _runtime(**kwargs) -> HookRuntime:
    kwargs.setdefault("clock", lambda: 100.0)
    return HookFactory.default(**kwargs)


def _registered_active(rt: HookRuntime, manifest: HookManifest, hook: Hook) -> HookDescriptor:
    d = rt.register(manifest, hook)
    rt.activate(manifest.namespace, manifest.id, manifest.version)
    return d


# ── HookManifest / HookDescriptor ──────────────────────────────────────────────
def test_manifest_is_immutable():
    m = _manifest("a")
    with pytest.raises(FrozenInstanceError):
        m.id = "other"  # type: ignore[misc]


def test_descriptor_is_immutable():
    m = _manifest("a")
    d = HookDescriptor(manifest=m)
    with pytest.raises(FrozenInstanceError):
        d.state = HookLifecycleState.ACTIVE  # type: ignore[misc]


def test_validate_manifest_requires_at_least_one_point():
    m = _manifest("a", points=frozenset())
    ok, errors = validate_manifest(m)
    assert not ok
    assert any("HookPoint" in e for e in errors)


def test_validate_manifest_requires_at_least_one_scope():
    m = _manifest("a", scope=frozenset())
    ok, errors = validate_manifest(m)
    assert not ok
    assert any("HookRuntimeScope" in e for e in errors)


def test_validate_manifest_rejects_unsupported_interface_version():
    m = _manifest("a", interface_version="9.9")
    ok, errors = validate_manifest(m)
    assert not ok
    assert any("interface_version" in e for e in errors)


def test_validate_manifest_rejects_empty_required_context_field():
    m = _manifest("a", required_context=(HookCapabilityRequirement(field=""),))
    ok, errors = validate_manifest(m)
    assert not ok


def test_validate_manifest_accepts_well_formed():
    ok, errors = validate_manifest(_manifest("a"))
    assert ok
    assert errors == []


# ── Lifecycle FSM ────────────────────────────────────────────────────────────────
def test_lifecycle_registers_then_activates():
    d = HookDescriptor(manifest=_manifest("a"))
    d = advance_hook(d, HookTrigger.REGISTER, clock=lambda: 1.0)
    assert d.state is HookLifecycleState.REGISTERED
    d = advance_hook(d, HookTrigger.ACTIVATE, clock=lambda: 1.0)
    assert d.state is HookLifecycleState.ACTIVE


def test_lifecycle_direct_retire_from_registered_and_active():
    d = HookDescriptor(manifest=_manifest("a"), state=HookLifecycleState.REGISTERED)
    retired = advance_hook(d, HookTrigger.RETIRE, clock=lambda: 1.0)
    assert retired.state is HookLifecycleState.RETIRED

    d2 = HookDescriptor(manifest=_manifest("a"), state=HookLifecycleState.ACTIVE)
    retired2 = advance_hook(d2, HookTrigger.RETIRE, clock=lambda: 1.0)
    assert retired2.state is HookLifecycleState.RETIRED


def test_lifecycle_rejects_illegal_transition():
    d = HookDescriptor(manifest=_manifest("a"), state=HookLifecycleState.DRAFT)
    with pytest.raises(FSMError):
        advance_hook(d, HookTrigger.ACTIVATE, clock=lambda: 1.0)


def test_no_second_execution_fsm_exists():
    """A hook never executes a multi-phase action -- only one lifecycle FSM,
    unlike Tool Runtime's dual-FSM shape."""
    assert HOOK_LIFECYCLE.states == frozenset(HookLifecycleState)
    import hooks.lifecycle as lifecycle_mod

    assert not hasattr(lifecycle_mod, "HookExecutionState")


# ── Registry ─────────────────────────────────────────────────────────────────────
def test_registry_register_and_get_descriptor():
    reg = HookRegistry(clock=lambda: 1.0)
    d = reg.register(_manifest("a"), _Verdict(HookOutcome.ALLOW, "internal.a"))
    assert d.state is HookLifecycleState.REGISTERED
    fetched = reg.get_descriptor("internal", "a", "1.0.0")
    assert fetched == d


def test_registry_rejects_duplicate_registration():
    reg = HookRegistry(clock=lambda: 1.0)
    reg.register(_manifest("a"), _Verdict(HookOutcome.ALLOW, "internal.a"))
    with pytest.raises(ConfigError):
        reg.register(_manifest("a"), _Verdict(HookOutcome.ALLOW, "internal.a"))


def test_registry_rejects_invalid_manifest():
    reg = HookRegistry(clock=lambda: 1.0)
    with pytest.raises(ConfigError):
        reg.register(_manifest("a", points=frozenset()), _Verdict(HookOutcome.ALLOW, "internal.a"))


def test_registry_get_hook_never_invokes_it():
    reg = HookRegistry(clock=lambda: 1.0)
    hook = _Verdict(HookOutcome.ALLOW, "internal.a")
    reg.register(_manifest("a"), hook)
    assert reg.get_hook("internal", "a", "1.0.0") is hook


def test_registry_get_hook_unknown_raises():
    reg = HookRegistry(clock=lambda: 1.0)
    with pytest.raises(ConfigError):
        reg.get_hook("internal", "missing", "1.0.0")


def test_registry_namespace_isolation():
    reg = HookRegistry(clock=lambda: 1.0)
    reg.register(_manifest("a", namespace="internal"), _Verdict(HookOutcome.ALLOW, "internal.a"))
    reg.register(_manifest("a", namespace="org.acme"), _Verdict(HookOutcome.ALLOW, "org.acme.a"))
    assert reg.get_descriptor("internal", "a", "1.0.0").manifest.namespace == "internal"
    assert reg.get_descriptor("org.acme", "a", "1.0.0").manifest.namespace == "org.acme"


def test_registry_version_resolution_prefers_active():
    reg = HookRegistry(clock=lambda: 1.0)
    reg.register(_manifest("a", version="1.0.0"), _Verdict(HookOutcome.ALLOW, "internal.a"))
    reg.register(_manifest("a", version="2.0.0"), _Verdict(HookOutcome.ALLOW, "internal.a"))
    reg.activate("internal", "a", "1.0.0")
    resolved = reg.get_descriptor("internal", "a")  # unpinned
    assert resolved.version == "1.0.0"  # only ACTIVE version wins over a newer REGISTERED one


def test_registry_deregister_removes_entry_and_hook():
    reg = HookRegistry(clock=lambda: 1.0)
    reg.register(_manifest("a"), _Verdict(HookOutcome.ALLOW, "internal.a"))
    reg.deregister("internal", "a", "1.0.0")
    with pytest.raises(ConfigError):
        reg.get_descriptor("internal", "a", "1.0.0")
    with pytest.raises(ConfigError):
        reg.get_hook("internal", "a", "1.0.0")


def test_registry_has_no_execution_or_evaluation_surface():
    for banned in ("execute", "invoke", "run", "evaluate"):
        assert not hasattr(HookRegistry, banned)


def test_registry_assigns_monotonic_sequence():
    reg = HookRegistry(clock=lambda: 1.0)
    d1 = reg.register(_manifest("a"), _Verdict(HookOutcome.ALLOW, "internal.a"))
    d2 = reg.register(_manifest("b"), _Verdict(HookOutcome.ALLOW, "internal.b"))
    assert d2.sequence == d1.sequence + 1


def test_list_for_filters_by_point_and_scope():
    reg = HookRegistry(clock=lambda: 1.0)
    reg.register(
        _manifest("a", points=frozenset({HookPoint.BEFORE_TOOL_CALL})),
        _Verdict(HookOutcome.ALLOW, "internal.a"),
    )
    reg.register(
        _manifest("b", points=frozenset({HookPoint.AFTER_TOOL_CALL})),
        _Verdict(HookOutcome.ALLOW, "internal.b"),
    )
    reg.activate("internal", "a", "1.0.0")
    reg.activate("internal", "b", "1.0.0")
    matched = reg.list_for(HookPoint.BEFORE_TOOL_CALL, HookRuntimeScope.TOOL)
    assert [d.qualified_name for d in matched] == ["internal.a"]


def test_list_for_matches_any_scope_declaration():
    reg = HookRegistry(clock=lambda: 1.0)
    reg.register(
        _manifest("a", scope=frozenset({HookRuntimeScope.ANY})),
        _Verdict(HookOutcome.ALLOW, "internal.a"),
    )
    reg.activate("internal", "a", "1.0.0")
    matched = reg.list_for(HookPoint.BEFORE_TOOL_CALL, HookRuntimeScope.SKILL)
    assert [d.qualified_name for d in matched] == ["internal.a"]


def test_list_for_excludes_non_active_by_default():
    reg = HookRegistry(clock=lambda: 1.0)
    reg.register(_manifest("a"), _Verdict(HookOutcome.ALLOW, "internal.a"))  # stays REGISTERED
    assert reg.list_for(HookPoint.BEFORE_TOOL_CALL, HookRuntimeScope.TOOL) == ()


# ── Ordering (priority desc, sequence asc) ─────────────────────────────────────
def test_ordered_hooks_sorts_by_priority_descending():
    reg = HookRegistry(clock=lambda: 1.0)
    reg.register(_manifest("low", priority=1), _Verdict(HookOutcome.ALLOW, "internal.low"))
    reg.activate("internal", "low", "1.0.0")
    reg.register(_manifest("high", priority=10), _Verdict(HookOutcome.ALLOW, "internal.high"))
    reg.activate("internal", "high", "1.0.0")
    ordered = reg.list_for(HookPoint.BEFORE_TOOL_CALL, HookRuntimeScope.TOOL)
    assert [d.qualified_name for d in ordered] == ["internal.high", "internal.low"]


def test_ordered_hooks_breaks_ties_by_registration_sequence():
    reg = HookRegistry(clock=lambda: 1.0)
    reg.register(_manifest("first", priority=5), _Verdict(HookOutcome.ALLOW, "internal.first"))
    reg.activate("internal", "first", "1.0.0")
    reg.register(_manifest("second", priority=5), _Verdict(HookOutcome.ALLOW, "internal.second"))
    reg.activate("internal", "second", "1.0.0")
    ordered = reg.list_for(HookPoint.BEFORE_TOOL_CALL, HookRuntimeScope.TOOL)
    assert [d.qualified_name for d in ordered] == ["internal.first", "internal.second"]


def test_ordered_hooks_is_a_pure_function():
    d1 = HookDescriptor(manifest=_manifest("a", priority=1), sequence=0)
    d2 = HookDescriptor(manifest=_manifest("b", priority=2), sequence=1)
    assert ordered_hooks([d1, d2]) == (d2, d1)
    assert ordered_hooks([d1, d2]) == (d2, d1)  # deterministic, repeatable


def test_category_never_affects_ordering():
    """HookCategory is descriptive only -- two hooks differing only in
    category, with identical priority/sequence, must sort identically."""
    d1 = HookDescriptor(
        manifest=_manifest("a", priority=5, category=HookCategory.AUDIT), sequence=0,
    )
    d2 = HookDescriptor(
        manifest=_manifest("b", priority=5, category=HookCategory.METRICS), sequence=1,
    )
    assert ordered_hooks([d2, d1]) == (d1, d2)  # sequence tie-break only, category ignored


# ── Filtering ────────────────────────────────────────────────────────────────────
def test_hook_filter_refines_by_tags():
    reg = HookRegistry(clock=lambda: 1.0)
    reg.register(
        _manifest("a", tags=frozenset({"security"})), _Verdict(HookOutcome.ALLOW, "internal.a")
    )
    reg.activate("internal", "a", "1.0.0")
    reg.register(
        _manifest("b", tags=frozenset({"logging"})), _Verdict(HookOutcome.ALLOW, "internal.b")
    )
    reg.activate("internal", "b", "1.0.0")
    hf = HookFilter(reg)
    matched = hf.select(
        HookPoint.BEFORE_TOOL_CALL, HookRuntimeScope.TOOL, tags=frozenset({"security"}),
    )
    assert [d.qualified_name for d in matched] == ["internal.a"]


def test_hook_filter_with_no_tags_returns_all_matching():
    reg = HookRegistry(clock=lambda: 1.0)
    reg.register(_manifest("a"), _Verdict(HookOutcome.ALLOW, "internal.a"))
    reg.activate("internal", "a", "1.0.0")
    hf = HookFilter(reg)
    assert len(hf.select(HookPoint.BEFORE_TOOL_CALL, HookRuntimeScope.TOOL)) == 1


# ── Execution Policy ─────────────────────────────────────────────────────────────
def test_default_policy_before_points_are_fail_closed():
    pol = default_policy_for(HookPoint.BEFORE_TOOL_CALL)
    assert pol.failure_mode is HookFailureMode.FAIL_CLOSED
    assert pol.isolation is True


def test_default_policy_after_points_are_fail_open():
    pol = default_policy_for(HookPoint.AFTER_TOOL_CALL)
    assert pol.failure_mode is HookFailureMode.FAIL_OPEN
    assert pol.isolation is True


def test_aggregate_deny_wins():
    verdicts = [
        HookVerdict(outcome=HookOutcome.ALLOW, hook="a"),
        HookVerdict(outcome=HookOutcome.DENY, hook="b"),
    ]
    assert aggregate_verdicts(verdicts, HookAggregationRule.DENY_WINS) is HookOutcome.DENY


def test_aggregate_modify_wins_over_allow():
    verdicts = [
        HookVerdict(outcome=HookOutcome.ALLOW, hook="a"),
        HookVerdict(outcome=HookOutcome.MODIFY, hook="b"),
    ]
    assert aggregate_verdicts(verdicts, HookAggregationRule.DENY_WINS) is HookOutcome.MODIFY


def test_aggregate_all_must_allow():
    verdicts = [
        HookVerdict(outcome=HookOutcome.ALLOW, hook="a"),
        HookVerdict(outcome=HookOutcome.MODIFY, hook="b"),
    ]
    assert aggregate_verdicts(verdicts, HookAggregationRule.ALL_MUST_ALLOW) is HookOutcome.DENY


def test_aggregate_empty_is_allow():
    assert aggregate_verdicts([], HookAggregationRule.DENY_WINS) is HookOutcome.ALLOW


# ── Engine (isolation, aggregation) ───────────────────────────────────────────────
def test_engine_isolation_one_failure_does_not_block_siblings():
    reg = HookRegistry(clock=lambda: 1.0)
    reg.register(_manifest("raiser", priority=10), _Raises())
    reg.activate("internal", "raiser", "1.0.0")
    reg.register(_manifest("ok", priority=5), _Verdict(HookOutcome.ALLOW, "internal.ok"))
    reg.activate("internal", "ok", "1.0.0")

    ordered = reg.list_for(HookPoint.BEFORE_TOOL_CALL, HookRuntimeScope.TOOL)
    hooks_map = reg.hooks_for(ordered)
    event = build_event(HookPoint.BEFORE_TOOL_CALL, HookRuntimeScope.TOOL, now=1.0)
    policy = default_policy_for(HookPoint.BEFORE_TOOL_CALL)
    result = HookEngine().run(ordered, hooks_map, event, policy, now=1.0)

    assert len(result.failures) == 1
    assert result.failures[0].hook == "internal.raiser"
    assert any(v.hook == "internal.ok" for v in result.verdicts)


def test_engine_fail_closed_produces_synthetic_deny():
    reg = HookRegistry(clock=lambda: 1.0)
    reg.register(_manifest("raiser"), _Raises())
    reg.activate("internal", "raiser", "1.0.0")
    ordered = reg.list_for(HookPoint.BEFORE_TOOL_CALL, HookRuntimeScope.TOOL)
    event = build_event(HookPoint.BEFORE_TOOL_CALL, HookRuntimeScope.TOOL, now=1.0)
    policy = default_policy_for(HookPoint.BEFORE_TOOL_CALL)
    result = HookEngine().run(ordered, reg.hooks_for(ordered), event, policy, now=1.0)
    assert result.aggregated_outcome is HookOutcome.DENY
    assert result.verdicts[0].outcome is HookOutcome.DENY


def test_engine_fail_open_excludes_failure_from_aggregation_but_still_records_it():
    reg = HookRegistry(clock=lambda: 1.0)
    reg.register(
        _manifest("raiser", points=frozenset({HookPoint.AFTER_TOOL_CALL})), _Raises()
    )
    reg.activate("internal", "raiser", "1.0.0")
    ordered = reg.list_for(HookPoint.AFTER_TOOL_CALL, HookRuntimeScope.TOOL)
    event = build_event(HookPoint.AFTER_TOOL_CALL, HookRuntimeScope.TOOL, now=1.0)
    policy = default_policy_for(HookPoint.AFTER_TOOL_CALL)
    result = HookEngine().run(ordered, reg.hooks_for(ordered), event, policy, now=1.0)
    assert result.verdicts == ()
    assert result.aggregated_outcome is HookOutcome.ALLOW
    assert len(result.failures) == 1  # never silently swallowed


def test_engine_never_executes_a_tool_or_skill():
    """The engine's only side effect is invoking each registered Hook.evaluate
    -- no method named execute/invoke/run_tool/run_skill exists."""
    for banned in ("execute_tool", "run_tool", "run_skill", "invoke_tool", "plan"):
        assert not hasattr(HookEngine, banned)


# ── HookRuntime facade (registration + run + replay + diagnostics) ────────────
def test_runtime_run_end_to_end():
    rt = _runtime()
    _registered_active(rt, _manifest("a", priority=1), _Verdict(HookOutcome.ALLOW, "internal.a"))
    event = rt.build_event(HookPoint.BEFORE_TOOL_CALL, HookRuntimeScope.TOOL)
    result = rt.run(HookPoint.BEFORE_TOOL_CALL, HookRuntimeScope.TOOL, event)
    assert result.aggregated_outcome is HookOutcome.ALLOW


def test_runtime_run_uses_registry_hooks_by_default():
    rt = _runtime()
    _registered_active(rt, _manifest("a"), _Verdict(HookOutcome.DENY, "internal.a"))
    event = rt.build_event(HookPoint.BEFORE_TOOL_CALL, HookRuntimeScope.TOOL)
    result = rt.run(HookPoint.BEFORE_TOOL_CALL, HookRuntimeScope.TOOL, event)
    assert result.aggregated_outcome is HookOutcome.DENY


def test_runtime_run_accepts_explicit_hooks_override():
    rt = _runtime()
    _registered_active(rt, _manifest("a"), _Verdict(HookOutcome.DENY, "internal.a"))
    event = rt.build_event(HookPoint.BEFORE_TOOL_CALL, HookRuntimeScope.TOOL)
    override = {"internal.a": _Verdict(HookOutcome.ALLOW, "internal.a")}
    result = rt.run(HookPoint.BEFORE_TOOL_CALL, HookRuntimeScope.TOOL, event, hooks=override)
    assert result.aggregated_outcome is HookOutcome.ALLOW


def test_runtime_registrations_returns_registration_log():
    rt = _runtime()
    _registered_active(rt, _manifest("a"), _Verdict(HookOutcome.ALLOW, "internal.a"))
    regs = rt.registrations()
    assert len(regs) == 1
    assert regs[0].qualified_name == "internal.a"


def test_runtime_emits_events_when_publisher_injected():
    published: list[tuple] = []

    def publish(event_type, payload, **kw):
        published.append((event_type, payload))

    rt = _runtime(publish=publish)
    _registered_active(rt, _manifest("a"), _Verdict(HookOutcome.ALLOW, "internal.a"))
    event = rt.build_event(HookPoint.BEFORE_TOOL_CALL, HookRuntimeScope.TOOL)
    rt.run(HookPoint.BEFORE_TOOL_CALL, HookRuntimeScope.TOOL, event)
    kinds = {e.value for e, _ in published}
    assert "hook.run.completed" in kinds


def test_runtime_has_no_tool_or_skill_planning_surface():
    for banned in ("execute", "plan", "create_plan", "invoke_tool", "invoke_skill"):
        assert not hasattr(HookRuntime, banned)


# ── Replay ─────────────────────────────────────────────────────────────────────
def test_replay_record_round_trips_and_validates():
    rt = _runtime()
    _registered_active(rt, _manifest("a"), _Verdict(HookOutcome.ALLOW, "internal.a"))
    event = rt.build_event(HookPoint.BEFORE_TOOL_CALL, HookRuntimeScope.TOOL)
    result = rt.run(HookPoint.BEFORE_TOOL_CALL, HookRuntimeScope.TOOL, event)
    record = rt.record_replay(event, result)
    comparison = rt.validate_replay(record.replay_id)
    assert comparison.matches
    assert comparison.mismatches == ()


def test_replay_validation_detects_tampering():
    event = build_event(HookPoint.BEFORE_TOOL_CALL, HookRuntimeScope.TOOL, now=1.0)
    from hooks.domain import HookRunResult

    result = HookRunResult(
        point=HookPoint.BEFORE_TOOL_CALL, scope=HookRuntimeScope.TOOL,
        aggregated_outcome=HookOutcome.ALLOW, aggregation="deny_wins", created_at=1.0,
    )
    record = hook_replay_record_for(event, result, now=1.0)
    tampered = record.__class__(
        replay_id=record.replay_id,
        event_document=record.event_document,
        result_document={**record.result_document, "aggregated_outcome": "deny"},
        replay_hash=record.replay_hash,
    )
    comparison = HookReplayEngine().validate(tampered)
    assert not comparison.matches
    assert comparison.mismatches == ("replay_hash",)


def test_replay_compare_detects_divergence():
    event = build_event(HookPoint.BEFORE_TOOL_CALL, HookRuntimeScope.TOOL, now=1.0)
    from hooks.domain import HookRunResult

    result_a = HookRunResult(
        point=HookPoint.BEFORE_TOOL_CALL, scope=HookRuntimeScope.TOOL, created_at=1.0,
    )
    result_b = HookRunResult(
        point=HookPoint.BEFORE_TOOL_CALL, scope=HookRuntimeScope.TOOL,
        aggregated_outcome=HookOutcome.DENY, created_at=1.0,
    )
    record_a = hook_replay_record_for(event, result_a, now=1.0)
    record_b = hook_replay_record_for(event, result_b, now=1.0)
    engine = HookReplayEngine()
    assert engine.compare(record_a, record_a).matches
    assert not engine.compare(record_a, record_b).matches


def test_runtime_validate_replay_raises_for_unknown_id():
    rt = _runtime()
    with pytest.raises(ConfigError):
        rt.validate_replay("nope")


def test_runtime_compare_replays_raises_for_unknown_id():
    rt = _runtime()
    _registered_active(rt, _manifest("a"), _Verdict(HookOutcome.ALLOW, "internal.a"))
    event = rt.build_event(HookPoint.BEFORE_TOOL_CALL, HookRuntimeScope.TOOL)
    result = rt.run(HookPoint.BEFORE_TOOL_CALL, HookRuntimeScope.TOOL, event)
    record = rt.record_replay(event, result)
    with pytest.raises(ConfigError):
        rt.compare_replays(record.replay_id, "nope")


# ── Observability (planning-free: run traces only) ─────────────────────────────
def test_trace_for_projects_a_run_result():
    from hooks.observability import trace_for

    rt = _runtime()
    _registered_active(rt, _manifest("a"), _Verdict(HookOutcome.ALLOW, "internal.a"))
    event = rt.build_event(HookPoint.BEFORE_TOOL_CALL, HookRuntimeScope.TOOL)
    result = rt.run(HookPoint.BEFORE_TOOL_CALL, HookRuntimeScope.TOOL, event)
    trace = trace_for(result, event, now=5.0)
    assert trace.event_id == event.event_id
    assert trace.hook_count == len(result.verdicts)
    assert trace.aggregated_outcome == result.aggregated_outcome.value
    assert trace.created_at == 5.0


def test_in_memory_observability_sink_is_the_bundle_interface_and_records():
    from hooks.extensions import HookObservabilitySink
    from hooks.observability import InMemoryHookObservabilitySink

    sink = InMemoryHookObservabilitySink()
    assert isinstance(sink, HookObservabilitySink)
    sink.record({"phase": "run_completed"})
    assert sink.count() == 1
    assert sink.all() == ({"phase": "run_completed"},)


@pytest.mark.asyncio
async def test_runtime_run_persists_a_trace_in_the_temporary_repository():
    rt = _runtime()
    _registered_active(rt, _manifest("a"), _Verdict(HookOutcome.ALLOW, "internal.a"))
    event = rt.build_event(HookPoint.BEFORE_TOOL_CALL, HookRuntimeScope.TOOL)
    rt.run(HookPoint.BEFORE_TOOL_CALL, HookRuntimeScope.TOOL, event)
    assert await rt._repo.trace_count() == 1


# ── Diagnostics + statistics ────────────────────────────────────────────────────
def test_diagnostics_reports_registry_state():
    rt = _runtime()
    _registered_active(rt, _manifest("a"), _Verdict(HookOutcome.ALLOW, "internal.a"))
    diag = rt.diagnostics()
    assert diag.registry_stats["count"] == 1
    assert diag.health_summary == "healthy"


def test_statistics_aggregates_results():
    from hooks.domain import HookRunResult

    results = [
        HookRunResult(
            point=HookPoint.BEFORE_TOOL_CALL, scope=HookRuntimeScope.TOOL,
            aggregated_outcome=HookOutcome.ALLOW,
        ),
        HookRunResult(
            point=HookPoint.AFTER_TOOL_CALL, scope=HookRuntimeScope.TOOL,
            aggregated_outcome=HookOutcome.DENY,
        ),
    ]
    stats = compute_statistics(results)
    assert stats.run_count == 2
    assert stats.per_outcome == {"allow": 1, "deny": 1}


def test_statistics_of_empty_sequence_is_zeroed():
    stats = compute_statistics([])
    assert stats.run_count == 0
    assert stats.per_point == {}


# ── Serialization round-trips ──────────────────────────────────────────────────
def test_manifest_serialization_round_trips():
    m = _manifest("a", priority=7, category=HookCategory.AUDIT, tags=frozenset({"x"}))
    s = HookSerializer()
    assert s.manifest_from_dict(s.manifest_to_dict(m)) == m


def test_descriptor_serialization_round_trips():
    d = HookDescriptor(manifest=_manifest("a"), state=HookLifecycleState.ACTIVE, sequence=3)
    s = HookSerializer()
    assert s.descriptor_from_document(s.descriptor_to_document(d)) == d


def test_event_serialization_round_trips():
    e = build_event(HookPoint.BEFORE_TOOL_CALL, HookRuntimeScope.TOOL, session_id="s", now=1.0)
    s = HookSerializer()
    assert s.event_from_dict(s.event_to_dict(e)) == e


def test_run_result_serialization_round_trips():
    rt = _runtime()
    _registered_active(rt, _manifest("a"), _Verdict(HookOutcome.ALLOW, "internal.a"))
    event = rt.build_event(HookPoint.BEFORE_TOOL_CALL, HookRuntimeScope.TOOL)
    result = rt.run(HookPoint.BEFORE_TOOL_CALL, HookRuntimeScope.TOOL, event)
    s = HookSerializer()
    assert s.run_result_from_dict(s.run_result_to_dict(result)) == result


def test_replay_record_serialization_round_trips():
    event = build_event(HookPoint.BEFORE_TOOL_CALL, HookRuntimeScope.TOOL, now=1.0)
    from hooks.domain import HookRunResult

    result = HookRunResult(
        point=HookPoint.BEFORE_TOOL_CALL, scope=HookRuntimeScope.TOOL, created_at=1.0,
    )
    record = hook_replay_record_for(event, result, now=1.0)
    s = HookSerializer()
    assert s.replay_record_from_document(s.replay_record_to_document(record)) == record


# ── Repository (temporary storage) ─────────────────────────────────────────────
@pytest.mark.asyncio
async def test_repository_stores_registrations_replays_and_traces():
    store = InMemoryDocumentStore()
    serializer = HookSerializer()
    repo = HookRepository(store, serializer)

    d = HookDescriptor(manifest=_manifest("a"), state=HookLifecycleState.ACTIVE, sequence=0)
    await repo.add_registration(d)
    assert await repo.registration_count() == 1

    event = build_event(HookPoint.BEFORE_TOOL_CALL, HookRuntimeScope.TOOL, now=1.0)
    from hooks.domain import HookRunResult

    result = HookRunResult(
        point=HookPoint.BEFORE_TOOL_CALL, scope=HookRuntimeScope.TOOL, created_at=1.0,
    )
    record = hook_replay_record_for(event, result, now=1.0)
    await repo.add_replay(record)
    assert await repo.replay_count() == 1
    fetched = await repo.get_replay(record.replay_id)
    assert fetched == record


# ── Configuration ────────────────────────────────────────────────────────────────
def test_configuration_rejects_non_positive_ttl():
    with pytest.raises(ConfigError):
        HookConfiguration(replay_ttl_s=0)


def test_configuration_rejects_empty_namespace():
    with pytest.raises(ConfigError):
        HookConfiguration(default_namespace="")


def test_configuration_fingerprint_is_deterministic():
    a = HookConfiguration.default()
    b = HookConfiguration.default()
    assert a.fingerprint() == b.fingerprint()


def test_factory_rejects_unknown_store_kind():
    from hooks.config import StoreConfig

    with pytest.raises(ConfigError):
        HookFactory.create(HookConfiguration(document_store=StoreConfig(kind="s3")))


# ── Determinism ────────────────────────────────────────────────────────────────
def test_ids_are_deterministic_and_content_derived():
    assert content_hash("a", "b") == content_hash("a", "b")
    assert content_hash("a", "b") != content_hash("b", "a")
    same = descriptor_id_for("internal", "a", "1.0.0")
    assert same == descriptor_id_for("internal", "a", "1.0.0")


def test_run_is_deterministic_given_identical_registration_order():
    reg1 = HookRegistry(clock=lambda: 1.0)
    reg2 = HookRegistry(clock=lambda: 1.0)
    for reg in (reg1, reg2):
        reg.register(_manifest("a", priority=1), _Verdict(HookOutcome.ALLOW, "internal.a"))
        reg.activate("internal", "a", "1.0.0")
        reg.register(_manifest("b", priority=5), _Verdict(HookOutcome.ALLOW, "internal.b"))
        reg.activate("internal", "b", "1.0.0")

    ordered1 = reg1.list_for(HookPoint.BEFORE_TOOL_CALL, HookRuntimeScope.TOOL)
    ordered2 = reg2.list_for(HookPoint.BEFORE_TOOL_CALL, HookRuntimeScope.TOOL)
    assert [d.qualified_name for d in ordered1] == [d.qualified_name for d in ordered2]


# ── "Never executes / never plans / never mutates state" surface constraints ──
def test_hook_capability_requirement_is_never_resolved():
    """Structural only -- no module in hooks/ calls anything resembling
    capability resolution over `required_context`."""
    src_dir = pathlib.Path("hooks")
    for p in src_dir.rglob("*.py"):
        text = p.read_text()
        assert "resolve_capability" not in text
        assert "CapabilityResolver" not in text


def _references_category(path: str) -> bool:
    """AST-level check (not a docstring/comment substring match): does this
    module contain any actual `.category` attribute access or `HookCategory`
    name reference in its code?"""
    tree = ast.parse(pathlib.Path(path).read_text())
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and node.attr == "category":
            return True
        if isinstance(node, ast.Name) and node.id == "HookCategory":
            return True
    return False


def test_hook_ordering_module_never_reads_category():
    assert not _references_category("hooks/ordering.py")


def test_hook_policy_module_never_reads_category():
    assert not _references_category("hooks/policy.py")


def test_hook_engine_module_never_reads_category():
    assert not _references_category("hooks/engine.py")


# ── Dependency rules (hooks stays acyclic; no ownership leakage) ──────────────
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


def test_hooks_dependency_rules():
    # hooks depends only on the foundation (core) plus its own package; never
    # Tool Runtime, Skill Runtime, Workflow, Agent, or any sibling runtime.
    forbidden = {
        "runtime", "api", "gateway", "replay", "events", "memory", "knowledge",
        "context", "prompt", "reasoning", "provider", "bridge", "pipeline",
        "voicebot", "observability", "tenant", "tool", "skill", "workflow", "agent",
    }
    assert not (_imports("hooks") & forbidden), _imports("hooks")


def test_hooks_imports_only_core_and_own_package():
    allowed_extra = {
        "core", "__future__", "abc", "collections", "dataclasses", "enum", "typing", "hashlib",
        "copy", "time",
    }
    non_stdlib_like = {t for t in _imports("hooks") if t not in allowed_extra}
    assert not non_stdlib_like, non_stdlib_like
