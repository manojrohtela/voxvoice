"""memory — the Memory Runtime (Epic 5, M5.1B-1: core).

A provider-neutral, deterministic, event-driven runtime that **only manages memory** —
it never reasons, renders prompts, or executes tools. Built entirely on the M5.0
foundation: a `BusConsumer`/`RuntimeModule` implementing `core.ports.MemoryPort`, a
`core.fsm` record lifecycle, a repository layer over the `core.providers.stores`
contracts, and `memory.*` events that Replay/Gateway observe for free.

M5.1B-1 delivers the core only: domain model, record lifecycle, repositories, local
adapters, serializer, configuration, factory, and store/retrieve/forget with event
publishing. Retrieval intelligence, ranking, policies, expiration, cache, and
content-event ingestion are explicit non-goals (later milestones).
"""

from .cache import CacheStats, LRUCacheBackend, MemoryCache
from .config import (
    CacheConfig,
    EmbedderConfig,
    MemoryConfiguration,
    RepositoryConfig,
    StoreConfig,
    TierConfig,
)
from .domain import (
    MemoryFilter,
    MemoryHit,
    MemoryId,
    MemoryMetadata,
    MemoryQuery,
    MemoryRecord,
    MemoryResult,
    MemoryScope,
    MemoryState,
    MemoryTier,
    MemoryType,
)
from .extensions import Compressor, Redactor, Reranker, Summarizer
from .factory import MemoryFactory
from .ingestion import AccessIntent, IngestionResult, MemoryIngestor, StoreIntent
from .lifecycle import LIFECYCLE, MemoryTrigger, advance, machine_for
from .observability import MemoryStatistics, Metric
from .planner import MemoryQueryPlanner, RetrievalPlan, RetrievalStrategy
from .policies import (
    ConflictPolicy,
    ConflictStrategy,
    ExpirationPolicy,
    MemoryPolicies,
    MergePolicy,
    PolicyEngine,
    PrivacyPolicy,
    RetentionPolicy,
)
from .ranking import RankingEngine, ScoredRecord
from .repositories import MemoryRepository, MetadataRepository, VectorRepository
from .retrieval import RetrievalEngine
from .runtime import MemoryRuntime
from .serializer import MemorySerializer
from .tenancy import MemoryAccessDenied, MemoryTenancy, ResolvedScope

__all__ = [
    # runtime + composition
    "MemoryRuntime",
    "MemoryFactory",
    "MemorySerializer",
    # domain
    "MemoryId",
    "MemoryType",
    "MemoryState",
    "MemoryTier",
    "MemoryScope",
    "MemoryQuery",
    "MemoryHit",
    "MemoryMetadata",
    "MemoryRecord",
    "MemoryFilter",
    "MemoryResult",
    # lifecycle
    "MemoryTrigger",
    "LIFECYCLE",
    "advance",
    "machine_for",
    # repositories
    "MemoryRepository",
    "VectorRepository",
    "MetadataRepository",
    # intelligence (M5.1B-2)
    "MemoryQueryPlanner",
    "RetrievalPlan",
    "RetrievalStrategy",
    "RetrievalEngine",
    "RankingEngine",
    "ScoredRecord",
    "PolicyEngine",
    "MemoryPolicies",
    "ExpirationPolicy",
    "RetentionPolicy",
    "MergePolicy",
    "ConflictPolicy",
    "ConflictStrategy",
    "PrivacyPolicy",
    "MemoryCache",
    "LRUCacheBackend",
    "CacheStats",
    "MemoryTenancy",
    "ResolvedScope",
    "MemoryAccessDenied",
    "MemoryIngestor",
    "IngestionResult",
    "StoreIntent",
    "AccessIntent",
    "MemoryStatistics",
    "Metric",
    # extension points
    "Reranker",
    "Summarizer",
    "Compressor",
    "Redactor",
    # config
    "MemoryConfiguration",
    "RepositoryConfig",
    "StoreConfig",
    "EmbedderConfig",
    "CacheConfig",
    "TierConfig",
]
