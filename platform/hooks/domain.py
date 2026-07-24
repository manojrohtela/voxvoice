"""Hook Framework domain model (M6.3B) — immutable value objects.

Per the approved M6.3A design: a `HookManifest` (the versioned, diffable
**contract only** — never executable code, mirroring the Tool/Skill manifest
precedent) and the runtime-loaded `HookDescriptor` (manifest + lifecycle
bookkeeping + the registry-assigned `sequence`, the deterministic ordering
tie-break). `HookCategory` is descriptive manifest metadata ONLY — it must
never affect ordering, filtering, execution policy, or aggregation (enforced
by construction: no module outside `validation.py` even reads it).

A Hook produces exactly one `HookVerdict` (ALLOW/DENY/MODIFY) per event; it
never executes a Tool, never plans a Skill, never mutates runtime state.
`MODIFY` carries a content-hash payload reference plus metadata only — never
inline mutation logic (§ Constraints).

Exactly ONE lifecycle state exists (`HookLifecycleState`) — mirrors Skill
Runtime's own single-FSM asymmetry from Tool Runtime, since a hook never
executes a multi-phase action.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from enum import StrEnum
from typing import Any


class HookPoint(StrEnum):
    """Deterministic, named lifecycle points. Additive only (AR-32)."""

    BEFORE_PLAN = "before_plan"
    AFTER_PLAN = "after_plan"
    BEFORE_TOOL_CALL = "before_tool_call"
    AFTER_TOOL_CALL = "after_tool_call"
    BEFORE_HANDOFF = "before_handoff"
    AFTER_HANDOFF = "after_handoff"
    BEFORE_WORKFLOW_STEP = "before_workflow_step"
    AFTER_WORKFLOW_STEP = "after_workflow_step"
    ON_LOOP_ITERATION = "on_loop_iteration"
    ON_ERROR = "on_error"


class HookRuntimeScope(StrEnum):
    """Which runtime(s) a hook is declared to attach to — a pure filter tag,
    never a live import (the Hook Framework does not know what `TOOL` *is*,
    only that a caller invoking with `scope=TOOL` wants hooks tagged for it)."""

    TOOL = "tool"
    SKILL = "skill"
    WORKFLOW = "workflow"
    AGENT = "agent"
    ANY = "any"  # attaches regardless of the calling runtime's scope


class HookCategory(StrEnum):
    """Descriptive manifest metadata ONLY. Must never influence ordering,
    filtering, execution policy, or verdict aggregation — a category is a
    classification for humans/tooling, not an execution signal."""

    VALIDATION = "validation"
    TRANSFORMATION = "transformation"
    AUDIT = "audit"
    NOTIFICATION = "notification"
    METRICS = "metrics"


class HookLifecycleState(StrEnum):
    DRAFT = "draft"
    REGISTERED = "registered"
    ACTIVE = "active"
    DEPRECATED = "deprecated"
    RETIRED = "retired"


class HookOutcome(StrEnum):
    ALLOW = "allow"
    DENY = "deny"
    MODIFY = "modify"


@dataclass(frozen=True, slots=True)
class HookCapabilityRequirement:
    """A declared, structural need (e.g. "requires org_id present") — never
    resolved or enforced by the Framework itself; capability resolution is
    out of scope for this bundle and every future one (§ Constraints)."""

    field: str
    optional: bool = False


@dataclass(frozen=True, slots=True)
class HookManifest:
    """The versioned, diffable **contract** for one hook — never executable
    code. The concrete callback (`Hook`) is a separately-injected object,
    registered alongside this manifest, never resolved from a string."""

    id: str
    namespace: str = "internal"
    version: str = "1.0.0"
    interface_version: str = "1.0"
    description: str = ""
    points: frozenset[HookPoint] = field(default_factory=frozenset)
    scope: frozenset[HookRuntimeScope] = field(default_factory=frozenset)
    priority: int = 0  # explicit, declared — never inferred (ordering.py)
    category: HookCategory | None = None  # descriptive only, see class docstring
    required_context: tuple[HookCapabilityRequirement, ...] = ()
    tags: frozenset[str] = field(default_factory=frozenset)
    owner: str = ""
    metadata: Mapping[str, str] = field(default_factory=dict)

    @property
    def qualified_name(self) -> str:
        return f"{self.namespace}.{self.id}"


@dataclass(frozen=True, slots=True)
class HookDescriptor:
    """Loaded runtime metadata: the immutable `HookManifest` plus
    registry-assigned lifecycle bookkeeping. `sequence` is the monotonic
    registration-order counter the registry assigns once, at registration —
    the ordering model's tie-break (§ Ordering)."""

    manifest: HookManifest
    state: HookLifecycleState = HookLifecycleState.DRAFT
    registered_at: float = 0.0
    sequence: int = 0

    @property
    def qualified_name(self) -> str:
        return self.manifest.qualified_name

    @property
    def version(self) -> str:
        return self.manifest.version

    def with_state(self, state: HookLifecycleState) -> HookDescriptor:
        return replace(self, state=state)


