"""Workflow Runtime Core (M6.4B-1) tests.

Verifies manifest validation (including a real, primary cycle-detection
algorithm — not defense-in-depth), descriptor validation, the registry
(registration/discovery/namespacing/version-resolution/lifecycle — no
execution), deterministic graph hashing, serialization round-trips, the
temporary registration repository, determinism, and the `workflow → core`
dependency rule (zero import of Tool Runtime, Skill Runtime, or Hook
Framework, per this bundle's explicit constraint). Execution, checkpoints,
recovery, replay, and observability are non-goals here (Bundle 2).
"""

from __future__ import annotations

import ast
import pathlib
from dataclasses import FrozenInstanceError

import pytest

from core.errors import ConfigError
from core.fsm import FSMError
from core.policies import RetryPolicy, TimeoutPolicy
from workflow import (
    WorkflowCondition,
    WorkflowConditionOperator,
    WorkflowConfiguration,
    WorkflowDescriptor,
    WorkflowEdge,
    WorkflowFactory,
    WorkflowGraph,
    WorkflowLifecycleState,
    WorkflowManifest,
    WorkflowNode,
    WorkflowNodeKind,
    WorkflowRegistry,
    WorkflowRepository,
    WorkflowRuntime,
    WorkflowSerializer,
    WorkflowTrigger,
    advance_workflow,
    compute_graph_hash,
    content_hash,
    descriptor_id_for,
    topological_order,
    validate_graph,
    validate_manifest,
)
from workflow.stores import InMemoryDocumentStore


def _linear_graph() -> WorkflowGraph:
    nodes = (
        WorkflowNode(
            node_id="a", kind=WorkflowNodeKind.TOOL, tool_ref=("internal", "t", "1.0.0"),
        ),
        WorkflowNode(
            node_id="b", kind=WorkflowNodeKind.SKILL, skill_ref=("internal", "s", "1.0.0"),
        ),
        WorkflowNode(node_id="c", kind=WorkflowNodeKind.EVENT, event_name="done"),
    )
    edges = (WorkflowEdge(source="a", target="b"), WorkflowEdge(source="b", target="c"))
    return WorkflowGraph(entry_node="a", nodes=nodes, edges=edges)


def _manifest(id_: str = "wf", **kw) -> WorkflowManifest:
    base = dict(id=id_, namespace="internal", version="1.0.0", graph=_linear_graph())
    base.update(kw)
    return WorkflowManifest(**base)


def _runtime(**kwargs) -> WorkflowRuntime:
    kwargs.setdefault("clock", lambda: 100.0)
    return WorkflowFactory.default(**kwargs)


def _registered_active(rt: WorkflowRuntime, manifest: WorkflowManifest) -> WorkflowDescriptor:
    d = rt.register(manifest)
    rt.activate(manifest.namespace, manifest.id, manifest.version)
    return d


# ── WorkflowManifest / WorkflowDescriptor ──────────────────────────────────────
def test_manifest_is_immutable():
    m = _manifest()
    with pytest.raises(FrozenInstanceError):
        m.id = "other"  # type: ignore[misc]


def test_descriptor_is_immutable():
    d = WorkflowDescriptor(manifest=_manifest())
    with pytest.raises(FrozenInstanceError):
        d.state = WorkflowLifecycleState.ACTIVE  # type: ignore[misc]


def test_graph_is_immutable():
    g = _linear_graph()
    with pytest.raises(FrozenInstanceError):
        g.entry_node = "b"  # type: ignore[misc]


def test_node_carries_opaque_retry_and_timeout_policy_unused():
    node = WorkflowNode(
        node_id="a", kind=WorkflowNodeKind.TOOL, tool_ref=("internal", "t", "1.0.0"),
        retry_policy=RetryPolicy(max_attempts=5), timeout_policy=TimeoutPolicy(seconds=30.0),
    )
    assert node.retry_policy.max_attempts == 5
    assert node.timeout_policy.seconds == 30.0


# ── Validation: manifest, interface_version ────────────────────────────────────
def test_validate_manifest_accepts_well_formed():
    ok, errors = validate_manifest(_manifest())
    assert ok
    assert errors == []


def test_validate_manifest_rejects_unsupported_interface_version():
    ok, errors = validate_manifest(_manifest(interface_version="9.9"))
    assert not ok
    assert any("interface_version" in e for e in errors)


def test_validate_manifest_rejects_empty_id():
    ok, errors = validate_manifest(_manifest(id=""))
    assert not ok


