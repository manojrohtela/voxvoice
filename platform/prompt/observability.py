"""Prompt observability (M5.4B-1) — metric names + lifecycle counters.

The canonical meter-key constants + a deterministic snapshot the runtime exposes for
dashboards/health. Tracing intelligence + rich per-stage statistics are M5.4B-2; the core
surfaces lifecycle counters + temporary-storage size. `PromptStatistics` lives in `domain`.
"""

from __future__ import annotations

from dataclasses import dataclass


class Metric:
    BUILT = "prompt.built"
    VALIDATED = "prompt.validated"
    RENDERED = "prompt.rendered"
    DELIVERED = "prompt.delivered"
    REJECTED = "prompt.rejected"
    EXPIRED = "prompt.expired"
    SECTIONS = "prompt.sections"
    MESSAGES = "prompt.messages"
    USED_TOKENS = "prompt.used_tokens"
    TRUNCATED = "prompt.truncated"
    BUILD_LATENCY = "prompt.build.latency"
    TEMPLATE_UPDATED = "prompt.template.updated"
    # ── M5.4B-2 intelligence ──────────────────────────────────────────────────
    BUDGET_DROPPED = "prompt.budget.dropped"
    BUDGET_TRUNCATED = "prompt.budget.truncated"


@dataclass(frozen=True, slots=True)
class PipelineStatistics:
    """A per-build snapshot of the intelligence stages (for tracing/replay)."""

    provider: str = "neutral"
    model: str = "default"
    sections: int = 0
    messages: int = 0
    used_tokens: int = 0
    limit_tokens: int = 0
    dropped_sections: int = 0
    truncated_sections: int = 0
    validated: bool = True
