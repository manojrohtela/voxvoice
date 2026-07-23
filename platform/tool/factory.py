"""ToolFactory — the composition root (M6.1B-1).

Assembles a complete `ToolRuntime` from a `ToolConfiguration` + injected
`(ToolManifest, ToolAdapter)` registrations (the deterministic `EchoToolAdapter` by
default). Resolves store kinds to local adapters, builds the temporary repository,
registry, result normalizer, and runtime. The *only* place store kinds are named;
an unknown kind fails fast with `ConfigError`. Only local deterministic adapters +
the Echo adapter ship.

Dependency inversion: the factory takes `ToolAdapter` *instances* (never a vendor
SDK or dynamically loaded code), so `tool` imports no external integration and no
sibling runtime.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import Any

from core.errors import ConfigError
from core.providers.stores import DocumentStore

from .config import StoreConfig, ToolConfiguration
from .domain import Classification, ToolManifest
from .ports import ToolAdapter
from .registry import ToolRegistry
from .repositories import ToolRepository
from .results import ResultNormalizer
from .runtime import ToolRuntime
from .serializer import ToolSerializer
from .stores import EchoToolAdapter, InMemoryDocumentStore

_LOCAL = frozenset({"in_memory", "local"})


class ToolFactory:
    @staticmethod
    def create(
        config: ToolConfiguration,
        *,
        tools: Sequence[tuple[ToolManifest, ToolAdapter]] = (),
        auto_activate: bool = True,
        id_factory: Callable[[], str] | None = None,
        clock: Callable[[], float] | None = None,
        **kwargs: Any,
    ) -> ToolRuntime:
        import time

        the_clock = clock or time.time
        serializer = ToolSerializer()
        store = _document_store(config.document_store)
        repository = ToolRepository(store, serializer, collections=config.collections)

        registry = ToolRegistry(clock=the_clock)
        registered: list[ToolManifest] = [_echo_manifest(), *[m for m, _a in tools]]
        adapters: list[ToolAdapter] = [EchoToolAdapter(), *[a for _m, a in tools]]
        for manifest, adapter in zip(registered, adapters, strict=True):
            registry.register(manifest, adapter)
            if auto_activate:
                registry.activate(manifest.namespace, manifest.name, manifest.version)

        normalizer = ResultNormalizer()

        return ToolRuntime(
            config=config, repository=repository, registry=registry, normalizer=normalizer,
            serializer=serializer, id_factory=id_factory, clock=the_clock, **kwargs,
        )

    @staticmethod
    def default(
        *, id_factory: Callable[[], str] | None = None, **kwargs: Any
    ) -> ToolRuntime:
        return ToolFactory.create(ToolConfiguration.default(), id_factory=id_factory, **kwargs)


def _echo_manifest() -> ToolManifest:
    return ToolManifest(
        name="echo", namespace="internal", version="1.0.0",
        description="Deterministic offline echo tool (dev/test default).",
        capabilities=frozenset({"echo"}), classification=Classification.READ_ONLY,
    )


def _document_store(cfg: StoreConfig) -> DocumentStore:
    if cfg.kind in _LOCAL:
        return InMemoryDocumentStore()
    raise ConfigError(f"unknown document store kind {cfg.kind!r}")
