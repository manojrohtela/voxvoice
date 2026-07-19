"""Memory observability (M5.1B-2) — statistics + metric names.

A deterministic snapshot object (`MemoryStatistics`) the runtime exposes for dashboards
and health, plus the canonical metric-name constants so the meter keys stay consistent
across the codebase. Tracing spans and `memory.*` events (Replay/Gateway) are emitted by
the runtime itself; this module is the read-side vocabulary.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


# ── metric name constants (Meter keys) ───────────────────────────────────────
class Metric:
    STORED = "memory.stored"
    UPDATED = "memory.updated"
    RETRIEVED = "memory.retrieved"
    EVICTED = "memory.evicted"
    EXPIRED = "memory.expired"
    CLEARED = "memory.cleared"
    REJECTED = "memory.rejected"
    INGESTED = "memory.ingested"
    RETRIEVAL_LATENCY = "memory.retrieval.latency"
    RANK_CANDIDATES = "memory.rank.candidates"
    CACHE_HIT = "memory.cache.hit"
    CACHE_MISS = "memory.cache.miss"


@dataclass(frozen=True, slots=True)
class MemoryStatistics:
    """A point-in-time snapshot of the runtime's health + counters + cache."""

    name: str
    state: str
    healthy: bool
    metrics: dict[str, Any] = field(default_factory=dict)
    cache: dict[str, int] | None = None
