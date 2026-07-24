"""AgentFactory — the composition root (M6.5B-1).

Assembles a complete `AgentRuntime` from an `AgentConfiguration` + injected
`AgentManifest` registrations. Resolves store kinds to local adapters,
builds the temporary registration repository and the registry. Only local,
deterministic, in-memory adapters ship — no external SDK, no network.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import Any

from core.errors import ConfigError

from .config import AgentConfiguration, StoreConfig
from .domain import AgentManifest
from .registry import AgentRegistry
from .repositories import AgentRepository
from .runtime import AgentRuntime
from .serializer import AgentSerializer
from .stores import InMemoryDocumentStore

_LOCAL = frozenset({"in_memory", "local"})


class AgentFactory:
    @staticmethod
    def create(
        config: AgentConfiguration,
        *,
        agents: Sequence[AgentManifest] = (),
        auto_activate: bool = True,
        clock: Callable[[], float] | None = None,
        **kwargs: Any,
    ) -> AgentRuntime:
        import time

        the_clock = clock or time.time
        serializer = AgentSerializer()
        store = _document_store(config.document_store)
        repository = AgentRepository(store, serializer, collections=config.collections)

        registry = AgentRegistry(clock=the_clock)
        runtime = AgentRuntime(
            config=config, repository=repository, registry=registry, clock=the_clock, **kwargs,
        )
        for manifest in agents:
            descriptor = runtime.register(manifest)
            if auto_activate:
                runtime.activate(manifest.namespace, manifest.id, descriptor.version)
        return runtime

    @staticmethod
    def default(**kwargs: Any) -> AgentRuntime:
        return AgentFactory.create(AgentConfiguration.default(), **kwargs)


def _document_store(cfg: StoreConfig) -> InMemoryDocumentStore:
    if cfg.kind in _LOCAL:
        return InMemoryDocumentStore()
    raise ConfigError(f"unknown document store kind {cfg.kind!r}")
