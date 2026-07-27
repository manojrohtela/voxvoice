"""Product Compiler & Binding Resolution (M7.0B-2) tests.

Verifies the deterministic 8-stage pipeline and every stage in isolation: successful
compilation, binding resolution against an injected `BindingSnapshot` (ADR-0019/0020),
missing/duplicate bindings, dependency analysis (missing references, cycles,
unreachable nodes, topological order), configuration precedence + override tracing +
conflict reporting, static compatibility (interface/schema/platform/version
constraints), ownership violations, immutability of `CompiledProduct`, and
byte-identical determinism across compiler instances. Also asserts the compiler
imports no Epic 5/6 sibling runtime and executes nothing.

Regression anchors are marked `# regression:`.
"""

from __future__ import annotations

import ast
import pathlib
from dataclasses import FrozenInstanceError

from product import ProductFactory
from product.compiler import (
    BindingKind,
    BindingResolver,
    BindingSnapshot,
    BindingSnapshotEntry,
    CompatibilityValidator,
    CompilationContext,
    CompiledProduct,
    ConfigurationResolver,
    DependencyAnalyzer,
    OwnershipValidator,
    ProductCompiler,
    compiled_to_dict,
    report_to_dict,
)
from product.compiler.bindings import ToolBinding
from product.diagnostics import ProductDiagnosticCode
from product.domain import (
    AgentRef,
    EnvironmentProfile,
    KnowledgeRef,
    MCPServerRef,
    ProductManifest,
    ProductMetadata,
    ProviderRef,
    SkillRef,
    TenantOverride,
    ToolRef,
    VersionMetadata,
    WorkflowRef,
)


def _metadata(**kw) -> ProductMetadata:
    base = dict(id="bot", name="Bot", version="1.0.0")
    base.update(kw)
    return ProductMetadata(**base)


def _manifest(**kw) -> ProductManifest:
    base: dict = dict(metadata=_metadata())
    base.update(kw)
    return ProductManifest(**base)


def _snapshot(*entries: BindingSnapshotEntry) -> BindingSnapshot:
    return BindingSnapshot.of(entries)


def _entry(
    kind: BindingKind, id_: str, *versions: str, ns: str = "internal"
) -> BindingSnapshotEntry:
    return BindingSnapshotEntry(kind=kind, namespace=ns, id=id_, versions=versions)


def _ctx(snapshot: BindingSnapshot, **kw) -> CompilationContext:
    return CompilationContext(snapshot=snapshot, **kw)


def _compile(manifest: ProductManifest, snapshot: BindingSnapshot, **ctx_kw):
    definition = ProductFactory.from_manifest(manifest)
    return ProductCompiler().compile(definition, _ctx(snapshot, **ctx_kw))


# ── stage 7/8: successful compilation ────────────────────────────────────────────
def test_successful_compilation_produces_compiled_product_and_report():
    m = _manifest(
        agents=(AgentRef(id="a", workflow_refs=("wf",)),),
        workflows=(WorkflowRef(id="wf"),),
    )
    snap = _snapshot(
        _entry(BindingKind.AGENT, "a", "1.0.0"),
        _entry(BindingKind.WORKFLOW, "wf", "1.0.0"),
    )
    result = _compile(m, snap)
    assert result.success and isinstance(result.compiled, CompiledProduct)
    assert result.report.summary.status == "success"
    assert result.compiled.dependency_graph.is_acyclic
    assert len(result.compiled.bindings) == 2


def test_unpinned_reference_resolves_to_highest_version():
    m = _manifest(tools=(ToolRef(id="t", version=None),))
    snap = _snapshot(_entry(BindingKind.TOOL, "t", "1.0.0", "2.3.0", "2.0.0"))
    result = _compile(m, snap)
    tool = next(b for b in result.compiled.bindings if b.id == "t")
    assert tool.resolved_version == "2.3.0"  # regression: highest, not first/last


def test_pinned_reference_resolves_exact_version():
    m = _manifest(tools=(ToolRef(id="t", version="1.0.0"),))
    snap = _snapshot(_entry(BindingKind.TOOL, "t", "1.0.0", "2.0.0"))
    result = _compile(m, snap)
    assert next(b for b in result.compiled.bindings if b.id == "t").resolved_version == "1.0.0"


# ── stage 2: missing / duplicate bindings ────────────────────────────────────────
def test_missing_binding_fails_compilation():
    m = _manifest(tools=(ToolRef(id="ghost"),))
    result = _compile(m, _snapshot())
    assert not result.success and result.compiled is None
    assert any(
        d.code is ProductDiagnosticCode.MISSING_BINDING for d in result.report.diagnostics.errors
    )


