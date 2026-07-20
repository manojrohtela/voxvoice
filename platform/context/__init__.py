"""context — the Context Runtime (Epic 5, M5.3B-1: core).

A provider-neutral, deterministic, event-driven runtime that **only owns context
assembly** — it never stores memory, owns knowledge, renders prompts, reasons, executes
tools, or talks to an LLM. Built on the M5.0 foundation: a `BusConsumer`/`RuntimeModule`
implementing `core.ports.ContextPort`, a `core.fsm` package lifecycle, a repository layer
over the `core.providers.stores` contracts (**temporary** storage only), and `context.*`
events that Replay/Gateway observe for free. Every input is consumed through an injected
port (wrapped as a `ContextSource`) or immutable data — so `context` imports no sibling
runtime and the graph stays acyclic (`context → core`).

M5.3B-1 delivers the core: domain, lifecycle, temporary repository, session/window
management, source adapters, deterministic assembly (merge → normalize → order → naive
budget), serializer, config, factory, and assemble/get/remove/history + a TTL sweep.
Budget/ranking/compression/validation/policy/tenancy intelligence + observability
intelligence + streaming are explicit non-goals (M5.3B-2).
"""

from .assembly import AssemblyOutcome, ContextAssembler
from .budget import BudgetResult, BudgetStats, ContextBudgetManager
from .builder import ContextBuilder
from .compression import (
    CompressionResult,
    CompressionStats,
    ContextCompressionEngine,
)
from .config import (
    CollectionsConfig,
    ContextConfiguration,
    StoreConfig,
    TokenizerConfig,
    WindowConfig,
)
from .dedup import ContextDeduplicationEngine, DedupResult, DedupStats
from .domain import (
    BudgetMetadata,
    BudgetUsage,
    CompressionMetadata,
    ContextItem,
    ContextKind,
    ContextMetadata,
    ContextPackage,
    ContextResponse,
    ContextScope,
    ContextSession,
    ContextWindow,
    PackageState,
    Provenance,
    RankingBreakdown,
    SourceRuntime,
)
from .events import ContextPhase
from .extensions import (
    Compressor,
    ContextConsumer,
    ModelProfile,
    Redactor,
    Reranker,
    SemanticDeduplicator,
    StreamingContextSink,
    Summarizer,
)
from .factory import ContextFactory
from .ids import content_hash, item_id_for, package_id_for, request_hash_for
from .lifecycle import PACKAGE_LIFECYCLE, PackageTrigger, advance_package
from .observability import ContextStatistics, Metric
from .ordering import ContextOrderingEngine
from .policies import (
    ContextBudgetPolicy,
    ContextCitationPolicy,
    ContextCompressionPolicy,
    ContextOrderingPolicy,
    ContextPolicies,
    ContextPolicyEngine,
    ContextPrivacyPolicy,
    ContextRankingPolicy,
    ContextSourceSelectionPolicy,
    ContextValidationPolicy,
    OverflowStrategy,
    Verdict,
)
from .ports import AssemblyRequest, ContextSource, SourceContribution, Tokenizer
from .ranking import ContextRankingEngine
from .repositories import ContextRepository
from .runtime import ContextRuntime
from .serializer import ContextSerializer
from .session import ContextSessionManager
from .sources import (
    KnowledgeSource,
    MemorySource,
    PromptDirective,
    PromptSource,
    SourceSelector,
    StaticContextSource,
)
from .stores import ContextStore, InMemoryDocumentStore, InMemoryMetadataStore, WordTokenizer
from .tenancy import ContextAccessDenied, ContextTenancy, ResolvedScope
from .validation import ContextValidationEngine, ValidationReport

__all__ = [
    # runtime + composition
    "ContextRuntime",
    "ContextFactory",
    "ContextAssembler",
    "ContextBuilder",
    "ContextSerializer",
    "ContextSessionManager",
    # domain
    "ContextPackage",
    "ContextItem",
    "ContextResponse",
    "ContextKind",
    "SourceRuntime",
    "PackageState",
    "ContextScope",
    "Provenance",
    "RankingBreakdown",
    "ContextMetadata",
    "BudgetUsage",
    "BudgetMetadata",
    "CompressionMetadata",
    "ContextWindow",
    "ContextSession",
    # ── intelligence (M5.3B-2) ────────────────────────────────────────────────
    "AssemblyOutcome",
    "ContextRankingEngine",
    "ContextDeduplicationEngine",
    "DedupResult",
    "DedupStats",
    "ContextCompressionEngine",
    "CompressionResult",
    "CompressionStats",
    "ContextBudgetManager",
    "BudgetResult",
    "BudgetStats",
    "ContextOrderingEngine",
    "ContextValidationEngine",
    "ValidationReport",
    "ContextTenancy",
    "ContextAccessDenied",
    "ResolvedScope",
    # policies
    "ContextPolicyEngine",
    "ContextPolicies",
    "ContextBudgetPolicy",
    "ContextRankingPolicy",
    "ContextCompressionPolicy",
    "ContextOrderingPolicy",
    "ContextValidationPolicy",
    "ContextPrivacyPolicy",
    "ContextCitationPolicy",
    "ContextSourceSelectionPolicy",
    "OverflowStrategy",
    "Verdict",
    # extension interfaces
    "ModelProfile",
    "Summarizer",
    "Compressor",
    "Redactor",
    "SemanticDeduplicator",
    "Reranker",
    "ContextConsumer",
    "StreamingContextSink",
    # ids
    "content_hash",
    "item_id_for",
    "package_id_for",
    "request_hash_for",
    # lifecycle
    "PACKAGE_LIFECYCLE",
    "PackageTrigger",
    "advance_package",
    # repository + stores
    "ContextRepository",
    "ContextStore",
    "InMemoryDocumentStore",
    "InMemoryMetadataStore",
    "WordTokenizer",
    # sources + ports
    "ContextSource",
    "SourceSelector",
    "MemorySource",
    "KnowledgeSource",
    "PromptSource",
    "PromptDirective",
    "StaticContextSource",
    "AssemblyRequest",
    "SourceContribution",
    "Tokenizer",
    # observability + events
    "ContextStatistics",
    "Metric",
    "ContextPhase",
    # config
    "ContextConfiguration",
    "StoreConfig",
    "TokenizerConfig",
    "WindowConfig",
    "CollectionsConfig",
]
