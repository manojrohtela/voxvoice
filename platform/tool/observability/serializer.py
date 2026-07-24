"""Observability serialization (M6.1B-3) — deterministic, round-trippable, no
I/O. Versioned (`schema_version`) so future fields are additive-only extension
points, never breaking an already-stored document.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from ..extensions import PolicyVerdict
from ..governance.domain import ApprovalState, Effect
from .domain import (
    DecisionReason,
    ExecutionTrace,
    FailureAnalysis,
    FailureCategory,
    ReasonCode,
    ReplayRecord,
    SourceLevel,
    ToolAuditRecord,
    ToolGovernanceDecisionTrace,
)

SCHEMA_VERSION = "1"


class ObservabilitySerializer:
    # ── DecisionReason ────────────────────────────────────────────────────────
    def reason_to_dict(self, r: DecisionReason) -> dict[str, Any]:
        return {
            "reason_code": r.reason_code.value, "source_level": r.source_level.value,
            "matched_rule": r.matched_rule, "policy_version": r.policy_version,
            "explanation": r.explanation,
        }

    def reason_from_dict(self, d: Mapping[str, Any]) -> DecisionReason:
        return DecisionReason(
            reason_code=ReasonCode(d["reason_code"]), source_level=SourceLevel(d["source_level"]),
            matched_rule=d.get("matched_rule"), policy_version=d.get("policy_version", "1"),
            explanation=d.get("explanation", ""),
        )

    # ── ToolGovernanceDecisionTrace ───────────────────────────────────────────
    def governance_trace_to_dict(self, g: ToolGovernanceDecisionTrace) -> dict[str, Any]:
        auth_verdict = g.authorization_verdict.value if g.authorization_verdict else None
        return {
            "schema_version": SCHEMA_VERSION, "policy_verdict": g.policy_verdict.value,
            "reason": self.reason_to_dict(g.reason),
            "authorization_verdict": auth_verdict,
            "approval_state": g.approval_state.value if g.approval_state else None,
            "tenant_allowed": g.tenant_allowed, "feature_gate_allowed": g.feature_gate_allowed,
            "rate_limit_allowed": g.rate_limit_allowed,
            "secret_provider_used": g.secret_provider_used,
        }

    def governance_trace_from_dict(self, d: Mapping[str, Any]) -> ToolGovernanceDecisionTrace:
        return ToolGovernanceDecisionTrace(
            policy_verdict=PolicyVerdict(d["policy_verdict"]),
            reason=self.reason_from_dict(d["reason"]),
            authorization_verdict=Effect(d["authorization_verdict"])
            if d.get("authorization_verdict") else None,
            approval_state=ApprovalState(d["approval_state"]) if d.get("approval_state") else None,
            tenant_allowed=d.get("tenant_allowed"),
            feature_gate_allowed=d.get("feature_gate_allowed"),
            rate_limit_allowed=d.get("rate_limit_allowed"),
            secret_provider_used=d.get("secret_provider_used"),
        )

    # ── ExecutionTrace ────────────────────────────────────────────────────────
    def execution_trace_to_dict(self, e: ExecutionTrace) -> dict[str, Any]:
        return {
            "schema_version": SCHEMA_VERSION, "trace_id": e.trace_id, "request_id": e.request_id,
            "tool_id": e.tool_id, "tool_version": e.tool_version,
            "classification": e.classification,
            "transitions": list(e.transitions), "duration_ms": e.duration_ms,
            "started_at": e.started_at, "ended_at": e.ended_at,
            "arguments_hash": e.arguments_hash, "output_hash": e.output_hash,
            "replay_hash": e.replay_hash, "metadata": dict(e.metadata),
        }

    def execution_trace_from_dict(self, d: Mapping[str, Any]) -> ExecutionTrace:
        return ExecutionTrace(
            trace_id=d["trace_id"], request_id=d["request_id"], tool_id=d["tool_id"],
            tool_version=d["tool_version"], classification=d["classification"],
            transitions=tuple(d.get("transitions", ())), duration_ms=d.get("duration_ms", 0.0),
            started_at=d.get("started_at", 0.0), ended_at=d.get("ended_at", 0.0),
            arguments_hash=d.get("arguments_hash", ""), output_hash=d.get("output_hash", ""),
            replay_hash=d.get("replay_hash", ""), metadata=dict(d.get("metadata", {})),
        )

    # ── ReplayRecord ──────────────────────────────────────────────────────────
    def replay_record_to_document(self, r: ReplayRecord) -> dict[str, Any]:
        return {
            "schema_version": SCHEMA_VERSION, "replay_id": r.replay_id,
            "request_document": dict(r.request_document),
            "governance_trace": self.governance_trace_to_dict(r.governance_trace)
            if r.governance_trace else None,
            "execution_trace": self.execution_trace_to_dict(r.execution_trace),
            "result_document": dict(r.result_document), "replay_hash": r.replay_hash,
            "replay_version": r.replay_version, "repository_version": r.repository_version,
            "replay_timestamp": r.replay_timestamp,
        }

    def replay_record_from_document(self, d: Mapping[str, Any]) -> ReplayRecord:
        return ReplayRecord(
            replay_id=d["replay_id"], request_document=dict(d["request_document"]),
            governance_trace=self.governance_trace_from_dict(d["governance_trace"])
            if d.get("governance_trace") else None,
            execution_trace=self.execution_trace_from_dict(d["execution_trace"]),
            result_document=dict(d["result_document"]), replay_hash=d["replay_hash"],
            replay_version=d.get("replay_version", "1"),
            repository_version=d.get("repository_version", "1"),
            replay_timestamp=d.get("replay_timestamp", 0.0),
        )

    # ── FailureAnalysis ───────────────────────────────────────────────────────
    def failure_analysis_to_dict(self, f: FailureAnalysis) -> dict[str, Any]:
        return {
            "category": f.category.value, "reason": f.reason, "phase": f.phase,
            "recoverable": f.recoverable, "recommendation": f.recommendation,
        }

    def failure_analysis_from_dict(self, d: Mapping[str, Any]) -> FailureAnalysis:
        return FailureAnalysis(
            category=FailureCategory(d["category"]), reason=d.get("reason", ""),
            phase=d.get("phase", ""), recoverable=d.get("recoverable", False),
            recommendation=d.get("recommendation", ""),
        )

    # ── ToolAuditRecord ───────────────────────────────────────────────────────
    def audit_record_to_document(self, a: ToolAuditRecord) -> dict[str, Any]:
        return {
            "schema_version": SCHEMA_VERSION, "audit_id": a.audit_id,
            "identity_kind": a.identity_kind, "identity_id": a.identity_id, "org_id": a.org_id,
            "workspace_id": a.workspace_id, "tool": a.tool, "action": a.action,
            "decision": a.decision, "timestamp": a.timestamp, "replay_ref": a.replay_ref,
            "trace_ref": a.trace_ref,
        }

    def audit_record_from_document(self, d: Mapping[str, Any]) -> ToolAuditRecord:
        return ToolAuditRecord(
            audit_id=d["audit_id"], identity_kind=d.get("identity_kind"),
            identity_id=d.get("identity_id"), org_id=d.get("org_id"),
            workspace_id=d.get("workspace_id"), tool=d["tool"], action=d["action"],
            decision=d["decision"], timestamp=d["timestamp"], replay_ref=d.get("replay_ref"),
            trace_ref=d.get("trace_ref"),
        )
