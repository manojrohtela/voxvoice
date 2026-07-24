"""Execution serialization (M6.4B-2).

Deterministic, adapter-neutral translation between the execution domain
aggregates and the plain dict shapes used by durable storage.
Round-trippable; no I/O. Stateless — safe to share.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from .domain import (
    WorkflowCheckpoint,
    WorkflowExecution,
    WorkflowExecutionContext,
    WorkflowExecutionResult,
    WorkflowExecutionState,
    WorkflowReplayRecord,
    WorkflowSnapshot,
)

SCHEMA_VERSION = "1"


class WorkflowExecutionSerializer:
    """Stateless, deterministic (de)serializer for execution aggregates."""

    # ── WorkflowExecution ─────────────────────────────────────────────────────
    def execution_to_dict(self, e: WorkflowExecution) -> dict[str, Any]:
        return {
            "schema_version": SCHEMA_VERSION, "execution_id": e.execution_id,
            "namespace": e.namespace, "workflow_id": e.workflow_id, "version": e.version,
            "graph_hash": e.graph_hash, "state": e.state.value,
            "started_at": e.started_at, "updated_at": e.updated_at,
        }

    def execution_from_dict(self, d: Mapping[str, Any]) -> WorkflowExecution:
        return WorkflowExecution(
            execution_id=d["execution_id"], namespace=d["namespace"],
            workflow_id=d["workflow_id"], version=d["version"], graph_hash=d["graph_hash"],
            state=WorkflowExecutionState(d.get("state", WorkflowExecutionState.PENDING.value)),
            started_at=d.get("started_at", 0.0), updated_at=d.get("updated_at", 0.0),
        )

    # ── WorkflowExecutionContext ──────────────────────────────────────────────
    def context_to_dict(self, c: WorkflowExecutionContext) -> dict[str, Any]:
        return {
            "schema_version": SCHEMA_VERSION, "context_id": c.context_id, "plan_id": c.plan_id,
            "root": c.root, "namespace": c.namespace, "org_id": c.org_id,
            "tenant_id": c.tenant_id, "requested_by": c.requested_by, "created_at": c.created_at,
            "metadata": dict(c.metadata), "execution_id": c.execution_id,
            "graph_hash": c.graph_hash, "current_node_id": c.current_node_id,
            "checkpoint_id": c.checkpoint_id, "retry_count": c.retry_count,
        }

    def context_from_dict(self, d: Mapping[str, Any]) -> WorkflowExecutionContext:
        return WorkflowExecutionContext(
            context_id=d["context_id"], plan_id=d.get("plan_id", ""), root=d.get("root", ""),
            namespace=d.get("namespace", "internal"), org_id=d.get("org_id", ""),
            tenant_id=d.get("tenant_id", ""), requested_by=d.get("requested_by", ""),
            created_at=d.get("created_at", 0.0), metadata=dict(d.get("metadata", {})),
            execution_id=d.get("execution_id", ""), graph_hash=d.get("graph_hash", ""),
            current_node_id=d.get("current_node_id", ""), checkpoint_id=d.get("checkpoint_id"),
            retry_count=d.get("retry_count", 0),
        )

    # ── WorkflowCheckpoint ────────────────────────────────────────────────────
    def checkpoint_to_dict(self, c: WorkflowCheckpoint) -> dict[str, Any]:
        return {
            "schema_version": SCHEMA_VERSION, "checkpoint_id": c.checkpoint_id,
            "execution_id": c.execution_id, "node_id": c.node_id, "sequence": c.sequence,
            "variables_hash": c.variables_hash, "checkpoint_hash": c.checkpoint_hash,
            "created_at": c.created_at,
        }

    def checkpoint_from_dict(self, d: Mapping[str, Any]) -> WorkflowCheckpoint:
        return WorkflowCheckpoint(
            checkpoint_id=d["checkpoint_id"], execution_id=d["execution_id"], node_id=d["node_id"],
            sequence=d["sequence"], variables_hash=d["variables_hash"],
            checkpoint_hash=d["checkpoint_hash"], created_at=d.get("created_at", 0.0),
        )

    # ── WorkflowSnapshot ──────────────────────────────────────────────────────
    def snapshot_to_dict(self, s: WorkflowSnapshot) -> dict[str, Any]:
        return {
            "schema_version": SCHEMA_VERSION, "execution_id": s.execution_id,
            "current_node": s.current_node, "variables": dict(s.variables),
            "node_history": list(s.node_history), "active_nodes": list(s.active_nodes),
            "last_checkpoint": self.checkpoint_to_dict(s.last_checkpoint)
            if s.last_checkpoint else None,
            "pending_error": s.pending_error,
        }

    def snapshot_from_dict(self, d: Mapping[str, Any]) -> WorkflowSnapshot:
        return WorkflowSnapshot(
            execution_id=d["execution_id"], current_node=d["current_node"],
            variables=dict(d.get("variables", {})),
            pending_error=d.get("pending_error"),
            node_history=tuple(d.get("node_history", ())),
            active_nodes=tuple(d.get("active_nodes", ())),
            last_checkpoint=self.checkpoint_from_dict(d["last_checkpoint"])
            if d.get("last_checkpoint") else None,
        )

    # ── WorkflowExecutionResult ───────────────────────────────────────────────
    def result_to_dict(self, r: WorkflowExecutionResult) -> dict[str, Any]:
        return {
            "schema_version": SCHEMA_VERSION, "execution_id": r.execution_id,
            "final_state": r.final_state.value, "variables": dict(r.variables),
            "completed_nodes": list(r.completed_nodes), "partial_success": r.partial_success,
            "error": r.error, "completed_at": r.completed_at, "duration_ms": r.duration_ms,
        }

    def result_from_dict(self, d: Mapping[str, Any]) -> WorkflowExecutionResult:
        return WorkflowExecutionResult(
            execution_id=d["execution_id"],
            final_state=WorkflowExecutionState(d["final_state"]),
            variables=dict(d.get("variables", {})),
            completed_nodes=tuple(d.get("completed_nodes", ())),
            partial_success=d.get("partial_success", False), error=d.get("error"),
            completed_at=d.get("completed_at", 0.0), duration_ms=d.get("duration_ms", 0.0),
        )

    # ── WorkflowReplayRecord ──────────────────────────────────────────────────
    def replay_record_to_document(self, r: WorkflowReplayRecord) -> dict[str, Any]:
        return {
            "schema_version": SCHEMA_VERSION, "replay_id": r.replay_id,
            "checkpoints_document": [dict(c) for c in r.checkpoints_document],
            "result_document": dict(r.result_document), "replay_hash": r.replay_hash,
            "replay_version": r.replay_version, "replay_timestamp": r.replay_timestamp,
        }

    def replay_record_from_document(self, d: Mapping[str, Any]) -> WorkflowReplayRecord:
        return WorkflowReplayRecord(
            replay_id=d["replay_id"],
            checkpoints_document=tuple(d.get("checkpoints_document", ())),
            result_document=dict(d["result_document"]), replay_hash=d["replay_hash"],
            replay_version=d.get("replay_version", "1"),
            replay_timestamp=d.get("replay_timestamp", 0.0),
        )
