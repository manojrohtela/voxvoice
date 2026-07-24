"""Agent serialization (M6.5B-1).

Deterministic, adapter-neutral translation between the domain aggregates
(`AgentManifest`, `AgentDescriptor`) and the plain dict shapes used by
storage. Round-trippable; no I/O. Stateless — safe to share. Mirrors
`workflow.serializer.WorkflowSerializer`'s shape and conventions.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from .domain import AgentCapability, AgentDescriptor, AgentLifecycleState, AgentManifest

SCHEMA_VERSION = "1"


def _capability_to_dict(c: AgentCapability) -> dict[str, Any]:
    return {"kind": c.kind, "tag": c.tag, "required": c.required}


def _capability_from_dict(d: Mapping[str, Any]) -> AgentCapability:
    return AgentCapability(kind=d["kind"], tag=d["tag"], required=d.get("required", True))


class AgentSerializer:
    """Stateless, deterministic (de)serializer for agent aggregates."""

    # ── AgentManifest ─────────────────────────────────────────────────────────
    def manifest_to_dict(self, m: AgentManifest) -> dict[str, Any]:
        return {
            "schema_version": SCHEMA_VERSION, "id": m.id, "namespace": m.namespace,
            "version": m.version, "interface_version": m.interface_version,
            "display_name": m.display_name, "persona_prompt_id": m.persona_prompt_id,
            "capabilities": [_capability_to_dict(c) for c in m.capabilities],
            "default_memory_scope": m.default_memory_scope,
            "tags": sorted(m.tags), "owner": m.owner, "metadata": dict(m.metadata),
        }

    def manifest_from_dict(self, d: Mapping[str, Any]) -> AgentManifest:
        return AgentManifest(
            id=d["id"], namespace=d.get("namespace", "internal"),
            version=d.get("version", "1.0.0"),
            interface_version=d.get("interface_version", "1.0"),
            display_name=d.get("display_name", ""),
            persona_prompt_id=d.get("persona_prompt_id"),
            capabilities=tuple(
                _capability_from_dict(c) for c in d.get("capabilities", ())
            ),
            default_memory_scope=d.get("default_memory_scope", "session"),
            tags=frozenset(d.get("tags", ())), owner=d.get("owner", ""),
            metadata=dict(d.get("metadata", {})),
        )

    # ── AgentDescriptor (registration records) ───────────────────────────────
    def descriptor_to_document(self, desc: AgentDescriptor) -> dict[str, Any]:
        return {
            "schema_version": SCHEMA_VERSION, "manifest": self.manifest_to_dict(desc.manifest),
            "state": desc.state.value, "registered_at": desc.registered_at,
        }

    def descriptor_from_document(self, d: Mapping[str, Any]) -> AgentDescriptor:
        return AgentDescriptor(
            manifest=self.manifest_from_dict(d["manifest"]),
            state=AgentLifecycleState(d.get("state", AgentLifecycleState.DRAFT.value)),
            registered_at=d.get("registered_at", 0.0),
        )
