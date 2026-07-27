"""Deployment reports (M7.0C) — machine-readable, deterministic result records.

Pure data aggregating the validation outcome: a summary, compatibility, rollback
resolution, and policy sub-reports, plus the full diagnostics and the deterministic
deployment identity. Rendered to a plain dict by `serializer`.
"""

from __future__ import annotations

from dataclasses import dataclass

from product.domain import DeploymentStrategy

from .descriptor import DeploymentProfile, DeploymentTarget, RollbackMode
from .diagnostics import DeploymentDiagnostics


@dataclass(frozen=True, slots=True)
class DeploymentCompatibilityReport:
    compatible: bool
    execution_compatible: bool
    runtime_compatible: bool
    target_execution_version: str
    target_runtime_version: str


@dataclass(frozen=True, slots=True)
class RollbackResolution:
    """The outcome of validating the rollback policy against the registry (if any).
    `resolved_version` is the target version the policy would roll back to, when it
    can be determined statically; `verifiable` is False when no registry was supplied
    to check against."""

    mode: RollbackMode
    resolved_version: str | None
    valid: bool
    verifiable: bool


@dataclass(frozen=True, slots=True)
class PolicyReport:
    strategy: DeploymentStrategy
    valid: bool
    requires_approval: bool


@dataclass(frozen=True, slots=True)
class DeploymentSummary:
    status: str  # "valid" | "invalid"
    error_count: int
    warning_count: int
    profile: DeploymentProfile
    tenancy: DeploymentTarget
    strategy: DeploymentStrategy


@dataclass(frozen=True, slots=True)
class DeploymentReport:
    summary: DeploymentSummary
    diagnostics: DeploymentDiagnostics
    compatibility: DeploymentCompatibilityReport
    rollback: RollbackResolution
    policy: PolicyReport
    deployment_id: str
