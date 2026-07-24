"""HookExecutionPolicy (M6.3B) — per-`HookPoint` configuration governing
failure handling and verdict aggregation. Never hardcoded inside `engine.py`;
always caller-configurable per point, with `default_policy_for` supplying
the M6.3A-design-specified default posture: `before_*` gates fail-closed
(a raising hook counts as its own DENY vote), `after_*`/observational points
fail-open (a failing observer never retroactively blocks an action that
already happened) but every isolated failure is still recorded by
observability regardless of failure mode (§ Constraints — isolation is
mandatory, not a policy knob).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum

from .domain import HookOutcome, HookPoint, HookVerdict


class HookFailureMode(StrEnum):
    FAIL_CLOSED = "fail_closed"  # a raising hook counts as its own DENY vote
    FAIL_OPEN = "fail_open"      # a raising hook is recorded, excluded from aggregation


class HookAggregationRule(StrEnum):
    DENY_WINS = "deny_wins"
    ALL_MUST_ALLOW = "all_must_allow"
    FIRST_MODIFY_WINS = "first_modify_wins"


@dataclass(frozen=True, slots=True)
class HookExecutionPolicy:
    point: HookPoint
    failure_mode: HookFailureMode
    aggregation: HookAggregationRule
    isolation: bool = True  # a hard invariant in this design — always True


_BEFORE_POINTS = frozenset({
    HookPoint.BEFORE_PLAN, HookPoint.BEFORE_TOOL_CALL, HookPoint.BEFORE_HANDOFF,
    HookPoint.BEFORE_WORKFLOW_STEP,
})


def default_policy_for(point: HookPoint) -> HookExecutionPolicy:
    if point in _BEFORE_POINTS:
        return HookExecutionPolicy(
            point=point, failure_mode=HookFailureMode.FAIL_CLOSED,
            aggregation=HookAggregationRule.DENY_WINS,
        )
    return HookExecutionPolicy(
        point=point, failure_mode=HookFailureMode.FAIL_OPEN,
        aggregation=HookAggregationRule.DENY_WINS,
    )


def aggregate_verdicts(
    verdicts: Sequence[HookVerdict], rule: HookAggregationRule,
) -> HookOutcome:
    """Deterministic, pure aggregation — no randomness, no scoring."""
    if not verdicts:
        return HookOutcome.ALLOW

    if rule is HookAggregationRule.ALL_MUST_ALLOW:
        return (
            HookOutcome.ALLOW if all(v.outcome is HookOutcome.ALLOW for v in verdicts)
            else HookOutcome.DENY
        )

    if any(v.outcome is HookOutcome.DENY for v in verdicts):
        return HookOutcome.DENY

    has_modify = any(v.outcome is HookOutcome.MODIFY for v in verdicts)
    if rule is HookAggregationRule.FIRST_MODIFY_WINS:
        return HookOutcome.MODIFY if has_modify else HookOutcome.ALLOW

    # DENY_WINS (default): DENY already handled above; MODIFY otherwise wins over ALLOW.
    return HookOutcome.MODIFY if has_modify else HookOutcome.ALLOW
