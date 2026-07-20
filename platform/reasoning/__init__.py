"""reasoning — the Reasoning Runtime (Epic 5, M5.5B-1: core).

A provider-neutral, deterministic, event-driven runtime that **only owns inference
orchestration** — it never stores memory, owns knowledge, assembles context, constructs
prompts, talks to a provider SDK, opens a socket, executes a tool, or persists durably. Built
on the M5.0 foundation: a `BusConsumer`/`RuntimeModule` implementing `core.ports.ReasoningPort`,
a `core.fsm` inference lifecycle, a repository layer over the `core.providers.stores`
contracts (**temporary** storage only), and `reasoning.*`/`model.*` events that Replay/Gateway
observe for free. Its input is a neutral `InferenceRequest` (projected from a `PromptPackage`
by a composition-root bridge — never the `prompt` package); the model is the injected
`ModelProvider` port (the deterministic `EchoProvider` by default) — so `reasoning` imports no
sibling runtime and the graph stays acyclic (`reasoning → core`).

M5.5B-1 delivers the core: domain, lifecycle, the inference engine (provider select → stream →
assemble) + EchoProvider + ProviderRegistry, response assembler, session management, the
temporary repository, serializer, config, factory, and run/infer/get/remove/history + a TTL
sweep. Retry, cancellation, tool coordination, scheduling, policies, and tenancy are explicit
non-goals (M5.5B-2).
"""

from .assembler import ResponseAssembler
from .cancellation import CancellationManager, CancellationSignal, CancelReason
from .config import CollectionsConfig, ReasoningConfiguration, StoreConfig
from .domain import (
    CancellationRecord,
    ChunkKind,
    FinishReason,
    GenerationParameters,
    InferenceMessage,
    InferenceRequest,
    InferenceResponse,
    InferenceState,
    ReasoningExecutionTrace,
    ReasoningLineage,
    ReasoningMetadata,
    ReasoningScope,
    ReasoningSession,
    ReasoningStatistics,
    ResponseChunk,
    ResponseMetadata,
    ResponsePackage,
    RetryRecord,
    StreamingMetadata,
    TokenUsage,
    ToolRecord,
    TraceEvent,
)
from .engine import InferenceEngine, ProviderRegistry
from .events import ReasoningPhase
from .extensions import (
    AgentBridge,
    AsyncModelProvider,
    MultiModalResponder,
    ReasoningConsumer,
    ReasoningOptimizer,
    ResponseOptimizer,
    ToolProvider,
    WorkflowBridge,
)
from .factory import ReasoningFactory
from .ids import chunk_id_for, content_hash, package_id_for, request_hash_for, response_hash_for
from .lifecycle import REASONING_LIFECYCLE, InferenceTrigger, advance
from .observability import Metric, PipelineStatistics
from .policies import (
    CancellationPolicy,
    ProviderPolicy,
    ReasoningPolicies,
    ReasoningPolicy,
    ReasoningPolicyEngine,
    RetryControlPolicy,
    SafetyPolicy,
    StreamingPolicy,
    ToolPolicy,
    Verdict,
)
from .ports import ModelProvider
from .repositories import ReasoningRepository
from .retry import RetryController, RetryExhausted, RetryOutcome
from .runtime import ReasoningRuntime
from .scheduler import ReasoningScheduler, ScheduledItem
from .serializer import ReasoningSerializer
from .session import ReasoningSessionManager
from .stores import EchoProvider, InMemoryDocumentStore, InMemoryMetadataStore, ReasoningStore
from .streaming import StreamingController, StreamingSession
from .tenancy import ReasoningAccessDenied, ReasoningTenancy
from .tools import ToolCoordination, ToolDecisionCoordinator

__all__ = [
    # runtime + composition
    "ReasoningRuntime",
    "ReasoningFactory",
    "InferenceEngine",
    "ProviderRegistry",
    "ResponseAssembler",
    "ReasoningSerializer",
    "ReasoningSessionManager",
    # domain
    "InferenceRequest",
    "InferenceResponse",
    "InferenceMessage",
    "ResponsePackage",
    "ResponseChunk",
    "ResponseMetadata",
    "ReasoningLineage",
    "ReasoningMetadata",
    "ReasoningScope",
    "ReasoningSession",
    "ReasoningStatistics",
    "GenerationParameters",
    "TokenUsage",
    "StreamingMetadata",
    "RetryRecord",
    "CancellationRecord",
    "InferenceState",
    "ChunkKind",
    "FinishReason",
    # ids
    "content_hash",
    "request_hash_for",
    "response_hash_for",
    "chunk_id_for",
    "package_id_for",
    # lifecycle
    "REASONING_LIFECYCLE",
    "InferenceTrigger",
    "advance",
    # repository + stores + ports
    "ReasoningRepository",
    "ReasoningStore",
    "InMemoryDocumentStore",
    "InMemoryMetadataStore",
    "EchoProvider",
    "ModelProvider",
    # observability + events
    "Metric",
    "PipelineStatistics",
    "ReasoningPhase",
    # config
    "ReasoningConfiguration",
    "StoreConfig",
    "CollectionsConfig",
    # ── M5.5B-2 intelligence ──────────────────────────────────────────────────
    # streaming / retry / cancellation / tools / scheduler
    "StreamingController",
    "StreamingSession",
    "RetryController",
    "RetryOutcome",
    "RetryExhausted",
    "CancellationManager",
    "CancellationSignal",
    "CancelReason",
    "ToolDecisionCoordinator",
    "ToolCoordination",
    "ReasoningScheduler",
    "ScheduledItem",
    # policies
    "ReasoningPolicyEngine",
    "ReasoningPolicies",
    "ReasoningPolicy",
    "RetryControlPolicy",
    "StreamingPolicy",
    "ToolPolicy",
    "ProviderPolicy",
    "CancellationPolicy",
    "SafetyPolicy",
    "Verdict",
    # tenancy
    "ReasoningTenancy",
    "ReasoningAccessDenied",
    # lineage / trace
    "ReasoningExecutionTrace",
    "TraceEvent",
    "ToolRecord",
    # extensions
    "AsyncModelProvider",
    "ReasoningConsumer",
    "ResponseOptimizer",
    "ReasoningOptimizer",
    "MultiModalResponder",
    "ToolProvider",
    "AgentBridge",
    "WorkflowBridge",
]