# ── Graph validation: duplicates, orphans, cycles ──────────────────────────────
def test_graph_requires_at_least_one_node():
    g = WorkflowGraph(entry_node="a", nodes=(), edges=())
    ok, errors = validate_graph(g)
    assert not ok
    assert any("at least one node" in e for e in errors)


def test_graph_rejects_unknown_entry_node():
    nodes = (WorkflowNode(node_id="a", kind=WorkflowNodeKind.EVENT, event_name="x"),)
    g = WorkflowGraph(entry_node="missing", nodes=nodes, edges=())
    ok, errors = validate_graph(g)
    assert not ok
    assert any("entry_node" in e for e in errors)


def test_graph_rejects_duplicate_node_ids():
    nodes = (
        WorkflowNode(node_id="a", kind=WorkflowNodeKind.EVENT, event_name="x"),
        WorkflowNode(node_id="a", kind=WorkflowNodeKind.EVENT, event_name="y"),
    )
    g = WorkflowGraph(entry_node="a", nodes=nodes, edges=())
    ok, errors = validate_graph(g)
    assert not ok
    assert any("duplicate node_id" in e for e in errors)


def test_graph_rejects_dangling_edge_reference():
    nodes = (WorkflowNode(node_id="a", kind=WorkflowNodeKind.EVENT, event_name="x"),)
    edges = (WorkflowEdge(source="a", target="ghost"),)
    g = WorkflowGraph(entry_node="a", nodes=nodes, edges=edges)
    ok, errors = validate_graph(g)
    assert not ok
    assert any("unknown target" in e for e in errors)


def test_graph_rejects_duplicate_edges():
    nodes = (
        WorkflowNode(node_id="a", kind=WorkflowNodeKind.EVENT, event_name="x"),
        WorkflowNode(node_id="b", kind=WorkflowNodeKind.EVENT, event_name="y"),
    )
    edges = (WorkflowEdge(source="a", target="b"), WorkflowEdge(source="a", target="b"))
    g = WorkflowGraph(entry_node="a", nodes=nodes, edges=edges)
    ok, errors = validate_graph(g)
    assert not ok
    assert any("duplicate edge" in e for e in errors)


def test_graph_allows_multiple_edges_to_same_target_with_different_conditions():
    nodes = (
        WorkflowNode(node_id="a", kind=WorkflowNodeKind.CONDITIONAL),
        WorkflowNode(node_id="b", kind=WorkflowNodeKind.EVENT, event_name="y"),
    )
    edges = (
        WorkflowEdge(
            source="a", target="b",
            condition=WorkflowCondition(field="x", operator=WorkflowConditionOperator.EQ, value=1),
        ),
        WorkflowEdge(
            source="a", target="b",
            condition=WorkflowCondition(field="x", operator=WorkflowConditionOperator.EQ, value=2),
        ),
    )
    g = WorkflowGraph(entry_node="a", nodes=nodes, edges=edges)
    ok, errors = validate_graph(g)
    assert ok, errors


def test_graph_rejects_unreachable_node():
    nodes = (
        WorkflowNode(node_id="a", kind=WorkflowNodeKind.EVENT, event_name="x"),
        WorkflowNode(node_id="orphan", kind=WorkflowNodeKind.EVENT, event_name="z"),
    )
    g = WorkflowGraph(entry_node="a", nodes=nodes, edges=())
    ok, errors = validate_graph(g)
    assert not ok
    assert any("unreachable" in e for e in errors)


def test_graph_rejects_a_real_cycle():
    nodes = (
        WorkflowNode(node_id="a", kind=WorkflowNodeKind.EVENT, event_name="x"),
        WorkflowNode(node_id="b", kind=WorkflowNodeKind.EVENT, event_name="y"),
    )
    edges = (WorkflowEdge(source="a", target="b"), WorkflowEdge(source="b", target="a"))
    g = WorkflowGraph(entry_node="a", nodes=nodes, edges=edges)
    ok, errors = validate_graph(g)
    assert not ok
    assert any("cycle" in e for e in errors)


