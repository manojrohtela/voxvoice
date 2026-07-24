"""Skill Runtime Planning (M6.2B-2) tests.

Verifies deterministic planning (topological dependency ordering, diamond
dependency dedup, plan-hash determinism), the immutable `SkillPlan`/
`SkillExecutionContext` shapes, the default permissive policy + a denying
policy, planning observability (trace recording), planning-replay recording/
validation/tamper-detection, serialization round-trips, diagnostics,
statistics, the "never resolves capabilities / never executes" surface
constraint, and the `skill -> core[, tenant]` dependency boundary extended to
this subpackage (in particular: zero import of Tool Runtime).

Bundle 1 (`skill/domain.py`, `skill/registry.py`, `skill/runtime.py`,
`skill/lifecycle.py`, ...) and Tool Runtime are untouched by this milestone.
"""

from __future__ import annotations

import ast
import inspect
import pathlib
from dataclasses import FrozenInstanceError

import pytest

from core.errors import ConfigError
from skill.domain import CapabilityKind, SkillCapabilityRequirement, SkillDependency, SkillManifest
from skill.extensions import CapabilityResolver, SkillPolicyEngine
from skill.planning import (
    AllowAllSkillPolicy,
    InMemoryPlanningObservabilitySink,
    PlanningConfiguration,
    PlanningFactory,
    PlanningRepository,
    PlanningSerializer,
    PlanReplayEngine,
    SkillExecutionContext,
    SkillExecutionPlanner,
    SkillPlanningRuntime,
    SkillPolicyVerdict,
    build_execution_context,
    build_plan,
    compute_statistics,
    context_id_for,
    plan_id_for,
    plan_replay_record_for,
    planning_trace_for,
)
from skill.registry import SkillRegistry
from skill.stores import InMemoryDocumentStore


def _manifest(id_: str, *, deps: tuple[SkillDependency, ...] = (), **kw) -> SkillManifest:
    base = dict(id=id_, namespace="internal", version="1.0.0", dependencies=deps)
    base.update(kw)
    return SkillManifest(**base)


def _registry_with_chain() -> SkillRegistry:
    """a <- b <- c (c depends on b, b depends on a)."""
    reg = SkillRegistry(clock=lambda: 1.0)
    reg.register(_manifest("a"))
    reg.activate("internal", "a", "1.0.0")
    reg.register(_manifest("b", deps=(SkillDependency(namespace="internal", id="a"),)))
    reg.activate("internal", "b", "1.0.0")
    reg.register(_manifest("c", deps=(SkillDependency(namespace="internal", id="b"),)))
    reg.activate("internal", "c", "1.0.0")
    return reg


def _runtime(registry: SkillRegistry, **kwargs) -> SkillPlanningRuntime:
    kwargs.setdefault("clock", lambda: 100.0)
    return PlanningFactory.default(registry, **kwargs)


# ── SkillExecutionPlanner: deterministic topological ordering ─────────────────
def test_planner_orders_linear_chain_leaf_first():
    reg = _registry_with_chain()
    plan = build_plan(reg, "internal", "c")
    assert [s.qualified_name for s in plan.steps] == ["internal.a", "internal.b", "internal.c"]
    assert plan.steps[-1].qualified_name == "internal.c"  # root always last


def test_planner_dedups_diamond_dependency():
    # d depends on b and c; b and c both depend on a -> a must appear once.
    reg = SkillRegistry(clock=lambda: 1.0)
    reg.register(_manifest("a"))
    reg.activate("internal", "a", "1.0.0")
    reg.register(_manifest("b", deps=(SkillDependency(namespace="internal", id="a"),)))
    reg.activate("internal", "b", "1.0.0")
    reg.register(_manifest("c", deps=(SkillDependency(namespace="internal", id="a"),)))
    reg.activate("internal", "c", "1.0.0")
    reg.register(
        _manifest(
            "d",
            deps=(
                SkillDependency(namespace="internal", id="b"),
                SkillDependency(namespace="internal", id="c"),
            ),
        )
    )
    reg.activate("internal", "d", "1.0.0")

    plan = build_plan(reg, "internal", "d")
    names = [s.qualified_name for s in plan.steps]
    assert names.count("internal.a") == 1
    assert names.index("internal.a") < names.index("internal.b")
    assert names.index("internal.a") < names.index("internal.c")
    assert names[-1] == "internal.d"


