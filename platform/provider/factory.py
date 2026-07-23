"""ProviderFactory — the composition root (M5.6B-1).

Assembles a complete `ProviderRuntime` from a `ProviderConfiguration` + injected
`(ProviderDescriptor, AdapterPort)` registrations (the deterministic `EchoProviderAdapter` by
default). Resolves store kinds to local adapters, builds the temporary repository, registry,
response normalizer, and runtime. The *only* place adapter kinds are named; an unknown kind
fails fast with `ConfigError`. Only local deterministic adapters + the Echo adapter ship in
M5.6B-1.

Dependency inversion: the factory takes `AdapterPort` *instances* (never a vendor SDK), so
`provider` imports no vendor client and no sibling runtime.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import Any

from core.errors import ConfigError
from core.providers.stores import DocumentStore, MetadataStore

from .config import ProviderConfiguration, StoreConfig
from .domain import ProviderDescriptor
from .ports import AdapterPort
from .registry import ProviderRegistry
from .repositories import ProviderRepository
from .response import ResponseNormalizer
from .runtime import ProviderRuntime
from .serializer import ProviderSerializer
from .session import ProviderSessionManager
from .stores import EchoProviderAdapter, InMemoryDocumentStore, InMemoryMetadataStore, ProviderStore

_LOCAL = frozenset({"in_memory", "local"})


class ProviderFactory:
    @staticmethod
    def create(
        config: ProviderConfiguration,
        *,
        adapters: Sequence[tuple[ProviderDescriptor, AdapterPort]] = (),
        id_factory: Callable[[], str] | None = None,
        clock: Callable[[], float] | None = None,
        **kwargs: Any,
    ) -> ProviderRuntime:
        import time

        the_clock = clock or time.time
        serializer = ProviderSerializer()
        store = ProviderStore(
            document_store=_document_store(config.document_store),
            metadata_store=_metadata_store(config.metadata_store),
        )
        repository = ProviderRepository(store, serializer, collections=config.collections)
        sessions = ProviderSessionManager(repository, clock=the_clock)

        registry = ProviderRegistry(clock=the_clock)
        registry.register(_echo_descriptor(), EchoProviderAdapter())  # deterministic default
        for descriptor, adapter in adapters:
            registry.register(descriptor, adapter)

        normalizer = ResponseNormalizer()
        return ProviderRuntime(
            config=config, repository=repository, registry=registry, normalizer=normalizer,
            sessions=sessions, serializer=serializer, id_factory=id_factory, clock=the_clock,
            **kwargs,
        )

    @staticmethod
    def default(
        *, id_factory: Callable[[], str] | None = None, **kwargs: Any
    ) -> ProviderRuntime:
        return ProviderFactory.create(
            ProviderConfiguration.default(), id_factory=id_factory, **kwargs
        )


def _echo_descriptor() -> ProviderDescriptor:
    return ProviderDescriptor(
        name="echo", version="1", capabilities=EchoProviderAdapter.capabilities
    )


def _document_store(cfg: StoreConfig) -> DocumentStore:
    if cfg.kind in _LOCAL:
        return InMemoryDocumentStore()
    raise ConfigError(f"unknown document store kind {cfg.kind!r}")


def _metadata_store(cfg: StoreConfig) -> MetadataStore:
    if cfg.kind in _LOCAL:
        return InMemoryMetadataStore()
    raise ConfigError(f"unknown metadata store kind {cfg.kind!r}")
