"""Product serialization (M7.0B-1).

Deterministic, adapter-neutral translation between the domain aggregates
(`ProductManifest`, `ProductDefinition`) and the plain dict shapes used for
authoring, storage, and diffing. Round-trippable; no I/O; stateless — safe to
share. Mirrors `skill.serializer.SkillSerializer`'s shape and conventions.

`manifest_from_dict` is the manifest *parser* the factory uses to load an authored
document; it is tolerant of omitted optional fields (defaults from the domain), but
it never invents identity — a missing `metadata`/`id` surfaces as an empty value
that `ProductValidator` then reports.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from .domain import (
    AgentRef,
    DeploymentMetadata,
    DeploymentStrategy,
    EnvironmentProfile,
    FeatureFlag,
    KnowledgeRef,
    MCPServerRef,
    ProductDefinition,
    ProductManifest,
    ProductMetadata,
    ProductVisibility,
    ProviderRef,
    SecretReference,
    SkillRef,
    TenantOverride,
    ToolRef,
    VersionMetadata,
    WorkflowRef,
)

SCHEMA_VERSION = "1"


def _ref_to_dict(r: Any) -> dict[str, Any]:
    return {
        "id": r.id,
        "namespace": r.namespace,
        "version": r.version,
        "config": dict(r.config),
    }


def _agent_to_dict(a: AgentRef) -> dict[str, Any]:
    d = _ref_to_dict(a)
    d["workflow_refs"] = list(a.workflow_refs)
    d["skill_refs"] = list(a.skill_refs)
    return d


def _agent_from_dict(d: Mapping[str, Any]) -> AgentRef:
    return AgentRef(
        id=d.get("id", ""),
        namespace=d.get("namespace", "internal"),
        version=d.get("version"),
        workflow_refs=tuple(d.get("workflow_refs", ())),
        skill_refs=tuple(d.get("skill_refs", ())),
        config=dict(d.get("config", {})),
    )


def _simple_ref_from_dict(cls: type, d: Mapping[str, Any]) -> Any:
    return cls(
        id=d.get("id", ""),
        namespace=d.get("namespace", "internal"),
        version=d.get("version"),
        config=dict(d.get("config", {})),
    )


def _secret_to_dict(s: SecretReference) -> dict[str, Any]:
    return {"ref": s.ref, "provider": s.provider, "path": s.path, "version": s.version}


def _secret_from_dict(d: Mapping[str, Any]) -> SecretReference:
    return SecretReference(
        ref=d.get("ref", ""),
        provider=d.get("provider", ""),
        path=d.get("path", ""),
        version=d.get("version"),
    )


def _environment_to_dict(e: EnvironmentProfile) -> dict[str, Any]:
    return {"name": e.name, "overrides": dict(e.overrides)}


def _tenant_to_dict(t: TenantOverride) -> dict[str, Any]:
    return {"tenant_id": t.tenant_id, "overrides": dict(t.overrides)}


def _flag_to_dict(f: FeatureFlag) -> dict[str, Any]:
    return {"key": f.key, "default": f.default, "description": f.description}


def _metadata_to_dict(m: ProductMetadata) -> dict[str, Any]:
    return {
        "id": m.id,
        "name": m.name,
        "version": m.version,
        "description": m.description,
        "owner": m.owner,
        "created_at": m.created_at,
        "labels": dict(m.labels),
    }


def _metadata_from_dict(d: Mapping[str, Any]) -> ProductMetadata:
    return ProductMetadata(
        id=d.get("id", ""),
        name=d.get("name", ""),
        version=d.get("version", "1.0.0"),
        description=d.get("description", ""),
        owner=d.get("owner", ""),
        created_at=d.get("created_at", ""),
        labels=dict(d.get("labels", {})),
    )


def _deployment_to_dict(dep: DeploymentMetadata) -> dict[str, Any]:
    return {
        "strategy": dep.strategy.value,
        "target_environments": list(dep.target_environments),
        "resource_limits": dict(dep.resource_limits),
    }


def _deployment_from_dict(d: Mapping[str, Any]) -> DeploymentMetadata:
    return DeploymentMetadata(
        strategy=DeploymentStrategy(d.get("strategy", DeploymentStrategy.ROLLING.value)),
        target_environments=tuple(d.get("target_environments", ())),
        resource_limits=dict(d.get("resource_limits", {})),
    )


def _version_meta_to_dict(v: VersionMetadata) -> dict[str, Any]:
    return {
        "requires_runtime": v.requires_runtime,
        "requires_execution": v.requires_execution,
        "rollback_to": v.rollback_to,
    }


def _version_meta_from_dict(d: Mapping[str, Any]) -> VersionMetadata:
    return VersionMetadata(
        requires_runtime=d.get("requires_runtime", ""),
        requires_execution=d.get("requires_execution", ""),
        rollback_to=d.get("rollback_to"),
    )


class ProductSerializer:
    """Stateless, deterministic (de)serializer for product aggregates."""

    # ── ProductManifest ───────────────────────────────────────────────────────
    def manifest_to_dict(self, m: ProductManifest) -> dict[str, Any]:
        return {
            "schema_version": SCHEMA_VERSION,
            "namespace": m.namespace,
            "interface_version": m.interface_version,
            "metadata": _metadata_to_dict(m.metadata),
            "agents": [_agent_to_dict(a) for a in m.agents],
            "workflows": [_ref_to_dict(w) for w in m.workflows],
            "skills": [_ref_to_dict(s) for s in m.skills],
            "tools": [_ref_to_dict(t) for t in m.tools],
            "providers": [_ref_to_dict(p) for p in m.providers],
            "mcp_servers": [_ref_to_dict(s) for s in m.mcp_servers],
            "knowledge": [_ref_to_dict(k) for k in m.knowledge],
            "environments": [_environment_to_dict(e) for e in m.environments],
            "tenants": [_tenant_to_dict(t) for t in m.tenants],
            "feature_flags": [_flag_to_dict(f) for f in m.feature_flags],
            "secrets": [_secret_to_dict(s) for s in m.secrets],
            "deployment": _deployment_to_dict(m.deployment),
            "version_metadata": _version_meta_to_dict(m.version_metadata),
            "visibility": m.visibility.value,
        }

    def manifest_from_dict(self, d: Mapping[str, Any]) -> ProductManifest:
        return ProductManifest(
            metadata=_metadata_from_dict(d.get("metadata", {})),
            namespace=d.get("namespace", "internal"),
            interface_version=d.get("interface_version", "1.0"),
            agents=tuple(_agent_from_dict(a) for a in d.get("agents", ())),
            workflows=tuple(_simple_ref_from_dict(WorkflowRef, w) for w in d.get("workflows", ())),
            skills=tuple(_simple_ref_from_dict(SkillRef, s) for s in d.get("skills", ())),
            tools=tuple(_simple_ref_from_dict(ToolRef, t) for t in d.get("tools", ())),
            providers=tuple(_simple_ref_from_dict(ProviderRef, p) for p in d.get("providers", ())),
            mcp_servers=tuple(
                _simple_ref_from_dict(MCPServerRef, s) for s in d.get("mcp_servers", ())
            ),
            knowledge=tuple(_simple_ref_from_dict(KnowledgeRef, k) for k in d.get("knowledge", ())),
            environments=tuple(
                EnvironmentProfile(name=e.get("name", ""), overrides=dict(e.get("overrides", {})))
                for e in d.get("environments", ())
            ),
            tenants=tuple(
                TenantOverride(
                    tenant_id=t.get("tenant_id", ""), overrides=dict(t.get("overrides", {}))
                )
                for t in d.get("tenants", ())
            ),
            feature_flags=tuple(
                FeatureFlag(
                    key=f.get("key", ""),
                    default=f.get("default", False),
                    description=f.get("description", ""),
                )
                for f in d.get("feature_flags", ())
            ),
            secrets=tuple(_secret_from_dict(s) for s in d.get("secrets", ())),
            deployment=_deployment_from_dict(d.get("deployment", {})),
            version_metadata=_version_meta_from_dict(d.get("version_metadata", {})),
            visibility=ProductVisibility(d.get("visibility", ProductVisibility.PRIVATE.value)),
        )

    # ── ProductDefinition ─────────────────────────────────────────────────────
    def definition_to_document(self, d: ProductDefinition) -> dict[str, Any]:
        return {
            "schema_version": SCHEMA_VERSION,
            "content_id": d.content_id,
            "content_hash": d.content_hash,
            "manifest": self.manifest_to_dict(d.manifest),
        }
