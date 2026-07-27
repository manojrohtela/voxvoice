"""Deployment extension points (M7.0C) — interfaces only, no implementation.

The M7.0A design calls for the deployment layer to be extensible toward scheduling,
approval workflows, orchestration, and GitOps/Kubernetes/Terraform integration. Per
this milestone ("Interfaces only. Do not implement.") these are declared as protocols
a future, out-of-Product-Runtime bundle implements. Everything here consumes an
already-built, immutable `DeploymentPlan` and returns intent/records only — **none of
these interfaces actuate a deployment**; actuation remains infrastructure's, outside
Product Runtime (ADR-0018).
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

from .plan import DeploymentPlan


@runtime_checkable
class DeploymentScheduler(Protocol):
    """Decides *when* a validated plan should be actuated — returns a schedule
    descriptor, never performs the deployment. Declared only."""

    def schedule(self, plan: DeploymentPlan) -> Any: ...


@runtime_checkable
class ApprovalWorkflow(Protocol):
    """Gates a plan behind an approval process — returns an approval record/decision,
    never performs the deployment. Declared only."""

    def request_approval(self, plan: DeploymentPlan) -> Any: ...


@runtime_checkable
class DeploymentOrchestrator(Protocol):
    """The seam a future, out-of-Product-Runtime actuator implements to execute a
    plan. Product Runtime never implements this — it only produces the plan the
    orchestrator consumes. Declared only."""

    def apply(self, plan: DeploymentPlan) -> Any: ...


@runtime_checkable
class GitOpsIntegration(Protocol):
    """Renders a plan into a GitOps desired-state artifact. Declared only."""

    def render(self, plan: DeploymentPlan) -> Any: ...


@runtime_checkable
class KubernetesIntegration(Protocol):
    """Renders a plan into Kubernetes manifests. Declared only — no k8s here."""

    def render_manifests(self, plan: DeploymentPlan) -> Any: ...


@runtime_checkable
class TerraformIntegration(Protocol):
    """Renders a plan into Terraform configuration. Declared only — no Terraform here."""

    def render_configuration(self, plan: DeploymentPlan) -> Any: ...
