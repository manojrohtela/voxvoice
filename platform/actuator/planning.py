"""Deployment plan adaptation (Epic 8, M8.16) — ADR-0022.

The Actuator consumes a **read-only** Epic 7 `DeploymentPlan` (deployment *intent*). This module is
the *only* place the Actuator touches Product Runtime — it **reuses** the frozen
`product.deployment` value objects to materialise a valid plan from a published-agent reference. It
imports `product` read-only (the ADR-0022 dependency `actuator → product`); Product Runtime is never
modified and never learns the Actuator exists. Keeping this here — not in the control-plane
Foundation — keeps the Foundation's frozen-Epic import boundary (it imports only `actuator`).

Building a plan here is legitimate reuse of Product Runtime's *declarative* types; it performs no
compilation and no execution (that is the Actuator's, behind the infrastructure adapter seam).
"""

from __future__ import annotations

from dataclasses import dataclass

from product.deployment import (
    DeploymentCompatibilityReport,
    DeploymentDescriptor,
    DeploymentDiagnostics,
    DeploymentIdentity,
    DeploymentPlan,
    DeploymentPolicy,
    DeploymentProfile,
    DeploymentReport,
    DeploymentSummary,
    DeploymentTarget,
    PackageReference,
    PolicyReport,
    ReleaseTarget,
    RollbackMode,
    RollbackPolicy,
    RollbackResolution,
    deployment_id_for,
)
from product.domain import DeploymentStrategy, EnvironmentProfile
from product.ids import content_hash
from product.registry import PackageIdentity

_PROFILE_BY_ENV = {
    "production": DeploymentProfile.PRODUCTION,
    "prod": DeploymentProfile.PRODUCTION,
    "staging": DeploymentProfile.STAGING,
    "qa": DeploymentProfile.QA,
    "testing": DeploymentProfile.TESTING,
    "test": DeploymentProfile.TESTING,
    "development": DeploymentProfile.DEVELOPMENT,
    "dev": DeploymentProfile.DEVELOPMENT,
}


def profile_for(environment: str) -> str:
    return _PROFILE_BY_ENV.get(environment.strip().casefold(), DeploymentProfile.DEVELOPMENT).value


def is_production_environment(environment: str) -> bool:
    return _PROFILE_BY_ENV.get(environment.strip().casefold()) is DeploymentProfile.PRODUCTION


def _strategy_for(strategy: str) -> DeploymentStrategy:
    try:
        return DeploymentStrategy(strategy)
    except ValueError:
        return DeploymentStrategy.ROLLING


@dataclass(frozen=True, slots=True)
class PlanRef:
    """The published-agent reference a plan is built from (a lightweight, control-plane-owned
    source descriptor — the Actuator does not reach into agent internals)."""

    agent_id: str
    agent_name: str
    version_id: str
    version_number: int


def build_deployment_plan(
    ref: PlanRef, *, environment: str, strategy: str, requires_approval: bool
) -> DeploymentPlan:
    """Build an immutable, valid Epic 7 `DeploymentPlan` from a published-agent reference — the
    deployment *intent* the Actuator consumes read-only. Content-addressed + deterministic."""
    strat = _strategy_for(strategy)
    profile = _PROFILE_BY_ENV.get(environment.strip().casefold(), DeploymentProfile.DEVELOPMENT)
    seed = f"{ref.agent_id}:{ref.version_id}:{environment}:{strat.value}"
    pkg_id = "pkg_" + content_hash(seed)[:16]
    identity_chain = PackageIdentity(
        manifest_id="mf_" + content_hash(ref.agent_id)[:12],
        definition_id="df_" + content_hash(ref.version_id)[:12],
        compilation_id="cp_" + content_hash(seed)[:12],
        package_id=pkg_id,
    )
    descriptor = DeploymentDescriptor(
        package_identity=identity_chain,
        profile=profile,
        tenancy=DeploymentTarget.SINGLE_TENANT,
        environment_profile=EnvironmentProfile(name=environment),
        release_target=ReleaseTarget(name=environment),
        deployment_strategy=strat,
        rollout_policy=DeploymentPolicy(requires_approval=requires_approval),
        rollback_policy=RollbackPolicy(mode=RollbackMode.PREVIOUS_VERSION),
    )
    descriptor_hash = content_hash(seed, strat.value, profile.value)
    dep_id = deployment_id_for(pkg_id, descriptor_hash)
    report = DeploymentReport(
        summary=DeploymentSummary(
            status="valid",
            error_count=0,
            warning_count=0,
            profile=profile,
            tenancy=DeploymentTarget.SINGLE_TENANT,
            strategy=strat,
        ),
        diagnostics=DeploymentDiagnostics.of([]),
        compatibility=DeploymentCompatibilityReport(
            compatible=True,
            execution_compatible=True,
            runtime_compatible=True,
            target_execution_version="",
            target_runtime_version="",
        ),
        rollback=RollbackResolution(
            mode=RollbackMode.PREVIOUS_VERSION,
            resolved_version=None,
            valid=True,
            verifiable=False,
        ),
        policy=PolicyReport(strategy=strat, valid=True, requires_approval=requires_approval),
        deployment_id=dep_id,
    )
    return DeploymentPlan(
        package_ref=PackageReference(
            package_id=pkg_id,
            content_hash=descriptor_hash,
            qualified_name=f"{ref.agent_name}@{environment}",
            package_version=str(ref.version_number),
        ),
        descriptor=descriptor,
        report=report,
        identity=DeploymentIdentity(
            manifest_id=identity_chain.manifest_id,
            definition_id=identity_chain.definition_id,
            compilation_id=identity_chain.compilation_id,
            package_id=pkg_id,
            deployment_id=dep_id,
        ),
        content_id=dep_id,
        content_hash=descriptor_hash,
    )
