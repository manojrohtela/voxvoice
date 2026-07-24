"""WorkflowFactory — the composition root (M6.4B-1).

Assembles a complete `WorkflowRuntime` from a `WorkflowConfiguration` +
injected `WorkflowManifest` registrations. Resolves store kinds to local
adapters, builds the temporary registration repository and the registry.
Only local, deterministic, in-memory adapters ship — no external SDK, no
network.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import Any

from core.errors import ConfigError

from .config import StoreConfig, WorkflowConfiguration
from .domain import WorkflowManifest
from .registry import WorkflowRegistry
from .repositories import WorkflowRepository
from .runtime import WorkflowRuntime
from .serializer import WorkflowSerializer
from .stores import InMemoryDocumentStore

_LOCAL = frozenset({"in_memory", "local"})


class WorkflowFactory:
    @staticmethod
    def create(
        config: WorkflowConfiguration,
        *,
        workflows: Sequence[WorkflowManifest] = (),
        auto_activate: bool = True,
        clock: Callable[[], float] | None = None,
        **kwargs: Any,
    ) -> WorkflowRuntime:
        import time

        the_clock = clock or time.time
        serializer = WorkflowSerializer()
        store = _document_store(config.document_store)
        repository = WorkflowRepository(store, serializer, collections=config.collections)

        registry = WorkflowRegistry(clock=the_clock)
        runtime = WorkflowRuntime(
            config=config, repository=repository, registry=registry, clock=the_clock, **kwargs,
        )
        for manifest in workflows:
            descriptor = runtime.register(manifest)
            if auto_activate:
                runtime.activate(manifest.namespace, manifest.id, descriptor.version)
        return runtime

    @staticmethod
    def default(**kwargs: Any) -> WorkflowRuntime:
        return WorkflowFactory.create(WorkflowConfiguration.default(), **kwargs)


def _document_store(cfg: StoreConfig) -> InMemoryDocumentStore:
    if cfg.kind in _LOCAL:
        return InMemoryDocumentStore()
    raise ConfigError(f"unknown document store kind {cfg.kind!r}")
