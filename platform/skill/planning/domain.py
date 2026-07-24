"""Skill planning domain model (M6.2B-2) — deterministic, offline value objects.

Mirrors the established `tool.observability` projection pattern: ordered,
metadata-only aggregates built *from* already-registered `SkillDescriptor`s,
never the other way around. A `SkillPlan` is a **structural** artifact — a
deterministic dependency-execution order plus the skill's *declared* capability
requirements — never a resolved or executed one (§8/§9 of the M6.2A design:
capability resolution and Skill/Tool execution are never this bundle's job).
Everything is frozen + slotted.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum

from ..domain import SkillCapabilityRequirement

__all__ = [
    "SkillPlanStep",
    "SkillPlan",
    "SkillExecutionContext",
    "SkillPolicyVerdict",
    "SkillPlanningTrace",
    "SkillPlanReplayRecord",
    "ReplayComparison",
    "PlanningDiagnostics",
    "PlanningStatistics",
]


@dataclass(frozen=True, slots=True)
class SkillPlanStep:
    """One node in the deterministic, leaf-first dependency execution order.
    `sequence` is this step's 0-based position in that order — the *last* step
    is always the plan's own root skill."""

    namespace: str
    id: str
    version: str
    sequence: int

    @property
    def qualified_name(self) -> str:
        return f"{self.namespace}.{self.id}"


@dataclass(frozen=True, slots=True)
class SkillPlan:
    """An immutable, deterministic execution plan for one registered skill —
    structural only: a dependency-ordered step sequence plus the root's
    *declared* (never resolved) capability requirements. Never references a
    live registry/resolver; safe to serialize and replay."""

    plan_id: str
    namespace: str
    id: str
    version: str
    steps: tuple[SkillPlanStep, ...] = ()
    capability_requirements: tuple[SkillCapabilityRequirement, ...] = ()
    manifest_hash: str = ""
    dependency_hash: str = ""
    plan_version: str = "1"
    planner_version: str = "1"
    created_at: float = 0.0

    @property
    def qualified_name(self) -> str:
        return f"{self.namespace}.{self.id}"


@dataclass(frozen=True, slots=True)
class SkillExecutionContext:
    """The canonical, provider-neutral, transport-neutral execution contract a
    future Workflow Runtime / Agent Runtime / MCP Runtime / A2A Runtime
    consumes to actually run a plan. Pure data — no behavior, no live
    references, no capability resolution. This bundle only ever *constructs*
    this shape; nothing in `skill/` ever consumes it to execute anything."""

    context_id: str
    plan_id: str
    root: str  # "namespace.id@version"
    namespace: str
    org_id: str = ""
    tenant_id: str = ""
    requested_by: str = ""
    created_at: float = 0.0
    capability_requirements: tuple[SkillCapabilityRequirement, ...] = ()
    metadata: Mapping[str, str] = field(default_factory=dict)


class SkillPolicyVerdict(StrEnum):
    """The only two outcomes a `SkillPolicyEngine.authorize(...)` may reach.
    Mirrors `tool.extensions.PolicyVerdict`'s shape, duplicated (not imported)
    per the "skill never imports tool" constraint."""

    ALLOW = "allow"
    DENY = "deny"


@dataclass(frozen=True, slots=True)
class SkillPlanningTrace:
    """A read-side observability record of one planning pass — built from an
    already-computed `SkillPlan`, never influencing it."""

    trace_id: str
    plan_id: str
    qualified_name: str
    step_count: int
    capability_count: int
    created_at: float = 0.0
    metadata: Mapping[str, str] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class SkillPlanReplayRecord:
    """Everything needed to deterministically re-verify one planning pass
    later: the serialized plan + execution context, and the hash chain
    computed over them at record time — never a live registry reference."""

    replay_id: str
    plan_document: Mapping[str, object]
    context_document: Mapping[str, object]
    replay_hash: str
    replay_version: str = "1"
    replay_timestamp: float = 0.0


@dataclass(frozen=True, slots=True)
class ReplayComparison:
    """The outcome of one planning-replay validation/comparison. `mismatches`
    names the fields that disagreed (empty when `matches`). Mirrors
    `tool.observability.domain.ReplayComparison`'s shape exactly."""

    matches: bool
    expected_hash: str
    actual_hash: str
    mismatches: tuple[str, ...] = ()
    reason: str = ""


@dataclass(frozen=True, slots=True)
class PlanningDiagnostics:
    """A read-only snapshot of the planning subsystem. Never mutates the
    registry/repository it inspects."""

    runtime_version: str
    config_fingerprint: str
    registry_stats: Mapping[str, object] = field(default_factory=dict)
    planning_stats: Mapping[str, object] = field(default_factory=dict)
    replay_stats: Mapping[str, object] = field(default_factory=dict)
    health_summary: str = "unknown"


@dataclass(frozen=True, slots=True)
class PlanningStatistics:
    """A point-in-time aggregation over already-computed `SkillPlan`s. Pure
    aggregation, no I/O."""

    per_root: dict[str, int] = field(default_factory=dict)
    per_namespace: dict[str, int] = field(default_factory=dict)
    plan_count: int = 0
    avg_steps: float = 0.0
    avg_capabilities: float = 0.0