def test_graph_detects_cycle_not_reachable_from_entry():
    """A cycle among nodes unreachable from entry_node must still be caught
    -- cycle detection runs over the whole node set, not just the reachable
    subgraph."""
    nodes = (
        WorkflowNode(node_id="a", kind=WorkflowNodeKind.EVENT, event_name="x"),
        WorkflowNode(node_id="b", kind=WorkflowNodeKind.EVENT, event_name="y"),
        WorkflowNode(node_id="c", kind=WorkflowNodeKind.EVENT, event_name="z"),
    )
    edges = (WorkflowEdge(source="b", target="c"), WorkflowEdge(source="c", target="b"))
    g = WorkflowGraph(entry_node="a", nodes=nodes, edges=edges)
    ok, errors = validate_graph(g)
    assert not ok
    joined = " ".join(errors)
    assert "cycle" in joined or "unreachable" in joined


def test_valid_diamond_shaped_parallel_graph():
    nodes = (
        WorkflowNode(node_id="start", kind=WorkflowNodeKind.PARALLEL),
        WorkflowNode(node_id="left", kind=WorkflowNodeKind.EVENT, event_name="l"),
        WorkflowNode(node_id="right", kind=WorkflowNodeKind.EVENT, event_name="r"),
        WorkflowNode(node_id="merge", kind=WorkflowNodeKind.MERGE),
    )
    edges = (
        WorkflowEdge(source="start", target="left"),
        WorkflowEdge(source="start", target="right"),
        WorkflowEdge(source="left", target="merge"),
        WorkflowEdge(source="right", target="merge"),
    )
    g = WorkflowGraph(entry_node="start", nodes=nodes, edges=edges)
    ok, errors = validate_graph(g)
    assert ok, errors


# ── Deterministic ordering + hashing ───────────────────────────────────────────
def test_topological_order_is_deterministic_leaf_first():
    g = _linear_graph()
    assert topological_order(g) == ("a", "b", "c")


def test_topological_order_stable_tie_break_among_parallel_branches():
    nodes = (
        WorkflowNode(node_id="start", kind=WorkflowNodeKind.PARALLEL),
        WorkflowNode(node_id="z_branch", kind=WorkflowNodeKind.EVENT, event_name="z"),
        WorkflowNode(node_id="a_branch", kind=WorkflowNodeKind.EVENT, event_name="a"),
    )
    edges = (
        WorkflowEdge(source="start", target="z_branch"),
        WorkflowEdge(source="start", target="a_branch"),
    )
    g = WorkflowGraph(entry_node="start", nodes=nodes, edges=edges)
    order1 = topological_order(g)
    order2 = topological_order(g)
    assert order1 == order2
    # stable node_id tie-break: a_branch sorts before z_branch
    assert order1.index("a_branch") < order1.index("z_branch")


def test_graph_hash_is_deterministic_and_order_independent():
    g1 = _linear_graph()
    reordered = WorkflowGraph(entry_node="a", nodes=tuple(reversed(g1.nodes)), edges=g1.edges)
    assert compute_graph_hash(g1) == compute_graph_hash(reordered)


def test_graph_hash_changes_with_topology():
    g1 = _linear_graph()
    g2 = WorkflowGraph(
        entry_node="a", nodes=g1.nodes,
        edges=(WorkflowEdge(source="a", target="c"), WorkflowEdge(source="c", target="b")),
    )
    assert compute_graph_hash(g1) != compute_graph_hash(g2)


def test_ids_are_deterministic_and_content_derived():
    assert content_hash("a", "b") == content_hash("a", "b")
    assert content_hash("a", "b") != content_hash("b", "a")
    same = descriptor_id_for("internal", "wf", "1.0.0")
    assert same == descriptor_id_for("internal", "wf", "1.0.0")


# ── Lifecycle FSM ────────────────────────────────────────────────────────────────
def test_lifecycle_registers_then_activates():
    d = WorkflowDescriptor(manifest=_manifest())
    d = advance_workflow(d, WorkflowTrigger.REGISTER, clock=lambda: 1.0)
    assert d.state is WorkflowLifecycleState.REGISTERED
    d = advance_workflow(d, WorkflowTrigger.ACTIVATE, clock=lambda: 1.0)
    assert d.state is WorkflowLifecycleState.ACTIVE


def test_lifecycle_direct_retire_from_registered_and_active():
    d = WorkflowDescriptor(manifest=_manifest(), state=WorkflowLifecycleState.REGISTERED)
    assert advance_workflow(d, WorkflowTrigger.RETIRE, clock=lambda: 1.0).state \
        is WorkflowLifecycleState.RETIRED
    d2 = WorkflowDescriptor(manifest=_manifest(), state=WorkflowLifecycleState.ACTIVE)
    assert advance_workflow(d2, WorkflowTrigger.RETIRE, clock=lambda: 1.0).state \
        is WorkflowLifecycleState.RETIRED