def test_planner_with_no_dependencies_yields_single_step_plan():
    reg = SkillRegistry(clock=lambda: 1.0)
    reg.register(_manifest("solo"))
    reg.activate("internal", "solo", "1.0.0")
    plan = build_plan(reg, "internal", "solo")
    assert [s.qualified_name for s in plan.steps] == ["internal.solo"]


def test_planner_copies_declared_capability_requirements_without_resolving():
    reg = SkillRegistry(clock=lambda: 1.0)
    req = SkillCapabilityRequirement(kind=CapabilityKind.TOOL, tag="calendar.create_event")
    reg.register(_manifest("cal", required_capabilities=frozenset({req})))
    reg.activate("internal", "cal", "1.0.0")
    plan = build_plan(reg, "internal", "cal")
    assert plan.capability_requirements == (req,)


def test_planning_is_deterministic_given_identical_registry_state():
    reg1 = _registry_with_chain()
    reg2 = _registry_with_chain()
    plan1 = build_plan(reg1, "internal", "c", now=5.0)
    plan2 = build_plan(reg2, "internal", "c", now=5.0)
    assert plan1 == plan2
    assert plan1.plan_id == plan2.plan_id
    assert plan1.dependency_hash == plan2.dependency_hash


def test_plan_id_changes_when_dependency_order_changes():
    reg = _registry_with_chain()
    plan_c = build_plan(reg, "internal", "c")
    plan_b = build_plan(reg, "internal", "b")
    assert plan_c.plan_id != plan_b.plan_id


def test_planner_class_delegates_to_build_plan():
    reg = _registry_with_chain()
    via_class = SkillExecutionPlanner().plan(reg, "internal", "c", now=9.0)
    via_fn = build_plan(reg, "internal", "c", now=9.0)
    assert via_class == via_fn


# ── SkillPlan / SkillExecutionContext immutability ─────────────────────────────
def test_plan_is_immutable():
    reg = _registry_with_chain()
    plan = build_plan(reg, "internal", "a")
    with pytest.raises(FrozenInstanceError):
        plan.plan_id = "other"  # type: ignore[misc]


def test_execution_context_is_immutable_and_carries_declared_capabilities():
    reg = SkillRegistry(clock=lambda: 1.0)
    req = SkillCapabilityRequirement(kind=CapabilityKind.MEMORY, tag="memory.recall")
    reg.register(_manifest("m", required_capabilities=frozenset({req})))
    reg.activate("internal", "m", "1.0.0")
    plan = build_plan(reg, "internal", "m")
    context = build_execution_context(plan, requested_by="tester", now=42.0)
    assert context.capability_requirements == (req,)
    assert context.root == "internal.m@1.0.0"
    with pytest.raises(FrozenInstanceError):
        context.plan_id = "other"  # type: ignore[misc]


def test_context_id_is_deterministic():
    assert context_id_for("p1", "user", 1.0) == context_id_for("p1", "user", 1.0)
    assert context_id_for("p1", "user", 1.0) != context_id_for("p1", "other", 1.0)


def test_plan_id_helper_is_content_derived():
    same = plan_id_for("internal.a", "1.0.0", "hash1")
    assert same == plan_id_for("internal.a", "1.0.0", "hash1")
    assert same != plan_id_for("internal.a", "1.0.0", "hash2")


# ── Policy model ───────────────────────────────────────────────────────────────
def test_default_policy_always_allows():
    reg = _registry_with_chain()
    descriptor = reg.get_descriptor("internal", "a")
    assert AllowAllSkillPolicy().authorize(descriptor) is SkillPolicyVerdict.ALLOW


def test_policy_engine_is_the_bundle1_interface():
    assert issubclass(AllowAllSkillPolicy, SkillPolicyEngine)


class _DenyAllPolicy(SkillPolicyEngine):
    def authorize(self, descriptor):
        return SkillPolicyVerdict.DENY


def test_runtime_create_plan_honors_injected_denying_policy():
    reg = _registry_with_chain()
    rt = _runtime(reg, policy_engine=_DenyAllPolicy())
    with pytest.raises(ConfigError):
        rt.create_plan("internal", "c")


def test_runtime_create_plan_succeeds_with_default_policy():
    reg = _registry_with_chain()
    rt = _runtime(reg)
    plan = rt.create_plan("internal", "c")
    assert plan.qualified_name == "internal.c"


# ── Observability (planning traces only) ───────────────────────────────────────
def test_planning_trace_projection_reflects_the_plan():
    reg = _registry_with_chain()
    plan = build_plan(reg, "internal", "c")
    trace = planning_trace_for(plan, now=7.0)
    assert trace.plan_id == plan.plan_id
    assert trace.step_count == len(plan.steps)
    assert trace.created_at == 7.0


