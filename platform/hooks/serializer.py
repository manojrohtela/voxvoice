"""Hook serialization (M6.3B).

Deterministic, adapter-neutral translation between the domain aggregates and
the plain dict shapes used by storage. Round-trippable; no I/O. Stateless —
safe to share. Mirrors `skill.serializer.SkillSerializer`'s shape.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from .domain import (
    HookCapabilityRequirement,
    HookCategory,
    HookDescriptor,
    HookEvent,
    HookFailure,
    HookLifecycleState,
    HookManifest,
    HookOutcome,
    HookPoint,
    HookReplayRecord,
    HookRunResult,
    HookRuntimeScope,
    HookVerdict,
)

SCHEMA_VERSION = "1"


def _requirement_to_dict(r: HookCapabilityRequirement) -> dict[str, Any]:
    return {"field": r.field, "optional": r.optional}


def _requirement_from_dict(d: Mapping[str, Any]) -> HookCapabilityRequirement:
    return HookCapabilityRequirement(field=d["field"], optional=d.get("optional", False))


def _verdict_to_dict(v: HookVerdict) -> dict[str, Any]:
    return {
        "outcome": v.outcome.value, "hook": v.hook,
        "modified_payload_hash": v.modified_payload_hash, "reason": v.reason,
        "metadata": dict(v.metadata),
    }


def _verdict_from_dict(d: Mapping[str, Any]) -> HookVerdict:
    return HookVerdict(
        outcome=HookOutcome(d["outcome"]), hook=d.get("hook", ""),
        modified_payload_hash=d.get("modified_payload_hash"), reason=d.get("reason", ""),
        metadata=dict(d.get("metadata", {})),
    )


def _failure_to_dict(f: HookFailure) -> dict[str, Any]:
    return {"hook": f.hook, "error_type": f.error_type, "message": f.message}


def _failure_from_dict(d: Mapping[str, Any]) -> HookFailure:
    return HookFailure(hook=d["hook"], error_type=d["error_type"], message=d.get("message", ""))


class HookSerializer:
    """Stateless, deterministic (de)serializer for hook aggregates."""

    # ── HookManifest ──────────────────────────────────────────────────────────
    def manifest_to_dict(self, m: HookManifest) -> dict[str, Any]:
        return {
            "schema_version": SCHEMA_VERSION, "id": m.id, "namespace": m.namespace,
            "version": m.version, "interface_version": m.interface_version,
            "description": m.description, "points": sorted(p.value for p in m.points),
            "scope": sorted(s.value for s in m.scope), "priority": m.priority,
            "category": m.category.value if m.category is not None else None,
            "required_context": [_requirement_to_dict(r) for r in m.required_context],
            "tags": sorted(m.tags), "owner": m.owner, "metadata": dict(m.metadata),
        }

    def manifest_from_dict(self, d: Mapping[str, Any]) -> HookManifest:
        return HookManifest(
            id=d["id"], namespace=d.get("namespace", "internal"),
            version=d.get("version", "1.0.0"), interface_version=d.get("interface_version", "1.0"),
            description=d.get("description", ""),
            points=frozenset(HookPoint(p) for p in d.get("points", ())),
            scope=frozenset(HookRuntimeScope(s) for s in d.get("scope", ())),
            priority=d.get("priority", 0),
            category=HookCategory(d["category"]) if d.get("category") else None,
            required_context=tuple(
                _requirement_from_dict(r) for r in d.get("required_context", ())
            ),
            tags=frozenset(d.get("tags", ())), owner=d.get("owner", ""),
            metadata=dict(d.get("metadata", {})),
        )

    # ── HookDescriptor (registration records) ────────────────────────────────
    def descriptor_to_document(self, desc: HookDescriptor) -> dict[str, Any]:
        return {
            "schema_version": SCHEMA_VERSION, "manifest": self.manifest_to_dict(desc.manifest),
            "state": desc.state.value, "registered_at": desc.registered_at,
            "sequence": desc.sequence,
        }

    def descriptor_from_document(self, d: Mapping[str, Any]) -> HookDescriptor:
        return HookDescriptor(
            manifest=self.manifest_from_dict(d["manifest"]),
            state=HookLifecycleState(d.get("state", HookLifecycleState.DRAFT.value)),
            registered_at=d.get("registered_at", 0.0), sequence=d.get("sequence", 0),
        )

    # ── HookEvent ─────────────────────────────────────────────────────────────
    def event_to_dict(self, e: HookEvent) -> dict[str, Any]:
        return {
            "schema_version": SCHEMA_VERSION, "event_id": e.event_id, "point": e.point.value,
            "scope": e.scope.value, "session_id": e.session_id, "trace_id": e.trace_id,
            "org_id": e.org_id, "workspace_id": e.workspace_id, "payload_hash": e.payload_hash,
            "created_at": e.created_at, "metadata": dict(e.metadata),
        }

    def event_from_dict(self, d: Mapping[str, Any]) -> HookEvent:
        return HookEvent(
            event_id=d["event_id"], point=HookPoint(d["point"]), scope=HookRuntimeScope(d["scope"]),
            session_id=d.get("session_id", ""), trace_id=d.get("trace_id", ""),
            org_id=d.get("org_id"), workspace_id=d.get("workspace_id"),
            payload_hash=d.get("payload_hash", ""), created_at=d.get("created_at", 0.0),
            metadata=dict(d.get("metadata", {})),
        )

    # ── HookRunResult ─────────────────────────────────────────────────────────
    def run_result_to_dict(self, r: HookRunResult) -> dict[str, Any]:
        return {
            "schema_version": SCHEMA_VERSION, "point": r.point.value, "scope": r.scope.value,
            "verdicts": [_verdict_to_dict(v) for v in r.verdicts],
            "failures": [_failure_to_dict(f) for f in r.failures],
            "aggregated_outcome": r.aggregated_outcome.value, "aggregation": r.aggregation,
            "created_at": r.created_at,
        }

    def run_result_from_dict(self, d: Mapping[str, Any]) -> HookRunResult:
        return HookRunResult(
            point=HookPoint(d["point"]), scope=HookRuntimeScope(d["scope"]),
            verdicts=tuple(_verdict_from_dict(v) for v in d.get("verdicts", ())),
            failures=tuple(_failure_from_dict(f) for f in d.get("failures", ())),
            aggregated_outcome=HookOutcome(d.get("aggregated_outcome", HookOutcome.ALLOW.value)),
            aggregation=d.get("aggregation", ""), created_at=d.get("created_at", 0.0),
        )

    # ── HookReplayRecord ──────────────────────────────────────────────────────
    def replay_record_to_document(self, r: HookReplayRecord) -> dict[str, Any]:
        return {
            "schema_version": SCHEMA_VERSION, "replay_id": r.replay_id,
            "event_document": dict(r.event_document), "result_document": dict(r.result_document),
            "replay_hash": r.replay_hash, "replay_version": r.replay_version,
            "replay_timestamp": r.replay_timestamp,
        }

    def replay_record_from_document(self, d: Mapping[str, Any]) -> HookReplayRecord:
        return HookReplayRecord(
            replay_id=d["replay_id"], event_document=dict(d["event_document"]),
            result_document=dict(d["result_document"]), replay_hash=d["replay_hash"],
            replay_version=d.get("replay_version", "1"),
            replay_timestamp=d.get("replay_timestamp", 0.0),
        )
