"""ProductRuntime composition façade (Epic 7, M7.0D/C1) tests.

Verifies the façade is immutable, deterministically constructed, purely compositional
over already-built artifacts, guards the package↔plan composition boundary, and
exposes NO execution/deployment/infrastructure surface. Also re-asserts the
`product → core` boundary now that the façade is added.
"""

from __future__ import annotations

import ast
import pathlib
from dataclasses import FrozenInstanceError

import pytest

from product import ProductFactory, ProductRuntime
from product.compiler import (
    BindingKind,
    BindingSnapshot,
    BindingSnapshotEntry,
    CompilationContext,
    CompiledProduct,
    ProductCompiler,
)
from product.deployment import (
    DeploymentDescriptor,
    DeploymentPlanner,
    DeploymentProfile,
    DeploymentTarget,
    ReleaseTarget,
    RollbackMode,
    RollbackPolicy,
)
from product.domain import (
    AgentRef,
    DeploymentStrategy,
    EnvironmentProfile,
    ProductManifest,
    ProductMetadata,
    ToolRef,
    VersionMetadata,
    WorkflowRef,
)
from product.errors import ProductError
from product.registry import ProductPackage, ProductPackager, ProductRegistry


def _package(package_version: str = "1.0.0") -> ProductPackage:
    manifest = ProductManifest(
        metadata=ProductMetadata(id="bot", name="Bot", version="1.0.0"),
        agents=(AgentRef(id="a", workflow_refs=("wf",)),),
        workflows=(WorkflowRef(id="wf"),),
        tools=(ToolRef(id="t", version="1.0.0"),),
        version_metadata=VersionMetadata(requires_execution="1.0.0"),
    )
    definition = ProductFactory.from_manifest(manifest)
    snapshot = BindingSnapshot.of(
        [
            BindingSnapshotEntry(BindingKind.AGENT, "internal", "a", ("1.0.0",)),
            BindingSnapshotEntry(BindingKind.WORKFLOW, "internal", "wf", ("1.0.0",)),
            BindingSnapshotEntry(BindingKind.TOOL, "internal", "t", ("1.0.0",)),
        ]
    )
    result = ProductCompiler().compile(
        definition, CompilationContext(snapshot=snapshot, execution_platform_version="1.2.0")
    )
    return ProductPackager().package(definition, result, package_version=package_version)


def _plan(package: ProductPackage, *, registry: ProductRegistry | None = None):
    descriptor = DeploymentDescriptor(
        package_identity=package.package_manifest.identity,
        profile=DeploymentProfile.PRODUCTION,
        tenancy=DeploymentTarget.SINGLE_TENANT,
        environment_profile=EnvironmentProfile(name="production"),
        release_target=ReleaseTarget(name="prod-cluster"),
        deployment_strategy=DeploymentStrategy.IMMEDIATE,
        rollback_policy=RollbackPolicy(mode=RollbackMode.MANUAL),
        target_execution_version="1.5.0",
    )
    return DeploymentPlanner().plan(package, descriptor, registry=registry)


# ── composition ──────────────────────────────────────────────────────────────────
def test_facade_composes_package_only():
    package = _package()
    rt = ProductRuntime.of(package)
    assert rt.package is package
    assert isinstance(rt.compiled_product, CompiledProduct)
    assert rt.compiled_product is package.compiled_product
    assert rt.metadata.package_id == package.package_id
    assert rt.qualified_name == "internal.bot" and rt.package_version == "1.0.0"
    assert not rt.has_deployment_plan and not rt.is_deployable
    assert rt.deployment_diagnostics is None


def test_facade_composes_package_and_plan():
    package = _package()
    plan = _plan(package)
    rt = ProductRuntime.of(package, plan)
    assert rt.has_deployment_plan and rt.deployment_plan is plan
    assert rt.is_deployable  # plan validated without errors
    assert rt.deployment_diagnostics is not None
    assert rt.diagnostics is package.compiled_product.diagnostics


def test_facade_rejects_mismatched_plan():
    # A plan built for a different package must not compose with this one.
    package = _package(package_version="1.0.0")
    other = _package(package_version="2.0.0")
    plan_for_other = _plan(other)
    with pytest.raises(ProductError):
        ProductRuntime.of(package, plan_for_other)


# ── immutability ─────────────────────────────────────────────────────────────────
def test_facade_is_immutable():
    rt = ProductRuntime.of(_package())
    with pytest.raises(FrozenInstanceError):
        rt.package = _package()  # type: ignore[misc]
    with pytest.raises(FrozenInstanceError):
        rt.deployment_plan = None  # type: ignore[misc]


# ── deterministic construction ───────────────────────────────────────────────────
def test_facade_construction_is_deterministic():
    package = _package()
    plan = _plan(package)
    a = ProductRuntime.of(package, plan)
    b = ProductRuntime.of(package, plan)
    assert a == b  # frozen value equality — identical inputs -> equal façade


# ── no execution/deployment/infra surface ────────────────────────────────────────
def test_facade_exposes_no_execution_or_deploy_surface():
    surface = {n for n in dir(ProductRuntime.of(_package())) if not n.startswith("_")}
    forbidden = {
        "execute", "run", "deploy", "apply", "start", "launch", "provision",
        "connect", "client", "session", "socket", "lifecycle", "install",
    }
    assert not (forbidden & surface)


# ── boundary re-assertion (façade added) ─────────────────────────────────────────
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


def test_product_still_imports_no_sibling_runtime_after_facade():
    forbidden = {
        "runtime", "api", "gateway", "replay", "events", "memory", "knowledge",
        "context", "prompt", "reasoning", "provider", "bridge", "pipeline",
        "voicebot", "observability", "tenant", "tool", "skill", "workflow",
        "agent", "loop", "mcp", "hooks", "integration",
    }
    assert not (_imports("product") & forbidden), _imports("product")