def test_pinned_version_not_in_snapshot_is_missing_binding():
    m = _manifest(tools=(ToolRef(id="t", version="9.9.9"),))
    result = _compile(m, _snapshot(_entry(BindingKind.TOOL, "t", "1.0.0")))
    assert any(
        d.code is ProductDiagnosticCode.MISSING_BINDING for d in result.report.diagnostics.errors
    )


def test_duplicate_binding_detected():
    # Two DISTINCT manifest refs (pass M7.0B-1 duplicate-id validation) that
    # RESOLVE to the same binding identity: an unpinned ref and a pinned ref both
    # resolving to 2.0.0 with identical config -> one DUPLICATE_BINDING at resolve.
    m = _manifest(tools=(ToolRef(id="t", version=None), ToolRef(id="t", version="2.0.0")))
    result = _compile(m, _snapshot(_entry(BindingKind.TOOL, "t", "2.0.0")))
    assert any(
        d.code is ProductDiagnosticCode.DUPLICATE_BINDING
        for d in result.report.diagnostics.errors
    )


def test_binding_resolver_is_independently_testable():
    m = _manifest(providers=(ProviderRef(id="p", version="1.0.0"),))
    resolution = BindingResolver().resolve(m, _snapshot(_entry(BindingKind.PROVIDER, "p", "1.0.0")))
    assert len(resolution.bindings) == 1 and not resolution.findings
    assert resolution.bindings[0].kind is BindingKind.PROVIDER


# ── stage 4: dependency analysis ─────────────────────────────────────────────────
def test_missing_reference_edge_is_error():
    m = _manifest(
        agents=(AgentRef(id="a", skill_refs=("nope",)),),
    )
    snap = _snapshot(_entry(BindingKind.AGENT, "a", "1.0.0"))
    result = _compile(m, snap)
    assert any(
        d.code is ProductDiagnosticCode.MISSING_REFERENCE for d in result.report.diagnostics.errors
    )


def test_unreachable_node_is_warning_not_error():
    # A tool no agent references -> unreachable warning, compilation still succeeds.
    m = _manifest(
        agents=(AgentRef(id="a"),),
        tools=(ToolRef(id="orphan"),),
    )
    snap = _snapshot(
        _entry(BindingKind.AGENT, "a", "1.0.0"),
        _entry(BindingKind.TOOL, "orphan", "1.0.0"),
    )
    result = _compile(m, snap)
    assert result.success  # regression: unreachable warns, never blocks
    assert any(
        d.code is ProductDiagnosticCode.UNREACHABLE_NODE
        for d in result.report.diagnostics.warnings
    )


def test_topological_order_is_deterministic_and_complete():
    m = _manifest(
        agents=(AgentRef(id="a", workflow_refs=("wf",), skill_refs=("sk",)),),
        workflows=(WorkflowRef(id="wf"),),
        skills=(SkillRef(id="sk"),),
    )
    snap = _snapshot(
        _entry(BindingKind.AGENT, "a", "1.0.0"),
        _entry(BindingKind.WORKFLOW, "wf", "1.0.0"),
        _entry(BindingKind.SKILL, "sk", "1.0.0"),
    )
    g1 = _compile(m, snap).compiled.dependency_graph
    g2 = _compile(m, snap).compiled.dependency_graph
    assert g1.topological_order == g2.topological_order
    assert len(g1.topological_order) == len(g1.nodes) == 3


def test_dependency_analyzer_reports_cycle():
    # Hand-build two agent bindings that reference each other -> cycle.
    from product.compiler.bindings import AgentBinding, BindingEdge

    a = AgentBinding(
        namespace="internal", id="a", resolved_version="1.0.0",
        edges=(BindingEdge(BindingKind.AGENT, "b"),),
    )
    b = AgentBinding(
        namespace="internal", id="b", resolved_version="1.0.0",
        edges=(BindingEdge(BindingKind.AGENT, "a"),),
    )
    analysis = DependencyAnalyzer().analyze((a, b))
    assert not analysis.graph.is_acyclic
    codes = {d.code for d in analysis.findings}
    assert ProductDiagnosticCode.CIRCULAR_DEPENDENCY in codes
    assert ProductDiagnosticCode.INVALID_DEPENDENCY_ORDER in codes
    assert analysis.graph.topological_order == ()


# ── stage 3: configuration precedence / tracing / conflicts ──────────────────────
def test_configuration_precedence_environment_overrides_default():
    m = _manifest(
        environments=(
            EnvironmentProfile(name="default", overrides={"log": "info", "region": "us"}),
            EnvironmentProfile(name="prod", overrides={"log": "warn"}),
        ),
    )
    snap = _snapshot()
    resolution = ConfigurationResolver().resolve(
        m, _ctx(snap, environment="prod")
    )
    values = resolution.configuration.values
    assert values["log"] == "warn"  # regression: environment beats default
    assert values["region"] == "us"  # untouched default survives


