"""Deterministic reason derivation (M6.1B-3) — no AI, no heuristics.

Two fixed lookup tables, each verified against the actual frozen behavior they
describe (not guessed): `classify_error` mirrors `tool.runtime.ToolRuntime.aexecute`'s
own error-string prefixes (`unknown_tool:` / `tool_retired` / `validation_error:` /
`execution_error:` — the only four failure paths that exist there), and
`reason_for_decision` mirrors `governance.runtime.ToolGovernance.aevaluate`'s own
short-circuit order (tenant -> policy -> authorization -> feature gate -> rate
limit -> approval -> allow).
"""

from __future__ import annotations

from ..extensions import PolicyVerdict
from ..governance.domain import GovernanceDecision, PolicyRule
from .domain import DecisionReason, FailureCategory, ReasonCode, SourceLevel


def matched_rule_repr(rule: PolicyRule) -> str:
    return f"{rule.level.value}:{rule.tool or '*'}:{rule.verdict.value}"


def classify_error(error: str | None) -> tuple[FailureCategory, str]:
    """Returns `(category, phase)`. `phase` is an `ExecutionState` value, inferred
    from the error prefix since `ToolResult.state` is always `FAILED` post-FSM
    (the pre-fail phase is only recoverable from the error string)."""
    if error is None:
        return FailureCategory.UNKNOWN, "unknown"
    if error.startswith("unknown_tool"):
        return FailureCategory.UNKNOWN_TOOL, "validating"
    if error.startswith("tool_retired"):
        return FailureCategory.RETIRED_TOOL, "validating"
    if error.startswith("validation_error"):
        return FailureCategory.VALIDATION, "validating"
    if error.startswith("execution_error"):
        return FailureCategory.EXECUTION, "executing"
    return FailureCategory.UNKNOWN, "unknown"


def reason_for_decision(decision: GovernanceDecision) -> DecisionReason:
    trace = decision.trace

    if trace.tenant is not None and not trace.tenant.allowed:
        return DecisionReason(
            reason_code=ReasonCode.TENANT_DENIED, source_level=SourceLevel.TENANT,
            explanation=trace.tenant.reason,
        )
    if trace.policy is not None and trace.policy.verdict is PolicyVerdict.DENY:
        level = SourceLevel(trace.policy.level.value) if trace.policy.level else SourceLevel.SYSTEM
        matched = trace.policy.trail[-1] if trace.policy.trail else None
        return DecisionReason(
            reason_code=ReasonCode.POLICY_DENIED, source_level=level,
            matched_rule=matched_rule_repr(matched) if matched else None,
            explanation=trace.policy.reason,
        )
    if trace.authorization is not None and not trace.authorization.allowed:
        return DecisionReason(
            reason_code=ReasonCode.AUTHORIZATION_DENIED, source_level=SourceLevel.AUTHORIZATION,
            explanation=trace.authorization.reason,
        )
    if trace.feature_gate is not None and not trace.feature_gate.allowed:
        return DecisionReason(
            reason_code=ReasonCode.FEATURE_GATE_DENIED, source_level=SourceLevel.FEATURE_GATE,
            explanation=trace.feature_gate.reason,
        )
    if trace.rate_limit and any(not d.allowed for d in trace.rate_limit):
        denied = next(d for d in trace.rate_limit if not d.allowed)
        return DecisionReason(
            reason_code=ReasonCode.RATE_LIMIT_DENIED, source_level=SourceLevel.RATE_LIMIT,
            explanation=denied.reason,
        )
    if decision.verdict is PolicyVerdict.PENDING:
        level = SourceLevel(trace.policy.level.value) if trace.policy and trace.policy.level \
            else SourceLevel.TOOL
        return DecisionReason(
            reason_code=ReasonCode.APPROVAL_PENDING, source_level=level,
            explanation=decision.reason,
        )
    return DecisionReason(reason_code=ReasonCode.ALLOWED, source_level=SourceLevel.TOOL)
