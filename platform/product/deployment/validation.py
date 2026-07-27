"""DeploymentValidator (M7.0C) — static validation of deployment intent only.

Validates a `DeploymentDescriptor` against its `ProductPackage`: package integrity,
target compatibility, deployment profile/target, environment + tenant overrides,
resource limits, feature-flag consistency, rollout policy consistency, and rollback
target admissibility. It performs **no runtime validation and no infrastructure
validation** — a `ProductRegistry` may be passed (read-only) purely to resolve/verify
rollback targets. Deterministic; never raises; reports via findings.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from product.compiler.ownership import FORBIDDEN_KEYS
from product.compiler.versions import version_sort_key
from product.diagnostics import Diagnostic, ProductDiagnosticCode, Severity
from product.domain import DeploymentStrategy
from product.registry import IntegrityValidator, ProductPackage, ProductRegistry, VersionManager

from .descriptor import DeploymentDescriptor, DeploymentTarget, RollbackMode
from .diagnostics import DeploymentDiagnostics
from .report import DeploymentCompatibilityReport, PolicyReport, RollbackResolution


@dataclass(frozen=True, slots=True)
class DeploymentValidation:
    diagnostics: DeploymentDiagnostics
    compatibility: DeploymentCompatibilityReport
    rollback: RollbackResolution
    policy: PolicyReport


class DeploymentValidator:
    """Stateless. `validate` never raises — it reports via findings."""

    def __init__(self) -> None:
        self._integrity = IntegrityValidator()
        self._versions = VersionManager()

    def validate(
        self,
        package: ProductPackage,
        descriptor: DeploymentDescriptor,
        *,
        registry: ProductRegistry | None = None,
    ) -> DeploymentValidation:
        findings: list[Diagnostic] = []

        self._validate_integrity(package, findings)
        compatibility = self._validate_compatibility(package, descriptor, findings)
        self._validate_target(descriptor, findings)
        self._validate_environment_overrides(descriptor, findings)
        self._validate_resource_profile(descriptor, findings)
        self._validate_feature_flags(package, descriptor, findings)
        self._validate_tenant_overrides(package, descriptor, findings)
        policy = self._validate_policy(descriptor, findings)
        rollback = self._validate_rollback(package, descriptor, registry, findings)

        return DeploymentValidation(
            diagnostics=DeploymentDiagnostics.of(findings),
            compatibility=compatibility,
            rollback=rollback,
            policy=policy,
        )

    # ── integrity ───────────────────────────────────────────────────────────────
    def _validate_integrity(self, package: ProductPackage, out: list[Diagnostic]) -> None:
        out.extend(self._integrity.verify(package).findings)

    # ── compatibility ─────────────────────────────────────────────────────────
    def _validate_compatibility(
        self, package: ProductPackage, descriptor: DeploymentDescriptor, out: list[Diagnostic]
    ) -> DeploymentCompatibilityReport:
        cm = package.package_manifest.compatibility_matrix
        ev = cm.evaluate(
            execution_platform_version=descriptor.target_execution_version,
            runtime_platform_version=descriptor.target_runtime_version,
        )
        if not ev.compatible:
            out.append(
                Diagnostic(
                    "descriptor.target_environment",
                    ProductDiagnosticCode.INCOMPATIBLE_DEPLOYMENT_ENVIRONMENT,
                    Severity.ERROR,
                    f"package requires execution {cm.requires_execution or '-'} / runtime "
                    f"{cm.requires_runtime or '-'}; target offers "
                    f"{descriptor.target_execution_version or '-'} / "
                    f"{descriptor.target_runtime_version or '-'}",
                    "deploy to an environment whose platform versions satisfy the package",
                )
            )
        return DeploymentCompatibilityReport(
            compatible=ev.compatible,
            execution_compatible=ev.execution_compatible,
            runtime_compatible=ev.runtime_compatible,
            target_execution_version=descriptor.target_execution_version,
            target_runtime_version=descriptor.target_runtime_version,
        )

    # ── target / release ───────────────────────────────────────────────────────
    def _validate_target(self, descriptor: DeploymentDescriptor, out: list[Diagnostic]) -> None:
        if not descriptor.release_target.name:
            out.append(
                Diagnostic(
                    "descriptor.release_target.name",
                    ProductDiagnosticCode.MISSING_DEPLOYMENT_TARGET,
                    Severity.ERROR,
                    "release target has no name",
                    "name the release target (environment/cluster/region label)",
                )
            )
        if not descriptor.environment_profile.name:
            out.append(
                Diagnostic(
                    "descriptor.environment_profile.name",
                    ProductDiagnosticCode.MISSING_DEPLOYMENT_TARGET,
                    Severity.ERROR,
                    "target environment has no name",
                    "select a named environment to deploy to",
                )
            )

    # ── environment overrides ───────────────────────────────────────────────────
    def _validate_environment_overrides(
        self, descriptor: DeploymentDescriptor, out: list[Diagnostic]
    ) -> None:
        for key in _forbidden_keys_in(descriptor.environment_overrides):
            out.append(
                Diagnostic(
                    f"descriptor.environment_profile.overrides.{key}",
                    ProductDiagnosticCode.INVALID_ENVIRONMENT_OVERRIDE,
                    Severity.ERROR,
                    f"environment override {key!r} asserts execution ownership",
                    "the product declares references, never execution (ADR-0018)",
                )
            )

    # ── resource profile ─────────────────────────────────────────────────────
    def _validate_resource_profile(
        self, descriptor: DeploymentDescriptor, out: list[Diagnostic]
    ) -> None:
        for key, value in descriptor.resource_profile.items():
            if isinstance(value, bool):
                continue
            if isinstance(value, (int, float)) and value < 0:
                out.append(
                    Diagnostic(
                        f"descriptor.resource_profile.{key}",
                        ProductDiagnosticCode.INVALID_RESOURCE_PROFILE,
                        Severity.ERROR,
                        f"resource limit {key!r} is negative ({value})",
                        "resource limits must be non-negative",
                    )
                )

    # ── feature flags ─────────────────────────────────────────────────────────
    def _validate_feature_flags(
        self, package: ProductPackage, descriptor: DeploymentDescriptor, out: list[Diagnostic]
    ) -> None:
        declared = {f.key for f in package.product_manifest.feature_flags}
        for key in descriptor.feature_flags:
            if key not in declared:
                out.append(
                    Diagnostic(
                        f"descriptor.feature_flags.{key}",
                        ProductDiagnosticCode.UNKNOWN_FEATURE_FLAG,
                        Severity.ERROR,
                        f"feature flag {key!r} is not declared by the product",
                        "only set feature flags the product manifest declares",
                    )
                )

    # ── tenant overrides ─────────────────────────────────────────────────────
    def _validate_tenant_overrides(
        self, package: ProductPackage, descriptor: DeploymentDescriptor, out: list[Diagnostic]
    ) -> None:
        overrides = descriptor.tenant_overrides
        if descriptor.tenancy is DeploymentTarget.SINGLE_TENANT and len(overrides) > 1:
            out.append(
                Diagnostic(
                    "descriptor.tenant_overrides",
                    ProductDiagnosticCode.INCONSISTENT_TENANT_OVERRIDE,
                    Severity.ERROR,
                    f"single-tenant deployment declares {len(overrides)} tenant overrides",
                    "a single-tenant deployment may carry at most one tenant override",
                )
            )
        declared = {t.tenant_id for t in package.product_manifest.tenants}
        for tenant_id, tenant_override in overrides.items():
            if declared and tenant_id not in declared:
                out.append(
                    Diagnostic(
                        f"descriptor.tenant_overrides.{tenant_id}",
                        ProductDiagnosticCode.INCONSISTENT_TENANT_OVERRIDE,
                        Severity.WARNING,
                        f"tenant override {tenant_id!r} is not declared in the product manifest",
                        "declare the tenant in the manifest or remove the override",
                    )
                )
            for key in _forbidden_keys_in(tenant_override):
                out.append(
                    Diagnostic(
                        f"descriptor.tenant_overrides.{tenant_id}.{key}",
                        ProductDiagnosticCode.INCONSISTENT_TENANT_OVERRIDE,
                        Severity.ERROR,
                        f"tenant override {key!r} asserts execution ownership",
                        "the product declares references, never execution (ADR-0018)",
                    )
                )

    # ── rollout policy ─────────────────────────────────────────────────────────
    def _validate_policy(
        self, descriptor: DeploymentDescriptor, out: list[Diagnostic]
    ) -> PolicyReport:
        strategy = descriptor.deployment_strategy
        policy = descriptor.rollout_policy
        before = len(out)

        if strategy is DeploymentStrategy.CANARY:
            if not (1 <= policy.canary_percent <= 100):
                out.append(_policy_violation("canary_percent must be within 1..100 for canary"))
        elif policy.canary_percent != 0:
            out.append(
                _policy_violation(f"canary_percent set for non-canary strategy {strategy.value!r}")
            )

        if strategy is DeploymentStrategy.ROLLING:
            if policy.batch_size < 1:
                out.append(_policy_violation("batch_size must be >= 1 for rolling"))
        elif policy.batch_size != 0:
            out.append(
                _policy_violation(f"batch_size set for non-rolling strategy {strategy.value!r}")
            )

        if strategy is DeploymentStrategy.MANUAL_APPROVAL and not policy.requires_approval:
            out.append(
                _policy_violation("manual_approval strategy requires requires_approval=True")
            )

        return PolicyReport(
            strategy=strategy,
            valid=len(out) == before,
            requires_approval=policy.requires_approval,
        )

    # ── rollback ─────────────────────────────────────────────────────────────
    def _validate_rollback(
        self,
        package: ProductPackage,
        descriptor: DeploymentDescriptor,
        registry: ProductRegistry | None,
        out: list[Diagnostic],
    ) -> RollbackResolution:
        policy = descriptor.rollback_policy
        mode = policy.mode
        name = package.qualified_name
        current = package.package_version

        if mode is RollbackMode.MANUAL:
            return RollbackResolution(mode=mode, resolved_version=None, valid=True, verifiable=True)

        if mode is RollbackMode.EXPLICIT_VERSION:
            return self._rollback_explicit(policy.explicit_version, name, registry, out)

        # PREVIOUS_VERSION / PREVIOUS_COMPATIBLE_VERSION
        if registry is None:
            out.append(
                _rollback_finding(
                    ProductDiagnosticCode.UNVERIFIABLE_ROLLBACK_TARGET,
                    Severity.WARNING,
                    f"rollback target for {mode.value} cannot be verified without a registry",
                    "supply a registry to verify the rollback target",
                )
            )
            return RollbackResolution(
                mode=mode, resolved_version=None, valid=True, verifiable=False
            )

        target = self._resolve_previous(mode, package, descriptor, registry, current, name)
        if target is None:
            out.append(
                _rollback_finding(
                    ProductDiagnosticCode.MISSING_ROLLBACK_TARGET,
                    Severity.WARNING,
                    f"no {mode.value} rollback target found for {name} below {current}",
                    "no prior version is available to roll back to yet",
                )
            )
            return RollbackResolution(mode=mode, resolved_version=None, valid=True, verifiable=True)
        return RollbackResolution(mode=mode, resolved_version=target, valid=True, verifiable=True)

    def _rollback_explicit(
        self,
        explicit_version: str | None,
        name: str,
        registry: ProductRegistry | None,
        out: list[Diagnostic],
    ) -> RollbackResolution:
        mode = RollbackMode.EXPLICIT_VERSION
        if not explicit_version:
            out.append(
                _rollback_finding(
                    ProductDiagnosticCode.INVALID_ROLLBACK_POLICY,
                    Severity.ERROR,
                    "explicit_version rollback requires rollback_policy.explicit_version",
                    "set the explicit rollback version",
                )
            )
            return RollbackResolution(
                mode=mode, resolved_version=None, valid=False, verifiable=True
            )
        if registry is None:
            out.append(
                _rollback_finding(
                    ProductDiagnosticCode.UNVERIFIABLE_ROLLBACK_TARGET,
                    Severity.WARNING,
                    f"explicit rollback target {explicit_version} needs a registry to verify",
                    "supply a registry to verify the rollback target",
                )
            )
            return RollbackResolution(
                mode=mode, resolved_version=explicit_version, valid=True, verifiable=False
            )
        if registry.exact(name, explicit_version) is None:
            out.append(
                _rollback_finding(
                    ProductDiagnosticCode.INVALID_ROLLBACK_TARGET,
                    Severity.ERROR,
                    f"explicit rollback target {explicit_version} is not in the registry",
                    "choose a registered version to roll back to",
                )
            )
            return RollbackResolution(
                mode=mode, resolved_version=explicit_version, valid=False, verifiable=True
            )
        return RollbackResolution(
            mode=mode, resolved_version=explicit_version, valid=True, verifiable=True
        )

    def _resolve_previous(
        self, mode, package, descriptor, registry, current, name
    ) -> str | None:
        if mode is RollbackMode.PREVIOUS_VERSION:
            target = registry.rollback_target(name, current)
            return target.package_version if target else None
        # PREVIOUS_COMPATIBLE_VERSION: highest same-major downgrade that is also
        # compatible with the target platform versions.
        compatible = registry.compatible(
            name,
            execution_platform_version=descriptor.target_execution_version,
            runtime_platform_version=descriptor.target_runtime_version,
        )
        candidates = [
            e.package_version
            for e in compatible
            if self._versions.is_rollback_allowed(current, e.package_version)
        ]
        return max(candidates, key=version_sort_key) if candidates else None


def _forbidden_keys_in(mapping: Mapping[str, object]) -> list[str]:
    return [k for k in mapping if str(k).lower() in FORBIDDEN_KEYS]


def _policy_violation(message: str) -> Diagnostic:
    return Diagnostic(
        "descriptor.rollout_policy",
        ProductDiagnosticCode.DEPLOYMENT_POLICY_VIOLATION,
        Severity.ERROR,
        message,
        "align the rollout policy parameters with the deployment strategy",
    )


def _rollback_finding(
    code: ProductDiagnosticCode, severity: Severity, message: str, remediation: str
) -> Diagnostic:
    return Diagnostic("descriptor.rollback_policy", code, severity, message, remediation)
