"""FailureAnalysis (M6.1B-3) — a fixed, deterministic lookup table. No AI, no
heuristics: `classify_error` (in `reasons.py`) already derived the category and
phase directly from `ToolRuntime`'s own frozen error-string prefixes; this module
only adds the recoverability flag and a canned recommendation per category.
"""

from __future__ import annotations

from ..domain import ToolResult
from .domain import FailureAnalysis, FailureCategory
from .reasons import classify_error

_RECOVERABLE: dict[FailureCategory, bool] = {
    FailureCategory.VALIDATION: True,
    FailureCategory.EXECUTION: True,
    FailureCategory.GOVERNANCE: True,
    FailureCategory.UNKNOWN_TOOL: False,
    FailureCategory.RETIRED_TOOL: False,
    FailureCategory.TIMEOUT: True,
    FailureCategory.UNKNOWN: False,
}

_RECOMMENDATION: dict[FailureCategory, str] = {
    FailureCategory.VALIDATION: "correct the request arguments against the tool's manifest",
    FailureCategory.EXECUTION: "inspect the adapter exception; retry if transient",
    FailureCategory.GOVERNANCE: "review the governance decision trace for the denying rule",
    FailureCategory.UNKNOWN_TOOL: "verify the tool name/namespace/version are registered",
    FailureCategory.RETIRED_TOOL: "select an ACTIVE version of the tool",
    FailureCategory.TIMEOUT: "retry with backoff",
    FailureCategory.UNKNOWN: "no classification available",
}


def analyze_failure(result: ToolResult) -> FailureAnalysis | None:
    """Returns `None` for a successful result — there is nothing to analyze."""
    if result.success:
        return None
    category, phase = classify_error(result.error)
    return FailureAnalysis(
        category=category, reason=result.error or "", phase=phase,
        recoverable=_RECOVERABLE[category], recommendation=_RECOMMENDATION[category],
    )
