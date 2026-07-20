"""Context observability (M5.3B-1) — statistics + metric names.

A deterministic snapshot object the runtime exposes for dashboards/health, plus the
canonical meter-key constants. Tracing spans and `context.*` events (Replay/Gateway) are
emitted by the runtime; this module is the read-side vocabulary. Ranking/compression/
budget *intelligence* statistics are M5.3B-2 — the core surfaces assembly + repository
counters only.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


class Metric:
    ASSEMBLED = "context.assembled"
    DELIVERED = "context.delivered"
    REJECTED = "context.rejected"
    EXPIRED = "context.expired"
    ITEMS = "context.items"
    USED_TOKENS = "context.used_tokens"
    TRUNCATED = "context.truncated"
    ASSEMBLY_LATENCY = "context.assembly.latency"
    # ── M5.3B-2 intelligence ──────────────────────────────────────────────────
    DEDUP_REMOVED = "context.dedup.removed"
    COMPRESSED = "context.compressed"
    BUDGET_DROPPED = "context.budget.dropped"
    EMERGENCY_USED = "context.budget.emergency"


@dataclass(frozen=True, slots=True)
class PipelineStatistics:
    """A per-assembly snapshot of the intelligence stages (for tracing/replay)."""

    intelligent: bool = False
    dedup_removed: int = 0
    compressed_items: int = 0
    rolled_up_items: int = 0
    budget_dropped: int = 0
    emergency_used: bool = False
    used_tokens: int = 0
    limit_tokens: int = 0


@dataclass(frozen=True, slots=True)
class ContextStatistics:
    """A point-in-time snapshot: health + counters + temporary-storage sizes."""

    name: str
    state: str
    healthy: bool
    packages: int = 0
    sessions: int = 0
    sources: tuple[str, ...] = ()
    intelligent: bool = False
    metrics: dict[str, Any] = field(default_factory=dict)
