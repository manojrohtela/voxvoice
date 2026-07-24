"""ToolGovernanceDecisionTrace builder (M6.1B-3) — a pure projection of an
already-computed `governance.GovernanceDecision`. Never re-evaluates policy/
authorization/tenancy/rate-limiting/feature-gates; never execution data.
"""

from __future__ import annotations

from ..governance.domain import GovernanceDecision
from .domain import ToolGovernanceDecisionTrace
from .reasons import reason_for_decision


def governance_trace_for(
    decision: GovernanceDecision, *, secret_provider_used: str | None = None
) -> ToolGovernanceDecisionTrace:
    trace = decision.trace
    return ToolGovernanceDecisionTrace(
        policy_verdict=decision.verdict,
        reason=reason_for_decision(decision),
        authorization_verdict=trace.authorization.effect if trace.authorization else None,
        approval_state=trace.approval.state if trace.approval else None,
        tenant_allowed=trace.tenant.allowed if trace.tenant else None,
        feature_gate_allowed=trace.feature_gate.allowed if trace.feature_gate else None,
        rate_limit_allowed=(
            all(d.allowed for d in trace.rate_limit) if trace.rate_limit else None
        ),
        secret_provider_used=secret_provider_used,
    )
