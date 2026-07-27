"""Product Deployment Planning Layer (M7.0C) tests.

Verifies valid/invalid deployment plans, the deterministic deployment identity
(…→Package→Deployment) + byte-identical serialization, rollback validation (previous/
previous-compatible/explicit/manual, verifiable vs not), rollout policy validation,
compatibility validation against the target environment, environment/tenant override
and feature-flag/resource consistency, immutability, and that the layer imports no
sibling runtime and exposes no deploy/execute verb (intent only, ADR-0018).

Regression anchors are marked `# regression:`.
"""

from __future__ import annotations

import ast
import dataclasses
import pathlib
from dataclasses import FrozenInstanceError

import pytest

from product import ProductFactory
from product.compiler import (
    BindingKind,
    BindingSnapshot,
    BindingSnapshotEntry,
    CompilationContext,
    ProductCompiler,
)
from product.deployment import (
    DeploymentDescriptor,
    DeploymentPlan,
    DeploymentPlanner,
    DeploymentPolicy,
    DeploymentProfile,
    DeploymentTarget,
    DeploymentValidator,
    ReleaseTarget,
    RollbackMode,
    RollbackPolicy,
)
from product.deployment.serializer import DeploymentSerializer
from product.diagnostics import ProductDiagnosticCode
from product.domain import (
    AgentRef,
    DeploymentStrategy,
    EnvironmentProfile,
    FeatureFlag,
    ProductManifest,
    ProductMetadata,
    TenantOverride,
    ToolRef,
    VersionMetadata,
    WorkflowRef,
)
from product.errors import ProductError
from product.registry import ProductPackage, ProductPackager, ProductRegistry