def test_observability_sink_is_the_bundle1_interface_and_records():
    from skill.extensions import SkillObservabilitySink

    sink = InMemoryPlanningObservabilitySink()
    assert isinstance(sink, SkillObservabilitySink)
    sink.record({"phase": "plan_created"})
    assert sink.count() == 1
    assert sink.all() == ({"phase": "plan_created"},)


def test_runtime_create_plan_records_a_planning_trace():
    reg = _registry_with_chain()
    rt = _runtime(reg)
    rt.create_plan("internal", "c")
    diag = rt.diagnostics()
    assert diag.planning_stats["plans"] == 1


def test_runtime_emits_events_when_publisher_injected():
    reg = _registry_with_chain()
    published: list[tuple] = []

    def publish(event_type, payload, **kw):
        published.append((event_type, payload))

    rt = _runtime(reg, publish=publish)
    rt.create_plan("internal", "c")
    kinds = {e.value for e, _ in published}
    assert "skill.plan.created" in kinds
    assert "skill.policy.evaluated" in kinds


# ── Replay ─────────────────────────────────────────────────────────────────────
def test_replay_record_round_trips_and_validates():
    reg = _registry_with_chain()
    rt = _runtime(reg)
    plan = rt.create_plan("internal", "c")
    context = rt.create_context(plan, requested_by="tester")
    record = rt.record_replay(plan, context)
    comparison = rt.validate_replay(record.replay_id)
    assert comparison.matches
    assert comparison.mismatches == ()


def test_replay_validation_detects_tampering():
    reg = _registry_with_chain()
    plan = build_plan(reg, "internal", "c", now=1.0)
    context = build_execution_context(plan, now=1.0)
    record = plan_replay_record_for(plan, context, now=1.0)

    tampered = record.__class__(
        replay_id=record.replay_id,
        plan_document={**record.plan_document, "steps": []},
        context_document=record.context_document,
        replay_hash=record.replay_hash,
    )
    comparison = PlanReplayEngine().validate(tampered)
    assert not comparison.matches
    assert comparison.mismatches == ("replay_hash",)


def test_replay_compare_matches_identical_records_and_detects_divergence():
    reg = _registry_with_chain()
    plan = build_plan(reg, "internal", "c", now=1.0)
    context = build_execution_context(plan, now=1.0)
    record_a = plan_replay_record_for(plan, context, now=1.0)
    record_b = plan_replay_record_for(plan, context, now=1.0)
    engine = PlanReplayEngine()
    assert engine.compare(record_a, record_b).matches

    other_plan = build_plan(reg, "internal", "b", now=1.0)
    other_context = build_execution_context(other_plan, now=1.0)
    record_c = plan_replay_record_for(other_plan, other_context, now=1.0)
    assert not engine.compare(record_a, record_c).matches


def test_runtime_validate_replay_raises_for_unknown_id():
    reg = _registry_with_chain()
    rt = _runtime(reg)
    with pytest.raises(ConfigError):
        rt.validate_replay("nope")


def test_runtime_compare_replays_raises_for_unknown_id():
    reg = _registry_with_chain()
    rt = _runtime(reg)
    plan = rt.create_plan("internal", "c")
    context = rt.create_context(plan)
    record = rt.record_replay(plan, context)
    with pytest.raises(ConfigError):
        rt.compare_replays(record.replay_id, "nope")


# ── Serialization round-trips ──────────────────────────────────────────────────
def test_plan_serialization_round_trips():
    reg = _registry_with_chain()
    plan = build_plan(reg, "internal", "c", now=3.0)
    s = PlanningSerializer()
    assert s.plan_from_dict(s.plan_to_dict(plan)) == plan


def test_context_serialization_round_trips():
    reg = _registry_with_chain()
    plan = build_plan(reg, "internal", "c")
    context = build_execution_context(plan, org_id="org1", requested_by="tester", now=4.0)
    s = PlanningSerializer()
    assert s.context_from_dict(s.context_to_dict(context)) == context


def test_replay_record_serialization_round_trips():
    reg = _registry_with_chain()
    plan = build_plan(reg, "internal", "c")
    context = build_execution_context(plan)
    record = plan_replay_record_for(plan, context)
    s = PlanningSerializer()
    assert s.replay_record_from_document(s.replay_record_to_document(record)) == record