def test_lifecycle_rejects_illegal_transition():
    d = WorkflowDescriptor(manifest=_manifest(), state=WorkflowLifecycleState.DRAFT)
    with pytest.raises(FSMError):
        advance_workflow(d, WorkflowTrigger.ACTIVATE, clock=lambda: 1.0)


def test_no_execution_lifecycle_exists_in_bundle_1():
    import workflow.lifecycle as lifecycle_mod

    assert not hasattr(lifecycle_mod, "WorkflowExecutionState")
    assert not hasattr(lifecycle_mod, "advance_execution")


# ── Registry ─────────────────────────────────────────────────────────────────────
def test_registry_register_computes_graph_hash():
    reg = WorkflowRegistry(clock=lambda: 1.0)
    d = reg.register(_manifest())
    assert d.manifest.graph.graph_hash == compute_graph_hash(_linear_graph())


def test_registry_never_trusts_caller_supplied_graph_hash():
    reg = WorkflowRegistry(clock=lambda: 1.0)
    tampered_graph = _linear_graph().with_hash("not-the-real-hash")
    d = reg.register(_manifest(graph=tampered_graph))
    assert d.manifest.graph.graph_hash != "not-the-real-hash"
    assert d.manifest.graph.graph_hash == compute_graph_hash(_linear_graph())


def test_registry_rejects_duplicate_registration():
    reg = WorkflowRegistry(clock=lambda: 1.0)
    reg.register(_manifest())
    with pytest.raises(ConfigError):
        reg.register(_manifest())


def test_registry_rejects_invalid_graph():
    reg = WorkflowRegistry(clock=lambda: 1.0)
    bad = WorkflowGraph(entry_node="missing", nodes=(), edges=())
    with pytest.raises(ConfigError):
        reg.register(_manifest(graph=bad))


def test_registry_namespace_isolation():
    reg = WorkflowRegistry(clock=lambda: 1.0)
    reg.register(_manifest(namespace="internal"))
    reg.register(_manifest(namespace="org.acme"))
    assert reg.get_descriptor("internal", "wf", "1.0.0").manifest.namespace == "internal"
    assert reg.get_descriptor("org.acme", "wf", "1.0.0").manifest.namespace == "org.acme"


def test_registry_version_resolution_prefers_active():
    reg = WorkflowRegistry(clock=lambda: 1.0)
    reg.register(_manifest(version="1.0.0"))
    reg.register(_manifest(version="2.0.0"))
    reg.activate("internal", "wf", "1.0.0")
    resolved = reg.get_descriptor("internal", "wf")  # unpinned
    assert resolved.version == "1.0.0"


def test_registry_deregister_removes_entry():
    reg = WorkflowRegistry(clock=lambda: 1.0)
    reg.register(_manifest())
    reg.deregister("internal", "wf", "1.0.0")
    with pytest.raises(ConfigError):
        reg.get_descriptor("internal", "wf", "1.0.0")


def test_registry_has_no_execution_surface():
    for banned in ("execute", "invoke", "run", "advance", "checkpoint", "recover"):
        assert not hasattr(WorkflowRegistry, banned)


def test_list_scoped_filters_by_namespace():
    reg = WorkflowRegistry(clock=lambda: 1.0)
    reg.register(_manifest(namespace="internal"))
    reg.activate("internal", "wf", "1.0.0")
    reg.register(_manifest(namespace="org.acme"))
    reg.activate("org.acme", "wf", "1.0.0")
    scoped = reg.list_scoped(namespace="internal")
    assert [d.manifest.namespace for d in scoped] == ["internal"]


# ── WorkflowRuntime facade ──────────────────────────────────────────────────────
def test_runtime_register_and_get_descriptor():
    rt = _runtime()
    _registered_active(rt, _manifest())
    d = rt.get_descriptor("internal", "wf")
    assert d.state is WorkflowLifecycleState.ACTIVE


def test_runtime_registrations_returns_registration_log():
    rt = _runtime()
    _registered_active(rt, _manifest())
    regs = rt.registrations()
    assert len(regs) == 1
    assert regs[0].qualified_name == "internal.wf"


def test_runtime_diagnostics_and_statistics():
    rt = _runtime()
    _registered_active(rt, _manifest())
    diag = rt.diagnostics()
    assert diag.registry_stats["count"] == 1
    stats = rt.statistics()
    assert stats.workflow_count == 1
    assert stats.per_node_kind == {"tool": 1, "skill": 1, "event": 1}


