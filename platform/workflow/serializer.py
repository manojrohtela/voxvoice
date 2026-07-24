"""Workflow serialization (M6.4B-1).

Deterministic, adapter-neutral translation between the domain aggregates
(`WorkflowManifest`, `WorkflowDescriptor`) and the plain dict shapes used by
storage. Round-trippable; no I/O. Stateless — safe to share. Mirrors
`skill.serializer.SkillSerializer`'s shape and conventions.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from core.policies import RetryPolicy, TimeoutPolicy

from .domain import (
    WorkflowCondition,
    WorkflowConditionOperator,
    WorkflowDescriptor,
    WorkflowEdge,
    WorkflowFailurePolicy,
    WorkflowGraph,
    WorkflowLifecycleState,
    WorkflowManifest,
    WorkflowNode,
    WorkflowNodeKind,
)

SCHEMA_VERSION = "1"


def _condition_to_dict(c: WorkflowCondition | None) -> dict[str, Any] | None:
    if c is None:
        return None
    return {"field": c.field, "operator": c.operator.value, "value": c.value}


def _condition_from_dict(d: Mapping[str, Any] | None) -> WorkflowCondition | None:
    if d is None:
        return None
    return WorkflowCondition(
        field=d["field"], operator=WorkflowConditionOperator(d["operator"]), value=d.get("value"),
    )


def _retry_to_dict(r: RetryPolicy | None) -> dict[str, Any] | None:
    if r is None:
        return None
    return {
        "max_attempts": r.max_attempts, "base_delay_s": r.base_delay_s,
        "multiplier": r.multiplier, "max_delay_s": r.max_delay_s,
    }


def _retry_from_dict(d: Mapping[str, Any] | None) -> RetryPolicy | None:
    return RetryPolicy(**d) if d is not None else None


def _timeout_to_dict(t: TimeoutPolicy | None) -> dict[str, Any] | None:
    return {"seconds": t.seconds} if t is not None else None


def _timeout_from_dict(d: Mapping[str, Any] | None) -> TimeoutPolicy | None:
    return TimeoutPolicy(**d) if d is not None else None


def _node_to_dict(n: WorkflowNode) -> dict[str, Any]:
    return {
        "node_id": n.node_id, "kind": n.kind.value,
        "skill_ref": list(n.skill_ref) if n.skill_ref else None,
        "tool_ref": list(n.tool_ref) if n.tool_ref else None,
        "condition": _condition_to_dict(n.condition),
        "delay_ms": n.delay_ms, "event_name": n.event_name,
        "retry_policy": _retry_to_dict(n.retry_policy),
        "timeout_policy": _timeout_to_dict(n.timeout_policy),
        "failure_policy": n.failure_policy.value, "config": dict(n.config),
    }


def _node_from_dict(d: Mapping[str, Any]) -> WorkflowNode:
    return WorkflowNode(
        node_id=d["node_id"], kind=WorkflowNodeKind(d["kind"]),
        skill_ref=tuple(d["skill_ref"]) if d.get("skill_ref") else None,
        tool_ref=tuple(d["tool_ref"]) if d.get("tool_ref") else None,
        condition=_condition_from_dict(d.get("condition")),
        delay_ms=d.get("delay_ms"), event_name=d.get("event_name"),
        retry_policy=_retry_from_dict(d.get("retry_policy")),
        timeout_policy=_timeout_from_dict(d.get("timeout_policy")),
        failure_policy=WorkflowFailurePolicy(d.get("failure_policy", "propagate")),
        config=dict(d.get("config", {})),
    )


def _edge_to_dict(e: WorkflowEdge) -> dict[str, Any]:
    return {"source": e.source, "target": e.target, "condition": _condition_to_dict(e.condition)}


def _edge_from_dict(d: Mapping[str, Any]) -> WorkflowEdge:
    return WorkflowEdge(
        source=d["source"], target=d["target"], condition=_condition_from_dict(d.get("condition")),
    )


class WorkflowSerializer:
    """Stateless, deterministic (de)serializer for workflow aggregates."""

    # ── WorkflowGraph ─────────────────────────────────────────────────────────
    def graph_to_dict(self, g: WorkflowGraph) -> dict[str, Any]:
        return {
            "entry_node": g.entry_node, "nodes": [_node_to_dict(n) for n in g.nodes],
            "edges": [_edge_to_dict(e) for e in g.edges], "graph_hash": g.graph_hash,
        }

    def graph_from_dict(self, d: Mapping[str, Any]) -> WorkflowGraph:
        return WorkflowGraph(
            entry_node=d["entry_node"], nodes=tuple(_node_from_dict(n) for n in d.get("nodes", ())),
            edges=tuple(_edge_from_dict(e) for e in d.get("edges", ())),
            graph_hash=d.get("graph_hash", ""),
        )

    # ── WorkflowManifest ──────────────────────────────────────────────────────
    def manifest_to_dict(self, m: WorkflowManifest) -> dict[str, Any]:
        return {
            "schema_version": SCHEMA_VERSION, "id": m.id, "namespace": m.namespace,
            "version": m.version, "interface_version": m.interface_version,
            "description": m.description, "graph": self.graph_to_dict(m.graph),
            "tags": sorted(m.tags), "owner": m.owner, "metadata": dict(m.metadata),
        }

    def manifest_from_dict(self, d: Mapping[str, Any]) -> WorkflowManifest:
        return WorkflowManifest(
            id=d["id"], namespace=d.get("namespace", "internal"),
            version=d.get("version", "1.0.0"),
            interface_version=d.get("interface_version", "1.0"),
            description=d.get("description", ""), graph=self.graph_from_dict(d["graph"]),
            tags=frozenset(d.get("tags", ())), owner=d.get("owner", ""),
            metadata=dict(d.get("metadata", {})),
        )

    # ── WorkflowDescriptor (registration records) ────────────────────────────
    def descriptor_to_document(self, desc: WorkflowDescriptor) -> dict[str, Any]:
        return {
            "schema_version": SCHEMA_VERSION, "manifest": self.manifest_to_dict(desc.manifest),
            "state": desc.state.value, "registered_at": desc.registered_at,
        }

    def descriptor_from_document(self, d: Mapping[str, Any]) -> WorkflowDescriptor:
        return WorkflowDescriptor(
            manifest=self.manifest_from_dict(d["manifest"]),
            state=WorkflowLifecycleState(d.get("state", WorkflowLifecycleState.DRAFT.value)),
            registered_at=d.get("registered_at", 0.0),
        )
