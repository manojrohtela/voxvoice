"""product.deployment — the Product Deployment Planning Layer (Epic 7, M7.0C).

Transforms an immutable `ProductPackage` into a validated, immutable `DeploymentPlan`
— an immutable `DeploymentDescriptor` (deployment **intent** only) plus a validation
`DeploymentReport`. It owns deployment intent and nothing more: it **never performs a
deployment, never calls infrastructure, and never starts a runtime session**
(ADR-0018). Extends the identity chain by one link (…→ Package ID → Deployment ID).

`EnvironmentProfile` and `DeploymentStrategy` are **reused** from `product.domain`
(not re-declared). Deployment execution, infrastructure adapters, Kubernetes,
Terraform, GitOps, and enterprise features are explicit non-goals — declared only as
extension-point protocols. `product → core` only; no sibling runtime imported.
"""

from product.domain import DeploymentStrategy, EnvironmentProfile

from .descriptor import (
    DeploymentDescriptor,
    DeploymentPolicy,
    DeploymentProfile,
    DeploymentTarget,
    PackageReference,
    ReleaseTarget,
    RollbackMode,
    RollbackPolicy,
)
from .diagnostics import DeploymentDiagnostics
from .extensions import (
    ApprovalWorkflow,
    DeploymentOrchestrator,
    DeploymentScheduler,
    GitOpsIntegration,
    KubernetesIntegration,
    TerraformIntegration,
)
from .factory import DeploymentPlanner
from .identity import DeploymentIdentity, deployment_id_for
from .plan import DeploymentPlan
from .report import (
    DeploymentCompatibilityReport,
    DeploymentReport,
    DeploymentSummary,
    PolicyReport,
    RollbackResolution,
)
from .serializer import DeploymentSerializer, descriptor_hash
from .validation import DeploymentValidation, DeploymentValidator

__all__ = [
    # planning entry point
    "DeploymentPlanner",
    "DeploymentPlan",
    # descriptor + value objects
    "DeploymentDescriptor",
    "DeploymentProfile",
    "DeploymentTarget",
    "DeploymentPolicy",
    "RollbackPolicy",
    "RollbackMode",
    "ReleaseTarget",
    "PackageReference",
    "EnvironmentProfile",  # reused from product.domain
    "DeploymentStrategy",  # reused from product.domain
    # identity
    "DeploymentIdentity",
    "deployment_id_for",
    # validation
    "DeploymentValidator",
    "DeploymentValidation",
    # diagnostics + reports
    "DeploymentDiagnostics",
    "DeploymentReport",
    "DeploymentSummary",
    "DeploymentCompatibilityReport",
    "RollbackResolution",
    "PolicyReport",
    # serialization
    "DeploymentSerializer",
    "descriptor_hash",
    # extension points (interfaces only)
    "DeploymentScheduler",
    "ApprovalWorkflow",
    "DeploymentOrchestrator",
    "GitOpsIntegration",
    "KubernetesIntegration",
    "TerraformIntegration",
]
