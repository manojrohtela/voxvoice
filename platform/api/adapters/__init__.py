"""adapters — Provider Adapter Binding (Epic 8, M8.14).

The **execution-integration layer**: real provider implementations of the M8.12 runtime adapter
interfaces (`LLMAdapter`/`VoiceAdapter`/`ToolExecutor`/`MemoryAdapter`) + the
`ProviderExecutionEngine` that binds a resolved plan to real execution. **All provider-specific
translation lives here** (golden rule: adapters own translation); the network egress is behind an
injectable `Transport` seam, so this real translation runs offline/deterministically in tests.

**Ownership & boundaries.** `api.adapters → api.foundation, core, tenant` (+ stdlib). It never
imports a frozen Epic 5/6/7 runtime and never imports `api.customer`. The Foundation/Application/
Persistence layers never import `api.adapters` — the customer Container is the composition root
that injects the engine into the Foundation. No provider SDK is a dependency; a production
transport (e.g. httpx) is documented, not required. The runtime (M8.12), Tool Framework (M8.11),
Playground (M8.13) and Agent Builder (M8.10) are reused exactly and are byte-for-byte unchanged.
"""

from __future__ import annotations

from .composition import build_execution_engine, build_knowledge_retriever
from .engine import ProviderExecutionEngine
from .knowledge import (
    EMBEDDING_PROVIDERS,
    VECTOR_STORES,
    KnowledgeToolExecutor,
    ProviderKnowledgeRetriever,
    build_embedder,
    build_vector_store,
)
from .llm import (
    LLM_PROVIDERS,
    AnthropicLLMAdapter,
    AzureOpenAILLMAdapter,
    GeminiLLMAdapter,
    GroqLLMAdapter,
    OpenAILLMAdapter,
    build_llm_adapter,
)
from .memory import DurableMemoryAdapter
from .resilience import (
    CancelToken,
    CircuitBreaker,
    ExecutionCancelledError,
    ProviderTimeoutError,
    ProviderUnavailableError,
    RateLimiter,
    RetrySpec,
    execute_with_resilience,
)
from .secrets import (
    ChainedCredentialProvider,
    CredentialError,
    CredentialProvider,
    EnvCredentialProvider,
    redact,
    redact_headers,
)
from .tools import ToolExecutorDispatcher, default_executors
from .transport import (
    EchoTransport,
    FailingTransport,
    HttpTransport,
    ProviderRequest,
    ProviderResponse,
    Transport,
    TransportError,
)
from .voice import (
    VOICE_PROVIDERS,
    CartesiaVoiceAdapter,
    DeepgramVoiceAdapter,
    ElevenLabsVoiceAdapter,
    OpenAIVoiceAdapter,
    build_voice_adapter,
)

__all__ = [
    # composition + engine
    "build_execution_engine", "build_knowledge_retriever", "ProviderExecutionEngine",
    # knowledge (M8.15)
    "EMBEDDING_PROVIDERS", "VECTOR_STORES", "KnowledgeToolExecutor",
    "ProviderKnowledgeRetriever", "build_embedder", "build_vector_store",
    # transport
    "Transport", "EchoTransport", "FailingTransport", "HttpTransport", "ProviderRequest",
    "ProviderResponse", "TransportError",
    # credentials / secrets (M8.18 hardening)
    "CredentialProvider", "EnvCredentialProvider", "ChainedCredentialProvider", "CredentialError",
    "redact", "redact_headers",
    # llm
    "LLM_PROVIDERS", "build_llm_adapter", "AnthropicLLMAdapter", "OpenAILLMAdapter",
    "GeminiLLMAdapter", "GroqLLMAdapter", "AzureOpenAILLMAdapter",
    # voice
    "VOICE_PROVIDERS", "build_voice_adapter", "DeepgramVoiceAdapter", "ElevenLabsVoiceAdapter",
    "CartesiaVoiceAdapter", "OpenAIVoiceAdapter",
    # tools
    "ToolExecutorDispatcher", "default_executors",
    # memory
    "DurableMemoryAdapter",
    # resilience
    "CircuitBreaker", "RateLimiter", "RetrySpec", "CancelToken", "execute_with_resilience",
    "ExecutionCancelledError", "ProviderUnavailableError", "ProviderTimeoutError",
]
