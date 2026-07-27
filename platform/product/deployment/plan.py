"""DeploymentPlan (M7.0C) — the immutable, validated deployment artifact.

Contains only inert data: a reference to the `ProductPackage`, the
`DeploymentDescriptor`, the validation `DeploymentReport` (with diagnostics +
compatibility), and the deterministic `DeploymentIdentity`. **Nothing executable** —
a plan describes a deployment; it never performs one (ADR-0018).
"""

from __future__ import annotations

from dataclasses import dataclass

from .descriptor import DeploymentDescriptor, DeploymentProfile, PackageReference
from .diagnostics import DeploymentDiagnostics
from .identity import DeploymentIdentity
from .report import DeploymentCompatibilityReport, DeploymentReport


@dataclass(frozen=True, slots=True)
class DeploymentPlan:
    """An immutable, content-addressed deployment plan."""

    package_ref: PackageReference
    descriptor: DeploymentDescriptor
    report: DeploymentReport
    identity: DeploymentIdentity
    content_id: str = ""  # == identity.deployment_id
    content_hash: str = ""  # descriptor canonical hash

    @property
    def deployment_id(self) -> str:
        return self.identity.deployment_id

    @property
    def profile(self) -> DeploymentProfile:
        return self.descriptor.profile

    @property
    def diagnostics(self) -> DeploymentDiagnostics:
        return self.report.diagnostics

    @property
    def compatibility(self) -> DeploymentCompatibilityReport:
        return self.report.compatibility

    @property
    def is_valid(self) -> bool:
        return not self.report.diagnostics.has_errors
