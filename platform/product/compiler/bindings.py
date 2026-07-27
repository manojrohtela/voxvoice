"""Immutable binding objects (M7.0B-2).

A binding is a manifest reference *resolved* into a concrete, immutable
`(kind, namespace, id, resolved_version)` plus its config and (for agents) its
declared composition edges. Resolution turns the M7.0B-1 inert reference
placeholders into these — it does NOT execute, initialize, or load anything
(ADR-0018/0019).

Following the certified `WorkflowNode` precedent (one data-only shape with a `kind`
discriminator rather than N near-identical classes — see the Workflow Runtime
design), there is exactly one immutable `Binding` shape. The seven names the
milestone calls for — `AgentBinding`, `WorkflowBinding`, `SkillBinding`,
`ToolBinding`, `ProviderBinding`, `MCPBinding`, `KnowledgeBinding` — are provided as
kind-specialized constructor helpers that build a `Binding`; only `AgentBinding`
accepts composition `edges`.

`binding_id` is a deterministic content identity over the resolved fields, so equal
resolved bindings share an id (duplicate detection) and authoring order never
affects it.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from product.canonical import canonical_form
from product.ids import content_hash


class BindingKind(StrEnum):
    AGENT = "agent"
    WORKFLOW = "workflow"
    SKILL = "skill"
    TOOL = "tool"
    PROVIDER = "provider"
    MCP = "mcp"
    KNOWLEDGE = "knowledge"


@dataclass(frozen=True, slots=True, order=True)
class BindingEdge:
    """A declared composition edge from an agent binding to another binding, by
    kind + target id. Resolved to a concrete target during dependency analysis —
    never executed."""

    kind: BindingKind
    target_id: str


@dataclass(frozen=True, slots=True)
class Binding:
    """One resolved reference — immutable, data only. No execute/resolve/load/bind
    surface (verified by test): a binding *is* the resolved data, nothing more."""

    kind: BindingKind
    namespace: str
    id: str
    resolved_version: str
    requested_version: str | None
    config: Mapping[str, Any] = field(default_factory=dict)
    edges: tuple[BindingEdge, ...] = ()
    binding_id: str = ""

    @property
    def key(self) -> tuple[str, str, str, str]:
        """The identity tuple (kind, namespace, id, resolved_version)."""
        return (self.kind.value, self.namespace, self.id, self.resolved_version)

    @property
    def ref(self) -> str:
        return f"{self.namespace}.{self.id}@{self.resolved_version}"


def _binding_id(
    kind: BindingKind,
    namespace: str,
    id: str,
    resolved_version: str,
    config: Mapping[str, Any],
    edges: tuple[BindingEdge, ...],
) -> str:
    config_json = json.dumps(canonical_form(dict(config)), sort_keys=True, separators=(",", ":"))
    edge_repr = ";".join(f"{e.kind.value}:{e.target_id}" for e in sorted(edges))
    return "bd_" + content_hash(kind.value, namespace, id, resolved_version, config_json, edge_repr)


def make_binding(
    kind: BindingKind,
    *,
    namespace: str,
    id: str,
    resolved_version: str,
    requested_version: str | None = None,
    config: Mapping[str, Any] | None = None,
    edges: Sequence[BindingEdge] = (),
) -> Binding:
    """Construct a `Binding` with a deterministic `binding_id`. The single place a
    binding id is computed."""
    cfg = dict(config or {})
    edge_tuple = tuple(edges)
    return Binding(
        kind=kind,
        namespace=namespace,
        id=id,
        resolved_version=resolved_version,
        requested_version=requested_version,
        config=cfg,
        edges=edge_tuple,
        binding_id=_binding_id(kind, namespace, id, resolved_version, cfg, edge_tuple),
    )


# ── kind-specialized constructor helpers (the milestone's named bindings) ─────────
def AgentBinding(*, edges: Sequence[BindingEdge] = (), **kw: Any) -> Binding:
    return make_binding(BindingKind.AGENT, edges=edges, **kw)


def WorkflowBinding(**kw: Any) -> Binding:
    return make_binding(BindingKind.WORKFLOW, **kw)


def SkillBinding(**kw: Any) -> Binding:
    return make_binding(BindingKind.SKILL, **kw)


def ToolBinding(**kw: Any) -> Binding:
    return make_binding(BindingKind.TOOL, **kw)


def ProviderBinding(**kw: Any) -> Binding:
    return make_binding(BindingKind.PROVIDER, **kw)


def MCPBinding(**kw: Any) -> Binding:
    return make_binding(BindingKind.MCP, **kw)


def KnowledgeBinding(**kw: Any) -> Binding:
    return make_binding(BindingKind.KNOWLEDGE, **kw)