def test_configuration_override_tracing_records_layers():
    m = _manifest(
        environments=(
            EnvironmentProfile(name="default", overrides={"log": "info"}),
            EnvironmentProfile(name="prod", overrides={"log": "warn"}),
        ),
        tenants=(TenantOverride(tenant_id="t1", overrides={"log": "debug"}),),
    )
    resolution = ConfigurationResolver().resolve(
        m, _ctx(_snapshot(), environment="prod", tenant_id="t1")
    )
    trace = next(t for t in resolution.configuration.traces if t.path == "log")
    assert trace.winning_layer == "tenant"  # tenant is later than environment/default
    assert "default" in trace.overridden_layers and "environment" in trace.overridden_layers


def test_runtime_and_request_are_contracts_not_applied():
    resolution = ConfigurationResolver().resolve(_manifest(), _ctx(_snapshot()))
    assert resolution.configuration.applied_layers == (
        "default", "environment", "tenant", "deployment",
    )
    assert resolution.configuration.contract_layers == ("runtime", "request")


def test_configuration_conflict_is_reported():
    # "db" is a scalar in default but an object in prod -> structural conflict.
    m = _manifest(
        environments=(
            EnvironmentProfile(name="default", overrides={"db": "sqlite"}),
            EnvironmentProfile(name="prod", overrides={"db": {"host": "x"}}),
        ),
    )
    resolution = ConfigurationResolver().resolve(m, _ctx(_snapshot(), environment="prod"))
    assert any(
        d.code is ProductDiagnosticCode.CONFIGURATION_CONFLICT for d in resolution.findings
    )


def test_missing_selected_environment_is_warning():
    resolution = ConfigurationResolver().resolve(
        _manifest(), _ctx(_snapshot(), environment="ghost")
    )
    assert any(
        d.code is ProductDiagnosticCode.ENVIRONMENT_NOT_FOUND for d in resolution.findings
    )


# ── stage 6: compatibility ───────────────────────────────────────────────────────
def test_execution_platform_incompatibility_fails():
    m = _manifest(version_metadata=VersionMetadata(requires_execution="2.0.0"))
    result = _compile(m, _snapshot(), execution_platform_version="1.0.0")
    assert any(
        d.code is ProductDiagnosticCode.INCOMPATIBLE_EXECUTION_PLATFORM
        for d in result.report.diagnostics.errors
    )


def test_execution_platform_compatible_passes():
    m = _manifest(version_metadata=VersionMetadata(requires_execution="1.0.0"))
    result = _compile(m, _snapshot(), execution_platform_version="1.2.0")
    assert result.report.compatibility_report.execution_compatible


def test_declared_constraint_without_platform_version_fails():
    m = _manifest(version_metadata=VersionMetadata(requires_execution="1.0.0"))
    result = _compile(m, _snapshot())  # no execution_platform_version supplied
    assert not result.report.compatibility_report.execution_compatible


def test_invalid_version_constraint_reported():
    m = _manifest(version_metadata=VersionMetadata(requires_execution="not-semver"))
    result = _compile(m, _snapshot(), execution_platform_version="1.0.0")
    assert any(
        d.code is ProductDiagnosticCode.INVALID_VERSION_CONSTRAINT
        for d in result.report.diagnostics.errors
    )


def test_schema_version_mismatch_fails():
    m = _manifest()
    result = _compile(m, _snapshot(), expected_schema_version="999")
    assert any(
        d.code is ProductDiagnosticCode.UNSUPPORTED_SCHEMA_VERSION
        for d in result.report.diagnostics.errors
    )


def test_compatibility_validator_is_independently_testable():
    m = _manifest(version_metadata=VersionMetadata(requires_runtime="1.0.0"))
    res = CompatibilityValidator().validate(m, _ctx(_snapshot(), runtime_platform_version="1.0.0"))
    assert res.metadata.compatible and res.report.runtime_compatible


# ── stage 5: ownership ───────────────────────────────────────────────────────────
def test_ownership_violation_rejected():
    m = _manifest(tools=(ToolRef(id="t", version="1.0.0", config={"fsm": {"states": []}}),))
    result = _compile(m, _snapshot(_entry(BindingKind.TOOL, "t", "1.0.0")))
    assert not result.success
    assert any(
        d.code is ProductDiagnosticCode.OWNERSHIP_VIOLATION
        for d in result.report.diagnostics.errors
    )


def test_ownership_violation_nested_key():
    m = _manifest(
        environments=(EnvironmentProfile(name="default", overrides={"a": {"execute": "boom"}}),),
    )
    result = OwnershipValidator().validate(m)
    assert result.report.violation_count == 1
    assert result.report.violations == ("environments[0].overrides.a.execute",)