def _package(
    *, package_version: str = "1.0.0", flags: tuple[str, ...] = ("beta",),
    tenants: tuple[str, ...] = (),
) -> ProductPackage:
    manifest = ProductManifest(
        metadata=ProductMetadata(id="bot", name="Bot", version="1.0.0"),
        agents=(AgentRef(id="a", workflow_refs=("wf",)),),
        workflows=(WorkflowRef(id="wf"),),
        tools=(ToolRef(id="t", version="1.0.0"),),
        feature_flags=tuple(FeatureFlag(key=f) for f in flags),
        tenants=tuple(TenantOverride(tenant_id=t) for t in tenants),
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


def _descriptor(package: ProductPackage, **overrides) -> DeploymentDescriptor:
    base: dict = dict(
        package_identity=package.package_manifest.identity,
        profile=DeploymentProfile.PRODUCTION,
        tenancy=DeploymentTarget.SINGLE_TENANT,
        environment_profile=EnvironmentProfile(name="production"),
        release_target=ReleaseTarget(name="prod-cluster"),
        deployment_strategy=DeploymentStrategy.IMMEDIATE,
        rollout_policy=DeploymentPolicy(),
        rollback_policy=RollbackPolicy(mode=RollbackMode.MANUAL),
        target_execution_version="1.5.0",
    )
    base.update(overrides)
    return DeploymentDescriptor(**base)


def _plan(package: ProductPackage, descriptor=None, *, registry=None) -> DeploymentPlan:
    return DeploymentPlanner().plan(
        package, descriptor or _descriptor(package), registry=registry
    )


# ── valid plan ───────────────────────────────────────────────────────────────────
def test_valid_deployment_plan():
    plan = _plan(_package())
    assert isinstance(plan, DeploymentPlan)
    assert plan.is_valid and plan.report.summary.status == "valid"
    assert plan.deployment_id.startswith("dp_")


def test_plan_is_immutable():
    plan = _plan(_package())
    with pytest.raises(FrozenInstanceError):
        plan.content_hash = "x"  # type: ignore[misc]


def test_plan_carries_no_execution_or_infra_surface():
    plan = _plan(_package())
    surface = {n for n in dir(plan) if not n.startswith("_")}
    forbidden = {"execute", "run", "deploy", "apply", "connect", "client", "session", "start"}
    assert not (forbidden & surface)


def test_descriptor_mismatch_raises():
    other = _package(package_version="9.9.9")
    with pytest.raises(ProductError):
        DeploymentPlanner().plan(_package(), _descriptor(other))


# ── deployment identity chain ────────────────────────────────────────────────────
def test_identity_chain_extends_package_by_one_link():
    package = _package()
    plan = _plan(package)
    ident = plan.identity
    assert ident.package_id == package.package_id
    assert ident.manifest_id == package.package_manifest.identity.manifest_id
    assert ident.deployment_id == plan.deployment_id and ident.deployment_id.startswith("dp_")


def test_deployment_identity_changes_with_configuration():
    package = _package()
    a = _plan(package, _descriptor(package, profile=DeploymentProfile.STAGING))
    b = _plan(package, _descriptor(package, profile=DeploymentProfile.PRODUCTION))
    assert a.deployment_id != b.deployment_id  # regression: config change -> id change


def test_deployment_identity_is_deterministic():
    package = _package()
    desc = _descriptor(package)
    assert _plan(package, desc).deployment_id == _plan(package, desc).deployment_id


# ── deterministic serialization ──────────────────────────────────────────────────
def test_plan_serialization_is_byte_identical():
    package = _package()
    desc = _descriptor(package)
    ser = DeploymentSerializer()
    assert ser.plan_to_dict(_plan(package, desc)) == ser.plan_to_dict(_plan(package, desc))


def test_serialization_reflects_descriptor_changes():
    package = _package()
    ser = DeploymentSerializer()
    a = ser.plan_to_dict(_plan(package, _descriptor(package, profile=DeploymentProfile.QA)))
    b = ser.plan_to_dict(_plan(package, _descriptor(package, profile=DeploymentProfile.EDGE)))
    assert a != b


# ── compatibility validation ─────────────────────────────────────────────────────
def test_incompatible_target_environment_is_error():
    package = _package()
    plan = _plan(package, _descriptor(package, target_execution_version="0.5.0"))
    assert not plan.is_valid
    assert any(
        d.code is ProductDiagnosticCode.INCOMPATIBLE_DEPLOYMENT_ENVIRONMENT
        for d in plan.diagnostics.errors
    )
    assert not plan.compatibility.compatible


def test_compatible_target_environment_passes():
    plan = _plan(_package())
    assert plan.compatibility.compatible and plan.compatibility.execution_compatible


# ── rollout policy validation ────────────────────────────────────────────────────
def test_canary_requires_valid_percent():
    package = _package()
    ok = _plan(
        package,
        _descriptor(
            package,
            deployment_strategy=DeploymentStrategy.CANARY,
            rollout_policy=DeploymentPolicy(canary_percent=25),
        ),
    )
    assert ok.report.policy.valid
    bad = _plan(
        package,
        _descriptor(
            package,
            deployment_strategy=DeploymentStrategy.CANARY,
            rollout_policy=DeploymentPolicy(canary_percent=0),
        ),
    )
    assert any(
        d.code is ProductDiagnosticCode.DEPLOYMENT_POLICY_VIOLATION for d in bad.diagnostics.errors
    )


def test_rolling_requires_batch_size():
    package = _package()
    bad = _plan(
        package,
        _descriptor(
            package,
            deployment_strategy=DeploymentStrategy.ROLLING,
            rollout_policy=DeploymentPolicy(batch_size=0),
        ),
    )
    assert not bad.report.policy.valid


def test_manual_approval_requires_flag():
    package = _package()
    bad = _plan(
        package,
        _descriptor(
            package,
            deployment_strategy=DeploymentStrategy.MANUAL_APPROVAL,
            rollout_policy=DeploymentPolicy(requires_approval=False),
        ),
    )
    assert any(
        d.code is ProductDiagnosticCode.DEPLOYMENT_POLICY_VIOLATION for d in bad.diagnostics.errors
    )


def test_dead_config_param_for_wrong_strategy_is_violation():
    # regression: batch_size set on a non-rolling strategy is dead config -> violation
    package = _package()
    bad = _plan(
        package,
        _descriptor(
            package,
            deployment_strategy=DeploymentStrategy.IMMEDIATE,
            rollout_policy=DeploymentPolicy(batch_size=5),
        ),
    )
    assert not bad.report.policy.valid


# ── rollback validation ──────────────────────────────────────────────────────────
def _registry(*versions: str) -> ProductRegistry:
    reg = ProductRegistry()
    for v in versions:
        reg.register(_package(package_version=v))
    return reg


def test_rollback_previous_version_resolves_against_registry():
    reg = _registry("1.0.0", "1.1.0", "1.2.0")
    current = reg.get_package(reg.latest("internal.bot").package_id)
    plan = _plan(
        current,
        _descriptor(current, rollback_policy=RollbackPolicy(mode=RollbackMode.PREVIOUS_VERSION)),
        registry=reg,
    )
    assert plan.report.rollback.resolved_version == "1.1.0"  # regression: previous same-major
    assert plan.report.rollback.verifiable


def test_rollback_unverifiable_without_registry():
    package = _package(package_version="1.2.0")
    plan = _plan(
        package,
        _descriptor(package, rollback_policy=RollbackPolicy(mode=RollbackMode.PREVIOUS_VERSION)),
    )
    assert not plan.report.rollback.verifiable
    assert any(
        d.code is ProductDiagnosticCode.UNVERIFIABLE_ROLLBACK_TARGET
        for d in plan.diagnostics.warnings
    )


def test_rollback_explicit_requires_version():
    package = _package()
    plan = _plan(
        package,
        _descriptor(
            package,
            rollback_policy=RollbackPolicy(
                mode=RollbackMode.EXPLICIT_VERSION, explicit_version=None
            ),
        ),
    )
    assert any(
        d.code is ProductDiagnosticCode.INVALID_ROLLBACK_POLICY for d in plan.diagnostics.errors
    )


def test_rollback_explicit_target_must_exist_in_registry():
    reg = _registry("1.0.0", "1.1.0")
    current = reg.get_package(reg.exact("internal.bot", "1.1.0").package_id)
    plan = _plan(
        current,
        _descriptor(
            current,
            rollback_policy=RollbackPolicy(
                mode=RollbackMode.EXPLICIT_VERSION, explicit_version="9.9.9"
            ),
        ),
        registry=reg,
    )
    assert any(
        d.code is ProductDiagnosticCode.INVALID_ROLLBACK_TARGET for d in plan.diagnostics.errors
    )


def test_rollback_manual_is_always_valid():
    pkg = _package()
    plan = _plan(pkg, _descriptor(pkg, rollback_policy=RollbackPolicy(RollbackMode.MANUAL)))
    assert plan.report.rollback.valid and plan.report.rollback.mode is RollbackMode.MANUAL


# ── override / flag / resource consistency ───────────────────────────────────────
def test_unknown_feature_flag_is_error():
    package = _package(flags=("beta",))
    plan = _plan(package, _descriptor(package, feature_flags={"ghost": True}))
    assert any(
        d.code is ProductDiagnosticCode.UNKNOWN_FEATURE_FLAG for d in plan.diagnostics.errors
    )


def test_environment_override_asserting_execution_is_rejected():
    package = _package()
    plan = _plan(
        package,
        _descriptor(
            package, environment_profile=EnvironmentProfile(name="prod", overrides={"fsm": {}})
        ),
    )
    assert any(
        d.code is ProductDiagnosticCode.INVALID_ENVIRONMENT_OVERRIDE
        for d in plan.diagnostics.errors
    )
    assert plan.diagnostics.invalid_environment_overrides  # categorized view populated


def test_negative_resource_limit_is_error():
    package = _package()
    plan = _plan(package, _descriptor(package, resource_profile={"cpu": -1}))
    assert any(
        d.code is ProductDiagnosticCode.INVALID_RESOURCE_PROFILE for d in plan.diagnostics.errors
    )


def test_single_tenant_with_multiple_overrides_is_inconsistent():
    package = _package()
    plan = _plan(
        package,
        _descriptor(
            package,
            tenancy=DeploymentTarget.SINGLE_TENANT,
            tenant_overrides={"t1": {"x": 1}, "t2": {"y": 2}},
        ),
    )
    assert any(
        d.code is ProductDiagnosticCode.INCONSISTENT_TENANT_OVERRIDE
        for d in plan.diagnostics.errors
    )


# ── integrity feeds deployment validation ────────────────────────────────────────
def test_tampered_package_fails_deployment_validation():
    package = _package()
    bad_integrity = dataclasses.replace(
        package.package_manifest.integrity, dependency_hash="dead"
    )
    bad_manifest = dataclasses.replace(package.package_manifest, integrity=bad_integrity)
    tampered = dataclasses.replace(package, package_manifest=bad_manifest)
    plan = DeploymentPlanner().plan(tampered, _descriptor(tampered))
    assert any(
        d.code is ProductDiagnosticCode.PACKAGE_INTEGRITY_MISMATCH for d in plan.diagnostics.errors
    )


# ── validator is independently testable ──────────────────────────────────────────
def test_validator_used_standalone():
    package = _package()
    validation = DeploymentValidator().validate(package, _descriptor(package))
    assert validation.diagnostics.is_valid
    assert validation.compatibility.compatible and validation.policy.valid


# ── dependency rules ─────────────────────────────────────────────────────────────
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


def test_deployment_imports_no_sibling_runtime():
    forbidden = {
        "runtime", "api", "gateway", "replay", "events", "memory", "knowledge",
        "context", "prompt", "reasoning", "provider", "bridge", "pipeline",
        "voicebot", "observability", "tenant", "tool", "skill", "workflow",
        "agent", "loop", "mcp", "hooks", "integration",
    }
    assert not (_imports("product") & forbidden), _imports("product")
