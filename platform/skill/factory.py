"""SkillFactory — the composition root (M6.2B-1).

Assembles a complete `SkillRuntime` from a `SkillConfiguration` + injected
`SkillManifest` registrations. Resolves store kinds to local adapters, builds
the temporary registration repository and the registry. The *only* place
store kinds are named; an unknown kind fails fast with `ConfigError`. Only
local, deterministic, in-memory adapters ship — no external SDK, no network.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import Any

from core.errors import ConfigError

from .config import SkillConfiguration, StoreConfig
from .domain import SkillManifest
from .registry import SkillRegistry
from .repositories import SkillRepository
from .runtime import SkillRuntime
from .serializer import SkillSerializer
from .stores import InMemoryDocumentStore

_LOCAL = frozenset({"in_memory", "local"})


class SkillFactory:
    @staticmethod
    def create(
        config: SkillConfiguration,
        *,
        skills: Sequence[SkillManifest] = (),
        auto_activate: bool = True,
        clock: Callable[[], float] | None = None,
        **kwargs: Any,
    ) -> SkillRuntime:
        import time

        the_clock = clock or time.time
        serializer = SkillSerializer()
        store = _document_store(config.document_store)
        repository = SkillRepository(store, serializer, collections=config.collections)

        registry = SkillRegistry(clock=the_clock)
        runtime = SkillRuntime(
            config=config, repository=repository, registry=registry, clock=the_clock, **kwargs,
        )
        for manifest in skills:
            descriptor = runtime.register(manifest)
            if auto_activate:
                runtime.activate(manifest.namespace, manifest.id, descriptor.version)
        return runtime

    @staticmethod
    def default(**kwargs: Any) -> SkillRuntime:
        return SkillFactory.create(SkillConfiguration.default(), **kwargs)


def _document_store(cfg: StoreConfig) -> InMemoryDocumentStore:
    if cfg.kind in _LOCAL:
        return InMemoryDocumentStore()
    raise ConfigError(f"unknown document store kind {cfg.kind!r}")
