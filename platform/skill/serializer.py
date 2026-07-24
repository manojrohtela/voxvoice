"""Skill serialization (M6.2B-1).

Deterministic, adapter-neutral translation between the domain aggregates
(`SkillManifest`, `SkillDescriptor`) and the plain dict shapes used by
storage. Round-trippable; no I/O. Stateless — safe to share. Mirrors
`tool.serializer.ToolSerializer`'s shape and conventions.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from .domain import (
    CapabilityKind,
    SkillCapabilityRequirement,
    SkillDependency,
    SkillDescriptor,
    SkillLifecycleState,
    SkillManifest,
    SkillResource,
    SkillVisibility,
)

SCHEMA_VERSION = "1"


def _requirement_to_dict(r: SkillCapabilityRequirement) -> dict[str, Any]:
    return {"kind": r.kind.value, "tag": r.tag, "optional": r.optional}


def _requirement_from_dict(d: Mapping[str, Any]) -> SkillCapabilityRequirement:
    return SkillCapabilityRequirement(
        kind=CapabilityKind(d["kind"]), tag=d["tag"], optional=d.get("optional", False),
    )


def _dependency_to_dict(dep: SkillDependency) -> dict[str, Any]:
    return {"namespace": dep.namespace, "id": dep.id, "version": dep.version}


def _dependency_from_dict(d: Mapping[str, Any]) -> SkillDependency:
    return SkillDependency(namespace=d["namespace"], id=d["id"], version=d.get("version"))


def _resource_to_dict(res: SkillResource) -> dict[str, Any]:
    return {"uri": res.uri, "content_type": res.content_type, "content_hash": res.content_hash}


def _resource_from_dict(d: Mapping[str, Any]) -> SkillResource:
    return SkillResource(uri=d["uri"], content_type=d.get("content_type", ""),
                         content_hash=d.get("content_hash", ""))


class SkillSerializer:
    """Stateless, deterministic (de)serializer for skill aggregates."""

    # ── SkillManifest ─────────────────────────────────────────────────────────
    def manifest_to_dict(self, m: SkillManifest) -> dict[str, Any]:
        return {
            "schema_version": SCHEMA_VERSION, "id": m.id, "namespace": m.namespace,
            "version": m.version, "interface_version": m.interface_version,
            "description": m.description, "instructions": m.instructions,
            "required_capabilities": [_requirement_to_dict(r) for r in m.required_capabilities],
            "dependencies": [_dependency_to_dict(d) for d in m.dependencies],
            "resources": [_resource_to_dict(r) for r in m.resources],
            "visibility": m.visibility.value, "tags": sorted(m.tags), "owner": m.owner,
            "metadata": dict(m.metadata),
        }

    def manifest_from_dict(self, d: Mapping[str, Any]) -> SkillManifest:
        return SkillManifest(
            id=d["id"], namespace=d.get("namespace", "internal"),
            version=d.get("version", "1.0.0"),
            interface_version=d.get("interface_version", "1.0"),
            description=d.get("description", ""), instructions=d.get("instructions", ""),
            required_capabilities=frozenset(
                _requirement_from_dict(r) for r in d.get("required_capabilities", ())
            ),
            dependencies=tuple(_dependency_from_dict(x) for x in d.get("dependencies", ())),
            resources=tuple(_resource_from_dict(x) for x in d.get("resources", ())),
            visibility=SkillVisibility(d.get("visibility", SkillVisibility.PUBLIC.value)),
            tags=frozenset(d.get("tags", ())), owner=d.get("owner", ""),
            metadata=dict(d.get("metadata", {})),
        )

    # ── SkillDescriptor (registration records) ───────────────────────────────
    def descriptor_to_document(self, desc: SkillDescriptor) -> dict[str, Any]:
        return {
            "schema_version": SCHEMA_VERSION, "manifest": self.manifest_to_dict(desc.manifest),
            "state": desc.state.value, "registered_at": desc.registered_at,
            "dependency_closure": [list(k) for k in desc.dependency_closure],
        }

    def descriptor_from_document(self, d: Mapping[str, Any]) -> SkillDescriptor:
        return SkillDescriptor(
            manifest=self.manifest_from_dict(d["manifest"]),
            state=SkillLifecycleState(d.get("state", SkillLifecycleState.DRAFT.value)),
            registered_at=d.get("registered_at", 0.0),
            dependency_closure=tuple(
                tuple(k) for k in d.get("dependency_closure", ())
            ),
        )
