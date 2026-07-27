"""DeploymentPlanner (M7.0C) — assembles a validated, immutable DeploymentPlan.

The one place a `DeploymentPlan` is built: it validates a `DeploymentDescriptor`
against its `ProductPackage` (optionally consulting a read-only `ProductRegistry` for
rollback targets), derives the deterministic deployment identity, and assembles the
plan + report. It **never deploys, never calls infrastructure, never starts a runtime
session** (ADR-0018). A plan is always produced; `plan.is_valid` reflects the
validation outcome.
"""

from __future__ import annotations

from product.errors import ProductError
from product.registry import ProductPackage, ProductRegistry

from .descriptor import DeploymentDescriptor, PackageReference
from .identity import DeploymentIdentity, deployment_id_for
from .plan import DeploymentPlan
from .report import DeploymentReport, DeploymentSummary
from .serializer import descriptor_hash
from .validation import DeploymentValidator


class DeploymentPlanner:
    """Stateless, deterministic. `plan` raises `ProductError` only for programmer
    misuse (a descriptor built for a different package)."""

    def __init__(self) -> None:
        self._validator = DeploymentValidator()

    def plan(
        self,
        package: ProductPackage,
        descriptor: DeploymentDescriptor,
        *,
        registry: ProductRegistry | None = None,
    ) -> DeploymentPlan:
        if descriptor.package_identity.package_id != package.package_id:
            raise ProductError(
                "descriptor package identity does not match the package "
                f"({descriptor.package_identity.package_id} != {package.package_id})"
            )

        validation = self._validator.validate(package, descriptor, registry=registry)
        diagnostics = validation.diagnostics

        desc_hash = descriptor_hash(descriptor)
        deployment_id = deployment_id_for(package.package_id, desc_hash)
        pid = descriptor.package_identity
        identity = DeploymentIdentity(
            manifest_id=pid.manifest_id,
            definition_id=pid.definition_id,
            compilation_id=pid.compilation_id,
            package_id=pid.package_id,
            deployment_id=deployment_id,
        )

        report = DeploymentReport(
            summary=DeploymentSummary(
                status="valid" if not diagnostics.has_errors else "invalid",
                error_count=len(diagnostics.errors),
                warning_count=len(diagnostics.warnings),
                profile=descriptor.profile,
                tenancy=descriptor.tenancy,
                strategy=descriptor.deployment_strategy,
            ),
            diagnostics=diagnostics,
            compatibility=validation.compatibility,
            rollback=validation.rollback,
            policy=validation.policy,
            deployment_id=deployment_id,
        )
        return DeploymentPlan(
            package_ref=PackageReference.of(package),
            descriptor=descriptor,
            report=report,
            identity=identity,
            content_id=deployment_id,
            content_hash=desc_hash,
        )
