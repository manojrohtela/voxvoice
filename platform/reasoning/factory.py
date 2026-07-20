"""ReasoningFactory — the composition root (M5.5B-1).

Assembles a complete `ReasoningRuntime` from a `ReasoningConfiguration` + injected
`ModelProvider`s (the deterministic `EchoProvider` by default). Resolves store kinds to local
adapters, builds the temporary repository, session manager, provider registry, inference
engine, and runtime. The *only* place adapter kinds are named; an unknown kind fails fast
with `ConfigError`. Only local deterministic adapters + the EchoProvider ship in M5.5B-1.

Dependency inversion: the factory takes `ModelProvider` *instances* (never a provider SDK),
so `reasoning` imports no provider client and no sibling runtime.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import Any

from core.errors import ConfigError
from core.providers.stores import DocumentStore, MetadataStore

from .assembler import ResponseAssembler
from .config import ReasoningConfiguration, StoreConfig
from .engine import InferenceEngine, ProviderRegistry
from .ports import ModelProvider
from .repositories import ReasoningRepository
from .runtime import ReasoningRuntime
from .serializer import ReasoningSerializer
from .session import ReasoningSessionManager
from .stores import EchoProvider, InMemoryDocumentStore, InMemoryMetadataStore, ReasoningStore

_LOCAL = frozenset({"in_memory", "local"})


class ReasoningFactory:
    @staticmethod
    def create(
        config: ReasoningConfiguration,
        *,
        providers: Sequence[ModelProvider] = (),
        id_factory: Callable[[], str] | None = None,
        clock: Callable[[], float] | None = None,
        **kwargs: Any,
    ) -> ReasoningRuntime:
        import time

        the_clock = clock or time.time
        serializer = ReasoningSerializer()
        store = ReasoningStore(
            document_store=_document_store(config.document_store),
            metadata_store=_metadata_store(config.metadata_store),
        )
        repository = ReasoningRepository(store, serializer, collections=config.collections)
        sessions = ReasoningSessionManager(repository, clock=the_clock)

        registry = ProviderRegistry()
        registry.register(EchoProvider())  # deterministic default
        for p in providers:
            registry.register(p)

        engine = InferenceEngine(registry=registry, assembler=ResponseAssembler())
        return ReasoningRuntime(
            config=config, repository=repository, engine=engine, sessions=sessions,
            serializer=serializer, id_factory=id_factory, clock=the_clock, **kwargs,
        )

    @staticmethod
    def default(
        *, id_factory: Callable[[], str] | None = None, **kwargs: Any
    ) -> ReasoningRuntime:
        return ReasoningFactory.create(
            ReasoningConfiguration.default(), id_factory=id_factory, **kwargs
        )


def _document_store(cfg: StoreConfig) -> DocumentStore:
    if cfg.kind in _LOCAL:
        return InMemoryDocumentStore()
    raise ConfigError(f"unknown document store kind {cfg.kind!r}")


def _metadata_store(cfg: StoreConfig) -> MetadataStore:
    if cfg.kind in _LOCAL:
        return InMemoryMetadataStore()
    raise ConfigError(f"unknown metadata store kind {cfg.kind!r}")
