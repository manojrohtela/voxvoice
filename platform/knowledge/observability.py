"""Knowledge observability (M5.2B-1) — statistics + metric names.

A deterministic snapshot object the runtime exposes for dashboards/health, plus the
canonical meter-key constants. Tracing spans and `knowledge.*` events (Replay/Gateway)
are emitted by the runtime; this module is the read-side vocabulary.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


class Metric:
    IMPORTED = "knowledge.imported"
    UPDATED = "knowledge.updated"
    DEDUPED = "knowledge.deduped"
    INDEXED = "knowledge.indexed"
    ARCHIVED = "knowledge.archived"
    DELETED = "knowledge.deleted"
    RETRIEVED = "knowledge.retrieved"
    CHUNKS_CREATED = "knowledge.chunks.created"
    EMBEDDINGS_CREATED = "knowledge.embeddings.created"
    INGEST_LATENCY = "knowledge.ingest.latency"
    # ── M5.2B-2 retrieval intelligence ────────────────────────────────────────
    RETRIEVAL_LATENCY = "knowledge.retrieval.latency"
    RETRIEVAL_HITS = "knowledge.retrieval.hits"
    RETRIEVAL_CANDIDATES = "knowledge.retrieval.candidates"
    CACHE_HIT = "knowledge.cache.hit"
    CACHE_MISS = "knowledge.cache.miss"
    INGESTED = "knowledge.ingested"


@dataclass(frozen=True, slots=True)
class RetrievalStatistics:
    """A per-retrieval snapshot (for tracing/replay): strategy + profile + counts."""

    strategy: str
    profile: str
    candidates: int
    hits: int


@dataclass(frozen=True, slots=True)
class KnowledgeStatistics:
    """A point-in-time snapshot: health + counters + sub-statistics."""

    name: str
    state: str
    healthy: bool
    documents: int = 0
    metrics: dict[str, Any] = field(default_factory=dict)
    index: dict[str, int] | None = None
    embeddings: dict[str, int] | None = None
    pipeline: dict[str, int] | None = None
    cache: dict[str, int] | None = None
    retrieval: dict[str, Any] | None = None
