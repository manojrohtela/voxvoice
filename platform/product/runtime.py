"""ProductRuntime — the immutable composition façade (Epic 7, M7.0D/C1 closure).

The single, read-only entry point that composes the Product Runtime artifacts a
consumer holds: a `ProductPackage`, its `CompiledProduct`, its metadata and
diagnostics, and (optionally) a `DeploymentPlan`. It is a **façade only** — pure
composition over already-built immutable artifacts, per the M7.0A "Runtime
Components" design.

It **never**: executes, deploys, owns a runtime lifecycle, owns providers/workflows,
or touches infrastructure. It holds no runtime state and exposes no such verb — it
only *accepts* immutable artifacts and *exposes* them. Constructing one performs no
I/O and is deterministic; identical inputs yield an equal façade (`frozen`).

Naming note: this realizes the `ProductRuntime` façade named in the M7.0A design; the
word "Runtime" here denotes the Product Runtime's read-only composition surface, not
an execution engine (which remains the frozen Epic 6 Execution Platform's, never
this layer's — ADR-0018).
"""

from __future__ import annotations

from dataclasses import dataclass

from product.compiler import CompiledProduct
from product.deployment import DeploymentDiagnostics, DeploymentPlan
from product.diagnostics import ProductDiagnostics
from product.errors import ProductError
from product.registry import ProductPackage, ProductPackageMetadata


@dataclass(frozen=True, slots=True)
class ProductRuntime:
    """An immutable composition of a `ProductPackage` and an optional
    `DeploymentPlan`. All accessors are read-only projections — no execution,
    deployment, or infrastructure surface."""

    package: ProductPackage
    deployment_plan: DeploymentPlan | None = None

    @classmethod
    def of(
        cls, package: ProductPackage, deployment_plan: DeploymentPlan | None = None
    ) -> ProductRuntime:
        """Compose a façade. Raises `ProductError` (composition-boundary guard) if a
        supplied deployment plan does not reference this exact package — the only
        way the two artifacts could be inconsistent."""
        if (
            deployment_plan is not None
            and deployment_plan.package_ref.package_id != package.package_id
        ):
            raise ProductError(
                "deployment plan does not reference this package "
                f"({deployment_plan.package_ref.package_id} != {package.package_id})"
            )
        return cls(package=package, deployment_plan=deployment_plan)

    # ── read-only projections ──────────────────────────────────────────────────
    @property
    def compiled_product(self) -> CompiledProduct:
        return self.package.compiled_product

    @property
    def metadata(self) -> ProductPackageMetadata:
        return self.package.metadata()

    @property
    def diagnostics(self) -> ProductDiagnostics:
        """The compilation diagnostics embedded in the compiled product."""
        return self.package.compiled_product.diagnostics

    @property
    def deployment_diagnostics(self) -> DeploymentDiagnostics | None:
        """The deployment plan's diagnostics, if a plan is composed."""
        return self.deployment_plan.diagnostics if self.deployment_plan is not None else None

    @property
    def package_id(self) -> str:
        return self.package.package_id

    @property
    def qualified_name(self) -> str:
        return self.package.qualified_name

    @property
    def package_version(self) -> str:
        return self.package.package_version

    @property
    def has_deployment_plan(self) -> bool:
        return self.deployment_plan is not None

    @property
    def is_deployable(self) -> bool:
        """A composed plan exists and validated without errors. This is a
        *description* of readiness — it never triggers a deployment."""
        return self.deployment_plan is not None and self.deployment_plan.is_valid
