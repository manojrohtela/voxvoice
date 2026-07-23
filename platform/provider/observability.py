"""Provider observability (M5.6B-1 core + M5.6B-2 intelligence) — metric names + snapshots.

The canonical meter-key constants; `ProviderStatistics` (the read-side snapshot) lives in
`domain`. M5.6B-2 adds routing/health/failover/circuit-breaker/policy metrics + a
per-execution `PipelineStatistics` snapshot (for tracing/replay). All additive; the core
counters are unchanged.
"""

from __future__ import annotations

from dataclasses import dataclass


class Metric:
    REQUESTED = "provider.requested"
    ROUTED = "provider.routed"
    EXECUTING = "provider.executing"
    STREAMING = "provider.streaming"
    CHUNKS = "provider.chunks"
    COMPLETED = "provider.completed"
    DELIVERED = "provider.delivered"
    FAILED = "provider.failed"
    EXPIRED = "provider.expired"
    PROMPT_TOKENS = "provider.tokens.prompt"
    COMPLETION_TOKENS = "provider.tokens.completion"
    REQUEST_LATENCY = "provider.request.latency"
    PROVIDER_SELECTED = "provider.selected"
    # ── M5.6B-2 intelligence ──────────────────────────────────────────────────
    ROUTING_DECISIONS = "provider.routing.decisions"
    FAILOVERS = "provider.failover.count"
    FAILOVER_EXHAUSTED = "provider.failover.exhausted"
    CIRCUIT_OPEN = "provider.circuit.open"
    CIRCUIT_HALF_OPEN = "provider.circuit.half_open"
    HEALTH_DEGRADED = "provider.health.degraded"
    HEALTH_UNAVAILABLE = "provider.health.unavailable"
    HEALTH_RECOVERED = "provider.health.recovered"
    POLICY_REJECTED = "provider.policy.rejected"


@dataclass(frozen=True, slots=True)
class PipelineStatistics:
    """A per-execution snapshot of the intelligence stages (for tracing/replay)."""

    provider: str = "echo"
    model: str = "default"
    chunks: int = 0
    used_tokens: int = 0
    failovers: int = 0
    circuit_state: str = "closed"
    routing_strategy: str = "auto"
    trace_events: int = 0
