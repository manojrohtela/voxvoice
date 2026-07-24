"""Deterministic evaluation of `WorkflowCondition` (M6.4B-2) — the small,
closed expression grammar Bundle 1 only carried as a shape. Never arbitrary
code, never `eval()`. Evaluated against the execution's own accumulated
variables only.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from ..domain import WorkflowCondition, WorkflowConditionOperator


def evaluate_condition(condition: WorkflowCondition | None, variables: Mapping[str, Any]) -> bool:
    """`None` -> unconditional edge, always true. Missing fields resolve to
    `None` and compare/behave accordingly -- never raise on a missing key."""
    if condition is None:
        return True

    actual = variables.get(condition.field)
    op = condition.operator

    if op is WorkflowConditionOperator.EXISTS:
        return condition.field in variables
    if op is WorkflowConditionOperator.EQ:
        return actual == condition.value
    if op is WorkflowConditionOperator.NEQ:
        return actual != condition.value
    if op is WorkflowConditionOperator.IN:
        return actual in condition.value if condition.value is not None else False

    # Ordering operators: an unorderable/missing comparison is deterministically False,
    # never a raised exception -- structural validation already ensures the grammar
    # itself is well-formed; a runtime type mismatch is a data problem, not a crash.
    try:
        if op is WorkflowConditionOperator.GT:
            return actual > condition.value
        if op is WorkflowConditionOperator.LT:
            return actual < condition.value
        if op is WorkflowConditionOperator.GTE:
            return actual >= condition.value
        if op is WorkflowConditionOperator.LTE:
            return actual <= condition.value
    except TypeError:
        return False

    return False  # unreachable for a well-formed WorkflowConditionOperator