def test_clean_config_has_no_ownership_violation():
    m = _manifest(tools=(ToolRef(id="t", version="1.0.0", config={"timeout_ms": 500}),))
    assert OwnershipValidator().validate(m).report.violation_count == 0


# ── determinism ──────────────────────────────────────────────────────────────────
def test_compilation_is_byte_identical_for_identical_inputs():
    m = _manifest(
        agents=(AgentRef(id="a", workflow_refs=("wf",)),),
        workflows=(WorkflowRef(id="wf"),),
        tools=(ToolRef(id="t", version="1.0.0"),),
        knowledge=(KnowledgeRef(id="kb"),),
        mcp_servers=(MCPServerRef(id="mcp"),),
    )
    snap = _snapshot(
        _entry(BindingKind.AGENT, "a", "1.0.0"),
        _entry(BindingKind.WORKFLOW, "wf", "1.0.0"),
        _entry(BindingKind.TOOL, "t", "1.0.0"),
        _entry(BindingKind.KNOWLEDGE, "kb", "1.0.0"),
        _entry(BindingKind.MCP, "mcp", "1.0.0"),
    )
    r1 = _compile(m, snap, execution_platform_version="1.0.0")
    r2 = _compile(m, snap, execution_platform_version="1.0.0")
    # regression: identical definition + snapshot + compiler version => identical bytes
    assert compiled_to_dict(r1.compiled) == compiled_to_dict(r2.compiled)
    assert r1.compiled.content_hash == r2.compiled.content_hash
    assert report_to_dict(r1.report) == report_to_dict(r2.report)


def test_reference_authoring_order_does_not_change_identity():
    snap = _snapshot(
        _entry(BindingKind.TOOL, "a", "1.0.0"),
        _entry(BindingKind.TOOL, "b", "1.0.0"),
    )
    m1 = _manifest(tools=(ToolRef(id="a", version="1.0.0"), ToolRef(id="b", version="1.0.0")))
    m2 = _manifest(tools=(ToolRef(id="b", version="1.0.0"), ToolRef(id="a", version="1.0.0")))
    assert _compile(m1, snap).compiled.content_hash == _compile(m2, snap).compiled.content_hash


def test_different_snapshot_changes_compilation_identity():
    m = _manifest(tools=(ToolRef(id="t", version=None),))
    r_low = _compile(m, _snapshot(_entry(BindingKind.TOOL, "t", "1.0.0")))
    r_high = _compile(m, _snapshot(_entry(BindingKind.TOOL, "t", "2.0.0")))
    # regression: resolving against a different snapshot yields a different artifact
    assert r_low.compiled.content_hash != r_high.compiled.content_hash


def test_failed_compilation_report_is_deterministic():
    m = _manifest(tools=(ToolRef(id="ghost"),))
    r1 = _compile(m, _snapshot())
    r2 = _compile(m, _snapshot())
    assert not r1.success and not r2.success
    assert report_to_dict(r1.report) == report_to_dict(r2.report)
    assert r1.report.compilation_id == r2.report.compilation_id


# ── immutability + no execution/runtime state ────────────────────────────────────
def test_compiled_product_is_immutable():
    result = _compile(_manifest(), _snapshot())
    with __import__("pytest").raises(FrozenInstanceError):
        result.compiled.content_hash = "x"  # type: ignore[misc]


def test_compiled_product_carries_no_execution_or_network_surface():
    result = _compile(_manifest(), _snapshot())
    surface = {n for n in dir(result.compiled) if not n.startswith("_")}
    forbidden = {"execute", "run", "invoke", "connect", "session", "client", "socket", "deploy"}
    assert not (forbidden & surface)


def test_binding_is_inert_data_only():
    b = ToolBinding(namespace="internal", id="t", resolved_version="1.0.0")
    surface = {n for n in dir(b) if not n.startswith("_")}
    assert not ({"execute", "invoke", "resolve", "load", "bind", "connect"} & surface)


def test_report_is_machine_readable_dict():
    result = _compile(_manifest(), _snapshot())
    d = report_to_dict(result.report)
    assert set(d) >= {
        "summary", "dependency_report", "compatibility_report",
        "ownership_report", "configuration_report", "diagnostics", "compilation_id",
    }


# ── dependency rules (compiler stays acyclic; ADR-0019) ──────────────────────────
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


def test_compiler_imports_no_sibling_runtime():
    # ADR-0019: the compiler resolves against a snapshot, never a live Epic 6/5
    # runtime; it depends only on `core` + its own `product` package + stdlib.
    forbidden = {
        "runtime", "api", "gateway", "replay", "events", "memory", "knowledge",
        "context", "prompt", "reasoning", "provider", "bridge", "pipeline",
        "voicebot", "observability", "tenant", "tool", "skill", "workflow",
        "agent", "loop", "mcp", "hooks", "integration",
    }
    assert not (_imports("product") & forbidden), _imports("product")
