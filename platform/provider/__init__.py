"""provider — the Provider Runtime (Epic 5, M5.6B-1: core).

A provider-neutral, deterministic, event-driven runtime that is **the sole execution
boundary between VoxVoice and every external AI provider** — it never assembles context,
constructs prompts, reasons, executes tools, or persists durably. Built on the M5.0
foundation: a `BusConsumer`, a `core.fsm` request-execution + adapter-health lifecycle, a
repository layer over the `core.providers.stores` contracts (**temporary** storage only), and
`provider.*`/`model.*` events that Replay/Gateway observe for free. Its input is a neutral
`ProviderRequest`; the vendor is the injected `AdapterPort` (the deterministic
`EchoProviderAdapter` by default) — so `provider` imports no sibling runtime and the graph
stays acyclic (`provider → core`).

M5.6B-1 delivers the core: domain, both lifecycles (request-execution + adapter-health),
`ProviderRegistry` (registration/discovery/lifecycle — no routing) + `CapabilityCatalog` (no
intelligence), the unified streaming/response/usage/metadata normalizers, the deterministic
`EchoProviderAdapter`, session management, the temporary repository, serializer, config,
factory, and execute/get/remove/history/expire/statistics. Routing strategies, health-outcome
feedback, a policy engine, and tenancy are explicit non-goals (M5.6B-2).
"""

from .catalog import CapabilityCatalog
from .config import CollectionsConfig, ProviderConfiguration, StoreConfig
from .domain import (
    Capability,
    ChunkKind,
    FinishReason,
    GenerationParameters,
    ProviderCapabilities,
    ProviderChunk,
    ProviderDescriptor,
    ProviderExecutionResult,
    ProviderHealthState,
    ProviderMessage,
    ProviderMetadata,
    ProviderRegistryEntry,
    ProviderRequest,
    ProviderRequestMetadata,
    ProviderRequestState,
    ProviderResponse,
    ProviderScope,
    ProviderSession,
    ProviderStatistics,
    ProviderUsage,
    StreamingMetadata,
)
from .events import ProviderPhase
from .extensions import (
    AsyncAdapter,
    BatchAdapter,
    EnterpriseAdapter,
    LocalAdapter,
    StreamingAdapter,
)
from .factory import ProviderFactory
from .ids import chunk_id_for, content_hash, request_hash_for, response_hash_for, response_id_for
from .lifecycle import (
    PROVIDER_EXECUTION_LIFECYCLE,
    PROVIDER_HEALTH_LIFECYCLE,
    HealthTrigger,
    ProviderTrigger,
    advance,
    advance_health,
)
from .metadata import MetadataNormalizer
from .observability import Metric
from .ports import AdapterPort
from .registry import ProviderRegistry
from .repositories import ProviderRepository
from .response import ResponseNormalizer
from .runtime import ProviderRuntime
from .serializer import ProviderSerializer
from .session import ProviderSessionManager
from .stores import EchoProviderAdapter, InMemoryDocumentStore, InMemoryMetadataStore, ProviderStore
from .streaming import UnifiedStreamingAssembler
from .usage import UsageNormalizer

__all__ = [
    # runtime + composition
    "ProviderRuntime",
    "ProviderFactory",
    "ProviderRegistry",
    "CapabilityCatalog",
    "ResponseNormalizer",
    "UsageNormalizer",
    "MetadataNormalizer",
    "UnifiedStreamingAssembler",
    "ProviderSerializer",
    "ProviderSessionManager",
    # domain
    "ProviderRequest",
    "ProviderRequestMetadata",
    "ProviderExecutionResult",
    "ProviderMessage",
    "ProviderResponse",
    "ProviderChunk",
    "ProviderMetadata",
    "ProviderScope",
    "ProviderSession",
    "ProviderStatistics",
    "ProviderCapabilities",
    "ProviderDescriptor",
    "ProviderRegistryEntry",
    "GenerationParameters",
    "ProviderUsage",
    "StreamingMetadata",
    "ProviderRequestState",
    "ProviderHealthState",
    "ChunkKind",
    "FinishReason",
    "Capability",
    # ids
    "content_hash",
    "request_hash_for",
    "response_hash_for",
    "chunk_id_for",
    "response_id_for",
    # lifecycle
    "PROVIDER_EXECUTION_LIFECYCLE",
    "PROVIDER_HEALTH_LIFECYCLE",
    "ProviderTrigger",
    "HealthTrigger",
    "advance",
    "advance_health",
    # ports
    "AdapterPort",
    # repository + stores
    "ProviderRepository",
    "ProviderStore",
    "InMemoryDocumentStore",
    "InMemoryMetadataStore",
    "EchoProviderAdapter",
    # observability + events
    "Metric",
    "ProviderPhase",
    # config
    "ProviderConfiguration",
    "StoreConfig",
    "CollectionsConfig",
    # extension hooks (interfaces only; M5.6B-1 scope)
    "AsyncAdapter",
    "BatchAdapter",
    "StreamingAdapter",
    "LocalAdapter",
    "EnterpriseAdapter",
]
