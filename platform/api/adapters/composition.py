"""Provider adapter composition root (M8.14).

Builds a `ProviderExecutionEngine` (the real `ExecutionEngine`) from a transport + a message
store — the one place provider adapters are wired. The customer Container calls this to inject
the engine into the Foundation (via `build_persistent_application`), so no lower layer imports
`api.adapters`. Defaults to the deterministic `EchoTransport` (offline) — a production wiring
passes an HTTP-backed transport + real API keys instead.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from api.foundation import (
    ConversationMessageRepository,
    KnowledgeChunkRepository,
    KnowledgeCollectionRepository,
    KnowledgeRetriever,
)

from .engine import ProviderExecutionEngine
from .knowledge import KnowledgeToolExecutor, ProviderKnowledgeRetriever, build_embedder
from .memory import DurableMemoryAdapter
from .resilience import CircuitBreaker, RateLimiter
from .tools import ToolExecutorDispatcher, default_executors
from .transport import EchoTransport, Transport


def build_knowledge_retriever(
    *, transport: Transport | None = None, embedding_provider: str = "local",
    model: str = "", api_key: str = "", vector_store: str = "faiss",
) -> ProviderKnowledgeRetriever:
    """Wire the real knowledge retriever (embedding provider + vector store)."""
    from .knowledge import build_vector_store
    transport = transport or EchoTransport()
    return ProviderKnowledgeRetriever(
        build_embedder(embedding_provider, transport, model=model, api_key=api_key),
        build_vector_store(vector_store),
    )


def build_execution_engine(
    messages: ConversationMessageRepository,
    *,
    transport: Transport | None = None,
    api_keys: dict[str, str] | None = None,
    functions: dict[str, Callable[..., Any]] | None = None,
    breaker: CircuitBreaker | None = None,
    limiter: RateLimiter | None = None,
    knowledge_collections: KnowledgeCollectionRepository | None = None,
    knowledge_chunks: KnowledgeChunkRepository | None = None,
    knowledge_retriever: KnowledgeRetriever | None = None,
) -> ProviderExecutionEngine:
    """Wire the real provider engine. `transport` defaults to the deterministic offline
    `EchoTransport`; production injects an HTTP transport + real credentials. When the knowledge
    repos + retriever are supplied (M8.15), the **Knowledge Tool** is wired for the `knowledge`
    category so agents receive retrieved context through Tool execution."""
    transport = transport or EchoTransport()
    knowledge_executor = None
    if knowledge_collections is not None and knowledge_chunks is not None:
        knowledge_executor = KnowledgeToolExecutor(
            collections=knowledge_collections, chunks=knowledge_chunks,
            retriever=knowledge_retriever or ProviderKnowledgeRetriever(
                build_embedder("local", transport)
            ),
        )
    return ProviderExecutionEngine(
        transport,
        memory=DurableMemoryAdapter(messages),
        tools=ToolExecutorDispatcher(
            default_executors(transport, functions=functions, knowledge_executor=knowledge_executor)
        ),
        api_keys=api_keys or {},
        breaker=breaker,
        limiter=limiter,
    )
