"""Reasoning observability (M5.5B-1) — metric names + lifecycle counters.

The canonical meter-key constants; `ReasoningStatistics` (the read-side snapshot) lives in
`domain`. Tracing intelligence + rich per-stage statistics are M5.5B-2; the core surfaces
lifecycle counters + temporary-storage size + provider names.
"""

from __future__ import annotations


class Metric:
    REQUESTED = "reasoning.requested"
    STARTED = "reasoning.started"
    STREAMING = "reasoning.streaming"
    CHUNKS = "reasoning.chunks"
    COMPLETED = "reasoning.completed"
    DELIVERED = "reasoning.delivered"
    FAILED = "reasoning.failed"
    CANCELLED = "reasoning.cancelled"
    EXPIRED = "reasoning.expired"
    PROMPT_TOKENS = "reasoning.tokens.prompt"
    COMPLETION_TOKENS = "reasoning.tokens.completion"
    INFER_LATENCY = "reasoning.infer.latency"
