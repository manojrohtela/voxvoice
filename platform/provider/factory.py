"""ProviderFactory — the composition root (M5.6B-1 core + M5.6B-2 intelligence).

Assembles a complete `ProviderRuntime` from a `ProviderConfiguration` + injected
`(ProviderDescriptor, AdapterPort)` registrations (the deterministic `EchoProviderAdapter` by
default). Resolves store kinds to local adapters, builds the temporary repository, registry,
response normalizer, and runtime. The *only* place adapter kinds are named; an unknown kind
fails fast with `ConfigError`. Only local deterministic adapters + the Echo adapter ship.

`intelligence=True` additionally wires `ProviderRouter`/`ProviderHealthManager`/
`FailoverController`/`CircuitBreaker`/`ProviderPolicyEngine`/`ProviderTenancy` from
`config.policies`; `intelligence=False` (the default) yields the exact M5.6B-1 core runtime.

Dependency inversion: the factory takes `AdapterPort` *instances* (never a vendor SDK), so
`provider` imports no vendor client and no sibling runtime.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import Any

from core.errors import ConfigError
from core.providers.stores import DocumentStore, MetadataStore

from .catalog import CapabilityCatalog
from .circuit_breaker import CircuitBreaker
from .config import ProviderConfiguration, StoreConfig
from .domain import ProviderDescriptor
from .failover import FailoverController
from .health import ProviderHealthManager
from .policies import ProviderPolicyEngine
from .ports import AdapterPort
from .registry import ProviderRegistry
from .repositories import ProviderRepository
from .response import ResponseNormalizer
from .router import ProviderRouter
from .runtime import ProviderRuntime
from .serializer import ProviderSerializer
from .session import ProviderSessionManager
from .stores import EchoProviderAdapter, InMemoryDocumentStore, InMemoryMetadataStore, ProviderStore
from .tenancy import ProviderTenancy

_LOCAL = frozenset({"in_memory", "local"})


class ProviderFactory:
    @staticmethod
    def create(
        config: ProviderConfiguration,
        *,
        adapters: Sequence[tuple[ProviderDescriptor, AdapterPort]] = (),
        intelligence: bool = False,
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

        # ── intelligence engines (wired only when enabled) ────────────────────
        router = health = failover = circuit_breaker = policy = tenancy = None
        if intelligence:
            p = config.policies
            policy = ProviderPolicyEngine(p)
            catalog = CapabilityCatalog(registry)
            router = ProviderRouter(registry, catalog, policy_engine=policy)
            health = ProviderHealthManager(registry)
            failover = FailoverController()
            circuit_breaker = CircuitBreaker(clock=the_clock)
            tenancy = ProviderTenancy(
                allowed_providers=p.access.allowed_providers,
                allowed_regions=p.region.allowed_regions, clock=the_clock,
            )

        return ProviderRuntime(
            config=config, repository=repository, registry=registry, normalizer=normalizer,
            sessions=sessions, serializer=serializer, router=router, health=health,
            failover=failover, circuit_breaker=circuit_breaker, policy=policy, tenancy=tenancy,
            id_factory=id_factory, clock=the_clock, **kwargs,
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
