"""Deployment serialization (M7.0C) — deterministic, byte-identical rendering.

Renders the descriptor, report, and full plan to canonical plain dicts, and derives
the descriptor's canonical hash (which feeds the deployment identity). The descriptor
is pure config — no timestamps — so its hash and the deployment id are reproducible.
Stateless; no I/O.
"""

from __future__ import annotations

from typing import Any

from product.compiler.serializer import canonical_hash, diagnostics_to_list

from .descriptor import DeploymentDescriptor
from .plan import DeploymentPlan
from .report import DeploymentReport

SCHEMA_VERSION = "1"


def descriptor_to_dict(d: DeploymentDescriptor) -> dict[str, Any]:
    ident = d.package_identity
    rt = d.release_target
    return {
        "package_identity": {
            "manifest_id": ident.manifest_id,
            "definition_id": ident.definition_id,
            "compilation_id": ident.compilation_id,
            "package_id": ident.package_id,
        },
        "profile": d.profile.value,
        "tenancy": d.tenancy.value,
        "environment_profile": {
            "name": d.environment_profile.name,
            "overrides": dict(d.environment_profile.overrides),
        },
        "release_target": {"name": rt.name, "region": rt.region, "tenant_id": rt.tenant_id},
        "deployment_strategy": d.deployment_strategy.value,
        "rollout_policy": {
            "canary_percent": d.rollout_policy.canary_percent,
            "batch_size": d.rollout_policy.batch_size,
            "requires_approval": d.rollout_policy.requires_approval,
        },
        "rollback_policy": {
            "mode": d.rollback_policy.mode.value,
            "explicit_version": d.rollback_policy.explicit_version,
        },
        "resource_profile": dict(d.resource_profile),
        "feature_flags": dict(d.feature_flags),
        "tenant_overrides": {k: dict(v) for k, v in d.tenant_overrides.items()},
        "target_execution_version": d.target_execution_version,
        "target_runtime_version": d.target_runtime_version,
    }


def descriptor_hash(d: DeploymentDescriptor) -> str:
    return canonical_hash(descriptor_to_dict(d))


def report_to_dict(report: DeploymentReport) -> dict[str, Any]:
    s = report.summary
    c = report.compatibility
    r = report.rollback
    p = report.policy
    return {
        "schema_version": SCHEMA_VERSION,
        "deployment_id": report.deployment_id,
        "summary": {
            "status": s.status,
            "error_count": s.error_count,
            "warning_count": s.warning_count,
            "profile": s.profile.value,
            "tenancy": s.tenancy.value,
            "strategy": s.strategy.value,
        },
        "compatibility": {
            "compatible": c.compatible,
            "execution_compatible": c.execution_compatible,
            "runtime_compatible": c.runtime_compatible,
            "target_execution_version": c.target_execution_version,
            "target_runtime_version": c.target_runtime_version,
        },
        "rollback": {
            "mode": r.mode.value,
            "resolved_version": r.resolved_version,
            "valid": r.valid,
            "verifiable": r.verifiable,
        },
        "policy": {
            "strategy": p.strategy.value,
            "valid": p.valid,
            "requires_approval": p.requires_approval,
        },
        "diagnostics": diagnostics_to_list(report.diagnostics.diagnostics),
    }


class DeploymentSerializer:
    """Stateless, deterministic plan/descriptor/report serialization."""

    def descriptor_to_dict(self, d: DeploymentDescriptor) -> dict[str, Any]:
        return descriptor_to_dict(d)

    def report_to_dict(self, report: DeploymentReport) -> dict[str, Any]:
        return report_to_dict(report)

    def plan_to_dict(self, plan: DeploymentPlan) -> dict[str, Any]:
        ref = plan.package_ref
        ident = plan.identity
        return {
            "schema_version": SCHEMA_VERSION,
            "content_id": plan.content_id,
            "content_hash": plan.content_hash,
            "identity": {
                "manifest_id": ident.manifest_id,
                "definition_id": ident.definition_id,
                "compilation_id": ident.compilation_id,
                "package_id": ident.package_id,
                "deployment_id": ident.deployment_id,
            },
            "package_ref": {
                "package_id": ref.package_id,
                "content_hash": ref.content_hash,
                "qualified_name": ref.qualified_name,
                "package_version": ref.package_version,
            },
            "descriptor": descriptor_to_dict(plan.descriptor),
            "report": report_to_dict(plan.report),
        }
