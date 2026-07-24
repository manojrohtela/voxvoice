"""HookFactory — the composition root for `HookRuntime` (M6.3B). Mirrors
`skill.factory.SkillFactory` / `tool.factory.ToolFactory`.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import Any

from core.errors import ConfigError

from .config import HookConfiguration, StoreConfig
from .domain import HookManifest
from .engine import HookEngine
from .extensions import Hook
from .registry import HookRegistry
from .replay_engine import HookReplayEngine
from .repositories import HookRepository
from .runtime import HookRuntime, Publisher
from .serializer import HookSerializer
from .stores import InMemoryDocumentStore

_LOCAL = frozenset({"in_memory", "local"})


class HookFactory:
    @staticmethod
    def create(
        config: HookConfiguration,
        *,
        hooks: Sequence[tuple[HookManifest, Hook]] = (),
        auto_activate: bool = True,
        clock: Callable[[], float] | None = None,
        publish: Publisher | None = None,
        **kwargs: Any,
    ) -> HookRuntime:
        import time

        the_clock = clock or time.time
        serializer = HookSerializer()
        store = _document_store(config.document_store)
        repository = HookRepository(store, serializer, collections=config.collections)

        registry = HookRegistry(clock=the_clock)
        runtime = HookRuntime(
            config=config, repository=repository, registry=registry,
            engine=HookEngine(), replay_engine=HookReplayEngine(), serializer=serializer,
            clock=the_clock, publish=publish, **kwargs,
        )
        for manifest, hook in hooks:
            descriptor = runtime.register(manifest, hook)
            if auto_activate:
                runtime.activate(manifest.namespace, manifest.id, descriptor.version)
        return runtime

    @staticmethod
    def default(**kwargs: Any) -> HookRuntime:
        return HookFactory.create(HookConfiguration.default(), **kwargs)


def _document_store(cfg: StoreConfig) -> InMemoryDocumentStore:
    if cfg.kind in _LOCAL:
        return InMemoryDocumentStore()
    raise ConfigError(f"unknown document store kind {cfg.kind!r}")