def test_runtime_has_no_execution_surface():
    for banned in (
        "execute", "invoke", "run", "advance", "checkpoint", "recover", "replay", "cancel",
    ):
        assert not hasattr(WorkflowRuntime, banned)


def test_factory_rejects_unknown_store_kind():
    from workflow.config import StoreConfig

    with pytest.raises(ConfigError):
        WorkflowFactory.create(WorkflowConfiguration(document_store=StoreConfig(kind="s3")))


# ── Serialization round-trips ──────────────────────────────────────────────────
def test_manifest_serialization_round_trips():
    m = _manifest()
    s = WorkflowSerializer()
    assert s.manifest_from_dict(s.manifest_to_dict(m)) == m


def test_manifest_with_conditions_and_policies_round_trips():
    nodes = (
        WorkflowNode(
            node_id="a", kind=WorkflowNodeKind.CONDITIONAL,
            condition=WorkflowCondition(field="x", operator=WorkflowConditionOperator.GTE, value=3),
            retry_policy=RetryPolicy(max_attempts=2), timeout_policy=TimeoutPolicy(seconds=5.0),
            config={"note": "x"},
        ),
        WorkflowNode(node_id="b", kind=WorkflowNodeKind.DELAY, delay_ms=250),
    )
    edges = (WorkflowEdge(source="a", target="b"),)
    m = _manifest(graph=WorkflowGraph(entry_node="a", nodes=nodes, edges=edges))
    s = WorkflowSerializer()
    assert s.manifest_from_dict(s.manifest_to_dict(m)) == m


def test_descriptor_serialization_round_trips():
    reg = WorkflowRegistry(clock=lambda: 1.0)
    d = reg.register(_manifest())
    s = WorkflowSerializer()
    assert s.descriptor_from_document(s.descriptor_to_document(d)) == d


# ── Repository (temporary storage) ─────────────────────────────────────────────
@pytest.mark.asyncio
async def test_repository_stores_and_counts_registrations():
    store = InMemoryDocumentStore()
    serializer = WorkflowSerializer()
    repo = WorkflowRepository(store, serializer)

    reg = WorkflowRegistry(clock=lambda: 1.0)
    d = reg.register(_manifest())
    await repo.add_registration(d)
    assert await repo.registration_count() == 1
    fetched = await repo.all_registrations()
    assert fetched[0].qualified_name == "internal.wf"


# ── Configuration ────────────────────────────────────────────────────────────────
def test_configuration_rejects_non_positive_ttl():
    with pytest.raises(ConfigError):
        WorkflowConfiguration(registration_ttl_s=0)


def test_configuration_fingerprint_is_deterministic():
    a = WorkflowConfiguration.default()
    b = WorkflowConfiguration.default()
    assert a.fingerprint() == b.fingerprint()


# ── Determinism ────────────────────────────────────────────────────────────────
def test_registration_is_deterministic_for_identical_input():
    reg1 = WorkflowRegistry(clock=lambda: 100.0)
    reg2 = WorkflowRegistry(clock=lambda: 100.0)
    d1 = reg1.register(_manifest())
    d2 = reg2.register(_manifest())
    assert d1 == d2


# ── Dependency rules (workflow stays acyclic; no ownership leakage) ───────────
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


def test_workflow_dependency_rules():
    forbidden = {
        "runtime", "api", "gateway", "replay", "events", "memory", "knowledge",
        "context", "prompt", "reasoning", "provider", "bridge", "pipeline",
        "voicebot", "observability", "tenant", "tool", "skill", "hooks", "agent",
    }
    assert not (_imports("workflow") & forbidden), _imports("workflow")


def test_workflow_imports_only_core_and_own_package():
    allowed_extra = {
        "core", "__future__", "abc", "collections", "dataclasses", "enum", "typing",
        "hashlib", "copy", "time",
    }
    non_stdlib_like = {t for t in _imports("workflow") if t not in allowed_extra}
    assert not non_stdlib_like, non_stdlib_like


def test_workflow_skill_port_and_hook_adapter_remain_unimplemented():
    from abc import ABC

    from workflow.extensions import WorkflowHookAdapter, WorkflowSkillPort

    assert issubclass(WorkflowSkillPort, ABC)
    assert issubclass(WorkflowHookAdapter, ABC)
    with pytest.raises(TypeError):
        WorkflowSkillPort()  # type: ignore[abstract]
    with pytest.raises(TypeError):
        WorkflowHookAdapter()  # type: ignore[abstract]
