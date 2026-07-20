"""Prompt observability (M5.4B-1) — metric names + lifecycle counters.

The canonical meter-key constants + a deterministic snapshot the runtime exposes for
dashboards/health. Tracing intelligence + rich per-stage statistics are M5.4B-2; the core
surfaces lifecycle counters + temporary-storage size. `PromptStatistics` lives in `domain`.
"""

from __future__ import annotations


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