# ── Repository (temporary storage) ─────────────────────────────────────────────
@pytest.mark.asyncio
async def test_repository_stores_and_counts_replays_and_traces():
    store = InMemoryDocumentStore()
    serializer = PlanningSerializer()
    repo = PlanningRepository(store, serializer)

    reg = _registry_with_chain()
    plan = build_plan(reg, "internal", "c")
    context = build_execution_context(plan)
    record = plan_replay_record_for(plan, context)
    await repo.add_replay(record)
    assert await repo.replay_count() == 1
    fetched = await repo.get_replay(record.replay_id)
    assert fetched == record

    trace = planning_trace_for(plan)
    await repo.add_trace(trace)
    assert await repo.trace_count() == 1


# ── Diagnostics + statistics ────────────────────────────────────────────────────
def test_diagnostics_reports_registry_and_planning_counts():
    reg = _registry_with_chain()
    rt = _runtime(reg)
    rt.create_plan("internal", "a")
    rt.create_plan("internal", "b")
    diag = rt.diagnostics()
    assert diag.planning_stats["plans"] == 2
    assert set(diag.registry_stats["skills"]) == {"internal.a", "internal.b", "internal.c"}
    assert diag.health_summary == "healthy"


def test_statistics_aggregates_plans():
    reg = _registry_with_chain()
    plan_a = build_plan(reg, "internal", "a")
    plan_c = build_plan(reg, "internal", "c")
    stats = compute_statistics([plan_a, plan_c])
    assert stats.plan_count == 2
    assert stats.per_root == {"internal.a": 1, "internal.c": 1}
    assert stats.avg_steps == pytest.approx((1 + 3) / 2)


def test_statistics_of_empty_sequence_is_zeroed():
    stats = compute_statistics([])
    assert stats.plan_count == 0
    assert stats.avg_steps == 0.0
    assert stats.avg_capabilities == 0.0


# ── Configuration ────────────────────────────────────────────────────────────────
def test_configuration_rejects_non_positive_ttl():
    with pytest.raises(ConfigError):
        PlanningConfiguration(replay_ttl_s=0)


def test_configuration_fingerprint_is_deterministic():
    a = PlanningConfiguration.default()
    b = PlanningConfiguration.default()
    assert a.fingerprint() == b.fingerprint()


def test_factory_rejects_unknown_store_kind():
    from skill.config import StoreConfig

    reg = _registry_with_chain()
    with pytest.raises(ConfigError):
        PlanningFactory.create(
            PlanningConfiguration(document_store=StoreConfig(kind="s3")), reg,
        )


# ── "Never executes / never resolves capabilities" surface constraints ────────
def test_capability_resolver_remains_unimplemented_interface():
    assert inspect.isabstract(CapabilityResolver)
    with pytest.raises(TypeError):
        CapabilityResolver()  # type: ignore[abstract]


def test_planning_package_never_instantiates_a_capability_resolver():
    src_dir = pathlib.Path("skill/planning")
    for p in src_dir.rglob("*.py"):
        assert "CapabilityResolver(" not in p.read_text()
        assert "can_satisfy" not in p.read_text()


def test_skill_planning_runtime_has_no_execution_surface():
    for banned in ("execute", "invoke", "run", "run_skill", "run_tool"):
        assert not hasattr(SkillPlanningRuntime, banned)


def test_skill_execution_planner_has_no_execution_surface():
    for banned in ("execute", "invoke", "run"):
        assert not hasattr(SkillExecutionPlanner, banned)


def test_execution_context_is_pure_data_with_no_methods():
    behavioral = [
        name for name, value in vars(SkillExecutionContext).items()
        if callable(value) and not name.startswith("__")
    ]
    assert behavioral == []


# ── Dependency boundary (extended to skill/planning) ───────────────────────────
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


def test_skill_planning_dependency_rules():
    forbidden = {
        "runtime", "api", "gateway", "replay", "events", "memory", "knowledge",
        "context", "prompt", "reasoning", "provider", "bridge", "pipeline",
        "voicebot", "observability", "tenant", "tool",
    }
    assert not (_imports("skill/planning") & forbidden), _imports("skill/planning")


def test_skill_planning_imports_only_core_and_own_package():
    allowed = {"core", "skill"}
    non_stdlib_like = {
        t for t in _imports("skill/planning")
        if t not in allowed and t not in {
            "__future__", "abc", "collections", "dataclasses", "enum", "typing",
            "hashlib", "inspect", "time",
        }
    }
    assert not non_stdlib_like, non_stdlib_like