@dataclass(frozen=True, slots=True)
class HookEvent:
    """The immutable, provider-neutral, transport-neutral shape passed to
    `Hook.evaluate`. Inline session/trace/org/workspace fields — no
    `tenant.TenantContext`/`pipeline.RequestContext` import, identical
    precedent to `ToolRequest`/`SkillExecutionContext`. `payload_hash` is a
    content-hash reference to the typed, already-validated payload — never a
    raw credential or unvalidated blob."""

    event_id: str
    point: HookPoint
    scope: HookRuntimeScope
    session_id: str = ""
    trace_id: str = ""
    org_id: str | None = None
    workspace_id: str | None = None
    payload_hash: str = ""
    created_at: float = 0.0
    metadata: Mapping[str, str] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class HookVerdict:
    """One hook's decision. A `MODIFY` outcome carries only a content-hash
    reference (`modified_payload_hash`) plus `metadata` — never inline
    mutation logic. Applying a modification is always the calling runtime's
    own responsibility, never this Framework's."""

    outcome: HookOutcome
    hook: str = ""  # qualified_name of the producing hook
    modified_payload_hash: str | None = None
    reason: str = ""
    metadata: Mapping[str, str] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class HookFailure:
    """An isolated hook-evaluation failure — always recorded, regardless of
    `HookFailureMode`, so a `FAIL_OPEN` point never silently swallows a real
    integration bug (§ Execution Policy)."""

    hook: str
    error_type: str
    message: str = ""


@dataclass(frozen=True, slots=True)
class HookRunResult:
    """The outcome of one `HookEngine.run` call: every collected verdict,
    every isolated failure, and the aggregated outcome for the point."""

    point: HookPoint
    scope: HookRuntimeScope
    verdicts: tuple[HookVerdict, ...] = ()
    failures: tuple[HookFailure, ...] = ()
    aggregated_outcome: HookOutcome = HookOutcome.ALLOW
    aggregation: str = ""
    created_at: float = 0.0


@dataclass(frozen=True, slots=True)
class HookExecutionTrace:
    """A read-side observability record of one `HookRunResult` — built from
    an already-finished run, never influencing it."""

    trace_id: str
    event_id: str
    point: str
    scope: str
    hook_count: int
    failure_count: int
    aggregated_outcome: str
    created_at: float = 0.0
    metadata: Mapping[str, str] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class HookReplayRecord:
    """Everything needed to deterministically re-verify one hook run later:
    the serialized event + result, and the hash computed over them at record
    time — never a live registry/hook reference."""

    replay_id: str
    event_document: Mapping[str, object]
    result_document: Mapping[str, object]
    replay_hash: str
    replay_version: str = "1"
    replay_timestamp: float = 0.0


@dataclass(frozen=True, slots=True)
class ReplayComparison:
    matches: bool
    expected_hash: str
    actual_hash: str
    mismatches: tuple[str, ...] = ()
    reason: str = ""


@dataclass(frozen=True, slots=True)
class HookRuntimeDiagnostics:
    runtime_version: str
    config_fingerprint: str
    registry_stats: Mapping[str, object] = field(default_factory=dict)
    execution_stats: Mapping[str, object] = field(default_factory=dict)
    replay_stats: Mapping[str, object] = field(default_factory=dict)
    health_summary: str = "unknown"


@dataclass(frozen=True, slots=True)
class HookRuntimeStatistics:
    per_point: dict[str, int] = field(default_factory=dict)
    per_scope: dict[str, int] = field(default_factory=dict)
    per_outcome: dict[str, int] = field(default_factory=dict)
    run_count: int = 0
    failure_count: int = 0


@dataclass(frozen=True, slots=True)
class HookStatistics:
    """A point-in-time snapshot: counters + registered hooks + repository size."""

    name: str
    state: str
    healthy: bool
    hooks: tuple[str, ...] = ()
    registrations: int = 0
    metrics: dict[str, Any] = field(default_factory=dict)
