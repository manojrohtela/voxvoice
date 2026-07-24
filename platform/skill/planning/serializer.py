"""Planning serialization (M6.2B-2) — deterministic, round-trippable, no I/O.
Versioned (`schema_version`) so future fields are additive-only extension
points. Mirrors `tool.observability.serializer.ObservabilitySerializer`'s
shape and conventions.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from ..domain import CapabilityKind, SkillCapabilityRequirement
from .domain import SkillExecutionContext, SkillPlan, SkillPlanReplayRecord, SkillPlanStep

SCHEMA_VERSION = "1"


def _requirement_to_dict(r: SkillCapabilityRequirement) -> dict[str, Any]:
    return {"kind": r.kind.value, "tag": r.tag, "optional": r.optional}


def _requirement_from_dict(d: Mapping[str, Any]) -> SkillCapabilityRequirement:
    return SkillCapabilityRequirement(
        kind=CapabilityKind(d["kind"]), tag=d["tag"], optional=d.get("optional", False),
    )


def _step_to_dict(s: SkillPlanStep) -> dict[str, Any]:
    return {"namespace": s.namespace, "id": s.id, "version": s.version, "sequence": s.sequence}


def _step_from_dict(d: Mapping[str, Any]) -> SkillPlanStep:
    return SkillPlanStep(
        namespace=d["namespace"], id=d["id"], version=d["version"], sequence=d["sequence"],
    )


class PlanningSerializer:
    """Stateless, deterministic (de)serializer for planning aggregates."""

    # ── SkillPlan ─────────────────────────────────────────────────────────────
    def plan_to_dict(self, p: SkillPlan) -> dict[str, Any]:
        return {
            "schema_version": SCHEMA_VERSION, "plan_id": p.plan_id, "namespace": p.namespace,
            "id": p.id, "version": p.version, "steps": [_step_to_dict(s) for s in p.steps],
            "capability_requirements": [
                _requirement_to_dict(r) for r in p.capability_requirements
            ],
            "manifest_hash": p.manifest_hash, "dependency_hash": p.dependency_hash,
            "plan_version": p.plan_version, "planner_version": p.planner_version,
            "created_at": p.created_at,
        }

    def plan_from_dict(self, d: Mapping[str, Any]) -> SkillPlan:
        return SkillPlan(
            plan_id=d["plan_id"], namespace=d["namespace"], id=d["id"], version=d["version"],
            steps=tuple(_step_from_dict(s) for s in d.get("steps", ())),
            capability_requirements=tuple(
                _requirement_from_dict(r) for r in d.get("capability_requirements", ())
            ),
            manifest_hash=d.get("manifest_hash", ""), dependency_hash=d.get("dependency_hash", ""),
            plan_version=d.get("plan_version", "1"), planner_version=d.get("planner_version", "1"),
            created_at=d.get("created_at", 0.0),
        )

    # ── SkillExecutionContext ────────────────────────────────────────────────
    def context_to_dict(self, c: SkillExecutionContext) -> dict[str, Any]:
        return {
            "schema_version": SCHEMA_VERSION, "context_id": c.context_id, "plan_id": c.plan_id,
            "root": c.root, "namespace": c.namespace, "org_id": c.org_id,
            "tenant_id": c.tenant_id, "requested_by": c.requested_by,
            "created_at": c.created_at,
            "capability_requirements": [
                _requirement_to_dict(r) for r in c.capability_requirements
            ],
            "metadata": dict(c.metadata),
        }

    def context_from_dict(self, d: Mapping[str, Any]) -> SkillExecutionContext:
        return SkillExecutionContext(
            context_id=d["context_id"], plan_id=d["plan_id"], root=d["root"],
            namespace=d["namespace"], org_id=d.get("org_id", ""),
            tenant_id=d.get("tenant_id", ""), requested_by=d.get("requested_by", ""),
            created_at=d.get("created_at", 0.0),
            capability_requirements=tuple(
                _requirement_from_dict(r) for r in d.get("capability_requirements", ())
            ),
            metadata=dict(d.get("metadata", {})),
        )

    # ── SkillPlanReplayRecord ─────────────────────────────────────────────────
    def replay_record_to_document(self, r: SkillPlanReplayRecord) -> dict[str, Any]:
        return {
            "schema_version": SCHEMA_VERSION, "replay_id": r.replay_id,
            "plan_document": dict(r.plan_document), "context_document": dict(r.context_document),
            "replay_hash": r.replay_hash, "replay_version": r.replay_version,
            "replay_timestamp": r.replay_timestamp,
        }

    def replay_record_from_document(self, d: Mapping[str, Any]) -> SkillPlanReplayRecord:
        return SkillPlanReplayRecord(
            replay_id=d["replay_id"], plan_document=dict(d["plan_document"]),
            context_document=dict(d["context_document"]), replay_hash=d["replay_hash"],
            replay_version=d.get("replay_version", "1"),
            replay_timestamp=d.get("replay_timestamp", 0.0),
        )
