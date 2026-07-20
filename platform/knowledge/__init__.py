"""knowledge — the Knowledge Runtime (Epic 5, M5.2B-1: core).

A provider-neutral, deterministic, event-driven runtime that **only owns the knowledge
lifecycle and retrieval** — it never stores memory, assembles context, generates
prompts, reasons, executes tools, or manages workflows. Built on the M5.0 foundation
(a `BusConsumer`/`RuntimeModule` implementing `core.ports.KnowledgePort`, `core.fsm`
document/chunk lifecycles, a repository layer over the `core.providers.stores`
contracts, and `knowledge.*` events that Replay/Gateway observe for free).

M5.2B-1 delivers the core: domain, lifecycles, repositories, local adapters, document/
chunk/embedding pipelines, versioning, serialization, config, factory, and
index/get/remove + a retrieve placeholder. Retrieval planning, ranking, advanced
policies, cache, and event ingestion are explicit non-goals (M5.2B-2).
"""

from .cache import CacheStats, KnowledgeCache, LRUCacheBackend
from .config import (
    CacheConfig,
    ChunkConfig,
    CollectionsConfig,
    EmbedderConfig,
    KnowledgeConfiguration,
    RetrievalConfig,
    StoreConfig,
)
from .domain import (
    Chunk,
    ChunkState,
    Document,
    DocumentState,
    DocumentVersion,
    ImportOrigin,
    KnowledgeMetadata,
    KnowledgeScope,
    KnowledgeType,
    SourceDescriptor,
)
from .events import KnowledgePhase
from .extensions import (
    Compressor,
    GraphRetriever,
    GraphStore,
    KnowledgeSource,
    MultiVectorRetriever,
    Redactor,
    Reranker,
    Summarizer,
)
from .factory import KnowledgeFactory
from .ids import chunk_id_for, content_hash, document_id_for, version_id_for
from .index import KnowledgeIndex
from .ingestion import (
    IngestAction,
    IngestionResult,
    KnowledgeIngestor,
    MaintainIntent,
    RegisterIntent,
)
from .lifecycle import (
    CHUNK_LIFECYCLE,
    DOCUMENT_LIFECYCLE,
    ChunkTrigger,
    DocumentTrigger,
    advance_chunk,
    advance_document,
)
from .observability import KnowledgeStatistics, Metric, RetrievalStatistics
from .pipeline import (
    ChunkPipeline,
    DocumentPipeline,
    EmbeddingPipeline,
    IngestResult,
    KnowledgeValidationError,
    PlainTextParser,
    SlidingWindowChunker,
)
from .planner import KnowledgeQueryPlanner, RetrievalPlan, RetrievalStrategy
from .policies import (
    ArchivePolicy,
    CitationPolicy,
    DeduplicationPolicy,
    DeletionPolicy,
    EmbeddingPolicy,
    KnowledgePolicies,
    KnowledgePolicyEngine,
    PrivacyPolicy,
    RetentionPolicy,
    RetrievalPolicy,
    Verdict,
    VersionPolicy,
)
from .ports import ChunkSpec, ChunkStrategy, Parser
from .profiles import PROFILES, ProfileConfig, RetrievalProfile, config_for
from .query import KnowledgeRetrievalQuery
from .ranking import Candidate, KnowledgeRankingEngine, ScoredChunk
from .repositories import (
    ChunkRepository,
    DocumentRepository,
    EmbeddingRepository,
    KnowledgeRepository,
)
from .results import Citation, KnowledgeHit, KnowledgeResult, RankingBreakdown
from .retrieval import KnowledgeRetrievalEngine
from .runtime import KnowledgeRuntime
from .serializer import KnowledgeSerializer
from .stores import KnowledgeStore
from .tenancy import KnowledgeAccessDenied, KnowledgeTenancy, ResolvedScope
from .version import VersionManager

__all__ = [
    # runtime + composition
    "KnowledgeRuntime",
    "KnowledgeFactory",
    "KnowledgeSerializer",
    # domain
    "Document",
    "DocumentState",
    "DocumentVersion",
    "Chunk",
    "ChunkState",
    "KnowledgeType",
    "KnowledgeScope",
    "KnowledgeMetadata",
    "SourceDescriptor",
    "ImportOrigin",
    # ids
    "content_hash",
    "document_id_for",
    "version_id_for",
    "chunk_id_for",
    # lifecycle
    "DOCUMENT_LIFECYCLE",
    "CHUNK_LIFECYCLE",
    "DocumentTrigger",
    "ChunkTrigger",
    "advance_document",
    "advance_chunk",
    # repositories + stores
    "KnowledgeRepository",
    "DocumentRepository",
    "ChunkRepository",
    "EmbeddingRepository",
    "KnowledgeStore",
    "KnowledgeIndex",
    "VersionManager",
    # pipelines + ports
    "DocumentPipeline",
    "ChunkPipeline",
    "EmbeddingPipeline",
    "SlidingWindowChunker",
    "PlainTextParser",
    "IngestResult",
    "KnowledgeValidationError",
    "Parser",
    "ChunkStrategy",
    "ChunkSpec",
    # observability + events
    "KnowledgeStatistics",
    "RetrievalStatistics",
    "Metric",
    "KnowledgePhase",
    # ── intelligence (M5.2B-2) ────────────────────────────────────────────────
    # retrieval orchestration
    "KnowledgeRetrievalEngine",
    "KnowledgeQueryPlanner",
    "RetrievalPlan",
    "RetrievalStrategy",
    "KnowledgeRankingEngine",
    "Candidate",
    "ScoredChunk",
    "KnowledgeRetrievalQuery",
    # results (provenance-complete)
    "KnowledgeResult",
    "KnowledgeHit",
    "Citation",
    "RankingBreakdown",
    # profiles
    "RetrievalProfile",
    "ProfileConfig",
    "PROFILES",
    "config_for",
    # policy engine
    "KnowledgePolicyEngine",
    "KnowledgePolicies",
    "Verdict",
    "RetentionPolicy",
    "VersionPolicy",
    "PrivacyPolicy",
    "DeduplicationPolicy",
    "EmbeddingPolicy",
    "ArchivePolicy",
    "DeletionPolicy",
    "RetrievalPolicy",
    "CitationPolicy",
    # cache (repository-owned)
    "KnowledgeCache",
    "LRUCacheBackend",
    "CacheStats",
    # tenancy
    "KnowledgeTenancy",
    "ResolvedScope",
    "KnowledgeAccessDenied",
    # ingestion
    "KnowledgeIngestor",
    "IngestionResult",
    "RegisterIntent",
    "MaintainIntent",
    "IngestAction",
    # extension points
    "Reranker",
    "Summarizer",
    "Redactor",
    "Compressor",
    "KnowledgeSource",
    "GraphStore",
    "GraphRetriever",
    "MultiVectorRetriever",
    # config
    "KnowledgeConfiguration",
    "StoreConfig",
    "EmbedderConfig",
    "ChunkConfig",
    "CollectionsConfig",
    "CacheConfig",
    "RetrievalConfig",
]
