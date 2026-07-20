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


@dataclass(frozen=True, slots=True)
class ContextStatistics:
    """A point-in-time snapshot: health + counters + temporary-storage sizes."""

    name: str
    state: str
    healthy: bool
    packages: int = 0
    sessions: int = 0
    sources: tuple[str, ...] = ()
    metrics: dict[str, Any] = field(default_factory=dict)
