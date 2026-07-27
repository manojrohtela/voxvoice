"""Deployment descriptor value objects (M7.0C) — declarative intent only.

Everything here is immutable, inert data describing *how* a package should be
deployed — never a runtime handle, infrastructure client, or execution object
(ADR-0018). The Deployment Planning Layer owns deployment **intent**; actuation is
infrastructure's, outside Product Runtime.

`EnvironmentProfile` and `DeploymentStrategy` are **reused** from `product.domain`
(the manifest's named-environment-with-overrides and rollout-strategy concepts) —
not re-declared — so no term denotes two concepts.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from product.domain import DeploymentStrategy, EnvironmentProfile
from product.registry import PackageIdentity, ProductPackage


class DeploymentProfile(StrEnum):
    """The deployment class — lifecycle stage and/or topology. Declarative."""

    DEVELOPMENT = "development"
    TESTING = "testing"
    QA = "qa"
    STAGING = "staging"
    PRODUCTION = "production"
    OFFLINE = "offline"
    EDGE = "edge"
    CLOUD = "cloud"
    HYBRID = "hybrid"


class DeploymentTarget(StrEnum):
    """Tenancy of the target. Declarative; orchestration is a non-goal."""

    SINGLE_TENANT = "single_tenant"
    MULTI_TENANT = "multi_tenant"


class RollbackMode(StrEnum):
    PREVIOUS_VERSION = "previous_version"
    PREVIOUS_COMPATIBLE_VERSION = "previous_compatible_version"
    EXPLICIT_VERSION = "explicit_version"
    MANUAL = "manual"


@dataclass(frozen=True, slots=True)
class ReleaseTarget:
    """The concrete release destination descriptor — a name plus optional region and
    tenant. Descriptive only (no cluster handles/clients)."""

    name: str
    region: str = ""
    tenant_id: str | None = None


@dataclass(frozen=True, slots=True)
class DeploymentPolicy:
    """The rollout policy parameters (the *how much / how fast* of the rollout). The
    rollout *strategy* itself is `DeploymentDescriptor.deployment_strategy`; these are
    its parameters, validated for consistency with it by `DeploymentValidator`."""

    canary_percent: int = 0
    batch_size: int = 0
    requires_approval: bool = False


@dataclass(frozen=True, slots=True)
class RollbackPolicy:
    """Declares the rollback intent. `explicit_version` is required only for
    `EXPLICIT_VERSION`. Targets are *validated*, never performed (ADR-0018)."""

    mode: RollbackMode = RollbackMode.PREVIOUS_VERSION
    explicit_version: str | None = None


@dataclass(frozen=True, slots=True)
class PackageReference:
    """An immutable reference to the package being deployed — never the package
    itself, keeping the plan a lightweight descriptor."""

    package_id: str
    content_hash: str
    qualified_name: str
    package_version: str

    @classmethod
    def of(cls, package: ProductPackage) -> PackageReference:
        return cls(
            package_id=package.package_id,
            content_hash=package.content_hash,
            qualified_name=package.qualified_name,
            package_version=package.package_version,
        )


@dataclass(frozen=True, slots=True)
class DeploymentDescriptor:
    """The immutable declarative description of a deployment. Describes only:
    package identity, deployment strategy, target environment, release target,
    resource profile, feature flags, environment overrides (via `environment_profile`),
    tenant overrides, rollout policy, and rollback policy. No runtime handles, no
    infrastructure clients, no execution objects."""

    package_identity: PackageIdentity
    profile: DeploymentProfile
    tenancy: DeploymentTarget
    environment_profile: EnvironmentProfile
    release_target: ReleaseTarget
    deployment_strategy: DeploymentStrategy = DeploymentStrategy.ROLLING
    rollout_policy: DeploymentPolicy = field(default_factory=DeploymentPolicy)
    rollback_policy: RollbackPolicy = field(default_factory=RollbackPolicy)
    resource_profile: Mapping[str, Any] = field(default_factory=dict)
    feature_flags: Mapping[str, bool] = field(default_factory=dict)
    tenant_overrides: Mapping[str, Mapping[str, Any]] = field(default_factory=dict)
    target_execution_version: str = ""
    target_runtime_version: str = ""

    @property
    def environment_overrides(self) -> Mapping[str, Any]:
        return self.environment_profile.overrides

    @property
    def environment(self) -> str:
        return self.environment_profile.name
