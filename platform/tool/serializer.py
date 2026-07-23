"""Tool serialization (M6.1B-1).

Deterministic, adapter-neutral translation between the domain aggregates
(`ToolManifest`, `ToolRequest`, `ToolResult`) and the plain dict shapes used by
storage and events. Round-trippable; no I/O. Stateless — safe to share.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from .domain import (
    Classification,
    ExecutionState,
    ToolAttachment,
    ToolExecutionTrace,
    ToolManifest,
    ToolRequest,
    ToolResult,
    ToolUsage,
    TraceEvent,
    Visibility,
)


def _trace_to_dict(t: ToolExecutionTrace) -> list[dict[str, Any]]:
    return [{"stage": e.stage, "detail": e.detail, "seq": e.seq, "at": e.at} for e in t.events]


def _trace_from_dict(items: Any) -> ToolExecutionTrace:
    events = tuple(
        TraceEvent(stage=e["stage"], detail=e.get("detail", ""), seq=e.get("seq", 0),
                   at=e.get("at", 0.0))
        for e in (items or ())
    )
    return ToolExecutionTrace(events=events)


def _attachment_to_dict(a: ToolAttachment) -> dict[str, Any]:
    return {"uri": a.uri, "content_type": a.content_type, "content_hash": a.content_hash}


def _attachment_from_dict(d: Mapping[str, Any]) -> ToolAttachment:
    return ToolAttachment(uri=d["uri"], content_type=d.get("content_type", ""),
                          content_hash=d.get("content_hash", ""))


class ToolSerializer:
    """Stateless, deterministic (de)serializer for tool aggregates."""

    # ── ToolManifest ──────────────────────────────────────────────────────────
    def manifest_to_dict(self, m: ToolManifest) -> dict[str, Any]:
        return {
            "name": m.name, "namespace": m.namespace, "version": m.version,
            "description": m.description, "capabilities": sorted(m.capabilities),
            "inputs": dict(m.inputs), "outputs": dict(m.outputs),
            "permissions": sorted(m.permissions), "classification": m.classification.value,
            "tags": sorted(m.tags), "visibility": m.visibility.value, "owner": m.owner,
            "metadata": dict(m.metadata),
        }

    def manifest_from_dict(self, d: Mapping[str, Any]) -> ToolManifest:
        return ToolManifest(
            name=d["name"], namespace=d.get("namespace", "internal"),
            version=d.get("version", "1.0.0"), description=d.get("description", ""),
            capabilities=frozenset(d.get("capabilities", ())),
            inputs=dict(d.get("inputs", {})), outputs=dict(d.get("outputs", {})),
            permissions=frozenset(d.get("permissions", ())),
            classification=Classification(d.get("classification", Classification.READ_ONLY.value)),
            tags=frozenset(d.get("tags", ())),
            visibility=Visibility(d.get("visibility", Visibility.PUBLIC.value)),
            owner=d.get("owner", ""), metadata=dict(d.get("metadata", {})),
        )

    # ── ToolRequest ───────────────────────────────────────────────────────────
    def request_to_dict(self, r: ToolRequest) -> dict[str, Any]:
        return {
            "request_id": r.request_id, "tool": r.tool, "namespace": r.namespace,
            "version": r.version, "arguments": dict(r.arguments), "metadata": dict(r.metadata),
            "session_id": r.session_id, "trace_id": r.trace_id, "org_id": r.org_id,
            "request_hash": r.request_hash,
        }

    def request_from_dict(self, d: Mapping[str, Any]) -> ToolRequest:
        return ToolRequest(
            request_id=d["request_id"], tool=d["tool"], namespace=d.get("namespace", "internal"),
            version=d.get("version"), arguments=dict(d.get("arguments", {})),
            metadata=dict(d.get("metadata", {})), session_id=d.get("session_id", ""),
            trace_id=d.get("trace_id", ""), org_id=d.get("org_id"),
            request_hash=d.get("request_hash", ""),
        )

    # ── ToolResult ────────────────────────────────────────────────────────────
    def result_to_document(self, r: ToolResult) -> dict[str, Any]:
        return {
            "request_id": r.request_id, "state": r.state.value, "success": r.success,
            "output": r.output, "error": r.error, "classification": r.classification.value,
            "usage": {"duration_ms": r.usage.duration_ms, "units": r.usage.units},
            "trace": _trace_to_dict(r.trace),
            "attachments": [_attachment_to_dict(a) for a in r.attachments],
            "metadata": dict(r.metadata), "tool": r.tool, "namespace": r.namespace,
            "version": r.version, "request_hash": r.request_hash, "result_hash": r.result_hash,
            "created_at": r.created_at, "expires_at": r.expires_at,
            "session_id": r.session_id, "org_id": r.org_id,
        }

    def result_from_document(self, d: Mapping[str, Any]) -> ToolResult:
        u = d.get("usage", {})
        return ToolResult(
            request_id=d["request_id"],
            state=ExecutionState(d.get("state", ExecutionState.RECEIVED.value)),
            success=d.get("success", False), output=d.get("output"), error=d.get("error"),
            classification=Classification(d.get("classification", Classification.READ_ONLY.value)),
            usage=ToolUsage(duration_ms=u.get("duration_ms", 0.0), units=u.get("units", 0)),
            trace=_trace_from_dict(d.get("trace")),
            attachments=tuple(_attachment_from_dict(a) for a in d.get("attachments", ())),
            metadata=dict(d.get("metadata", {})), tool=d.get("tool", ""),
            namespace=d.get("namespace", "internal"), version=d.get("version", ""),
            request_hash=d.get("request_hash", ""), result_hash=d.get("result_hash", ""),
            created_at=d.get("created_at", 0.0), expires_at=d.get("expires_at"),
            session_id=d.get("session_id", ""), org_id=d.get("org_id"),
        )

    # ── event payload ─────────────────────────────────────────────────────────
    def result_event_payload(self, r: ToolResult, *, phase: str) -> dict[str, Any]:
        return {
            "phase": phase, "request_id": r.request_id, "tool": r.tool,
            "namespace": r.namespace, "version": r.version, "state": r.state.value,
            "success": r.success, "error": r.error, "result_hash": r.result_hash,
        }
