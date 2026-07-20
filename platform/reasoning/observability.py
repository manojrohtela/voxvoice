"""Reasoning observability (M5.5B-1 core + M5.5B-2 intelligence) — metric names + snapshots.

The canonical meter-key constants; `ReasoningStatistics` (the read-side snapshot) lives in
`domain`. M5.5B-2 adds streaming/retry/tool/cancellation/scheduler/provider metrics + a
per-inference `PipelineStatistics` snapshot (for tracing/replay). All additive; the core
counters are unchanged.
"""

from __future__ import annotations

from dataclasses import dataclass


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
    # ── M5.5B-2 intelligence ──────────────────────────────────────────────────
    BACKPRESSURE = "reasoning.streaming.backpressure"
    STREAM_TIMEOUT = "reasoning.streaming.timeout"
    RETRIES = "reasoning.retry.attempts"
    RETRY_EXHAUSTED = "reasoning.retry.exhausted"
    CIRCUIT_TRIPS = "reasoning.retry.circuit_trips"
    TOOL_CALLS = "reasoning.tool.calls"
    TOOL_REJECTED = "reasoning.tool.rejected"
    CANCELLATIONS = "reasoning.cancellations"
    SCHEDULED = "reasoning.scheduler.scheduled"
    PROVIDER_SELECTED = "reasoning.provider.selected"


@dataclass(frozen=True, slots=True)
class PipelineStatistics:
    """A per-inference snapshot of the intelligence stages (for tracing/replay)."""

    provider: str = "echo"
    model: str = "default"
    chunks: int = 0
    used_tokens: int = 0
    retries: int = 0
    tool_calls: int = 0
    cancelled: bool = False
    backpressure_events: int = 0
    trace_events: int = 0
