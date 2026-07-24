"""Observability domain model (M6.1B-3) — deterministic, offline value objects.

Mirrors the established Epic 5 trace pattern (`provider.ProviderExecutionTrace`,
`pipeline.PipelineExecutionTrace`/`PipelineExecutionSnapshot`): ordered, metadata-
only aggregates that never change execution or governance behavior — they are
built *from* an already-finished `ToolResult`/`GovernanceDecision`, never the other
way around. Everything is frozen + slotted. No raw sensitive payloads are ever
stored — arguments/output are captured as content hashes only (`tool.ids.content_hash`).
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum

from ..extensions import PolicyVerdict
from ..governance.domain import ApprovalState, Effect

__all__ = [
    "ReasonCode",
    "SourceLevel",
    "DecisionReason",
    "ToolGovernanceDecisionTrace",
    "ExecutionTrace",
    "ReplayRecord",
    "ReplayComparison",
    "FailureCategory",
    "FailureAnalysis",
    "ToolAuditRecord",
]


class ReasonCode(StrEnum):
    ALLOWED = "allowed"
    TENANT_DENIED = "tenant_denied"
    POLICY_DENIED = "policy_denied"
    AUTHORIZATION_DENIED = "authorization_denied"
    FEATURE_GATE_DENIED = "feature_gate_denied"
    RATE_LIMIT_DENIED = "rate_limit_denied"
    APPROVAL_PENDING = "approval_pending"
    APPROVAL_REJECTED = "approval_rejected"
    APPROVAL_EXPIRED = "approval_expired"
    UNKNOWN_TOOL = "unknown_tool"
    TOOL_RETIRED = "tool_retired"
    VALIDATION_ERROR = "validation_error"
    EXECUTION_ERROR = "execution_error"


class SourceLevel(StrEnum):
    """A superset of `governance.PolicyLevel` (same string values for the shared
    six) plus the non-policy governance/execution stages a `DecisionReason` can
    attribute to."""

    SYSTEM = "system"
    ORGANIZATION = "organization"
    WORKSPACE = "workspace"
    AGENT = "agent"
    SKILL = "skill"
    TOOL = "tool"
    TENANT = "tenant"
    AUTHORIZATION = "authorization"
    FEATURE_GATE = "feature_gate"
    RATE_LIMIT = "rate_limit"
    EXECUTION = "execution"


@dataclass(frozen=True, slots=True)
class DecisionReason:
    """A deterministic, structured explanation for one governance/execution
    outcome. `explanation` is derived mechanically from the other fields — never
    free-text/AI-generated (§ "Decision Reason Model" of the M6.1B-3 prompt)."""

    reason_code: ReasonCode
    source_level: SourceLevel
    matched_rule: str | None = None
    policy_version: str = "1"
    explanation: str = ""


@dataclass(frozen=True, slots=True)
class ToolGovernanceDecisionTrace:
    """A read-side projection of one `governance.GovernanceDecision` — never
    execution data (that's `ExecutionTrace`'s job). Built by `governance_trace.py`
    from the already-computed `ToolGovernanceTrace`, never re-derived independently."""

    policy_verdict: PolicyVerdict
    reason: DecisionReason
    authorization_verdict: Effect | None = None
    approval_state: ApprovalState | None = None
    tenant_allowed: bool | None = None
    feature_gate_allowed: bool | None = None
    rate_limit_allowed: bool | None = None
    secret_provider_used: str | None = None


@dataclass(frozen=True, slots=True)
class ExecutionTrace:
    """A read-side observability record of one tool execution — built from an
    already-finished `ToolResult`, never influencing it. Arguments/output are
    hashes only, never raw payloads (§ "Never store raw sensitive payloads")."""

    trace_id: str
    request_id: str
    tool_id: str
    tool_version: str
    classification: str
    transitions: tuple[str, ...] = ()
    duration_ms: float = 0.0
    started_at: float = 0.0
    ended_at: float = 0.0
    arguments_hash: str = ""
    output_hash: str = ""
    replay_hash: str = ""
    metadata: Mapping[str, str] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ReplayRecord:
    """Everything needed to deterministically re-verify one execution later:
    the serialized request, the governance + execution traces, and the
    serialized result — never a live adapter/registry reference."""

    replay_id: str
    request_document: Mapping[str, object]
    governance_trace: ToolGovernanceDecisionTrace | None
    execution_trace: ExecutionTrace
    result_document: Mapping[str, object]
    replay_hash: str
    replay_version: str = "1"
    repository_version: str = "1"
    replay_timestamp: float = 0.0


@dataclass(frozen=True, slots=True)
class ReplayComparison:
    """The outcome of one replay validation/comparison. `mismatches` names the
    fields that disagreed (empty when `matches`)."""

    matches: bool
    expected_hash: str
    actual_hash: str
    mismatches: tuple[str, ...] = ()
    reason: str = ""


class FailureCategory(StrEnum):
    VALIDATION = "validation"
    EXECUTION = "execution"
    GOVERNANCE = "governance"
    UNKNOWN_TOOL = "unknown_tool"
    RETIRED_TOOL = "retired_tool"
    TIMEOUT = "timeout"  # reserved — no timeout mechanism exists yet
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class FailureAnalysis:
    """A deterministic classification of one failed `ToolResult` — a fixed
    lookup table, never AI/heuristic inference (§ "No AI analysis. No
    heuristics.")."""

    category: FailureCategory
    reason: str
    phase: str
    recoverable: bool
    recommendation: str = ""


@dataclass(frozen=True, slots=True)
class ToolAuditRecord:
    """One governed-request audit entry. Never includes secrets — `tool`/
    `action`/`decision` are names/verdicts only, never arguments or output."""

    audit_id: str
    identity_kind: str | None
    identity_id: str | None
    org_id: str | None
    workspace_id: str | None
    tool: str
    action: str
    decision: str
    timestamp: float
    replay_ref: str | None = None
    trace_ref: str | None = None
