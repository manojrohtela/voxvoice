"""ObservabilityFactory — the composition root for `ToolObservability` (M6.1B-3).
Mirrors `tool.factory.ToolFactory` / `governance.factory.GovernanceFactory`.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from core.errors import ConfigError

from ..config import StoreConfig
from ..stores import InMemoryDocumentStore
from .config import ObservabilityConfiguration
from .metrics import ToolRuntimeMetrics
from .replay_engine import ReplayEngine
from .repository import ReplayRepository
from .runtime import Publisher, ToolObservability
from .serializer import ObservabilitySerializer

_LOCAL = frozenset({"in_memory", "local"})


class ObservabilityFactory:
    @staticmethod
    def create(
        config: ObservabilityConfiguration,
        *,
        clock: Callable[[], float] | None = None,
        publish: Publisher | None = None,
        **kwargs: Any,
    ) -> ToolObservability:
        import time

        the_clock = clock or time.time
        serializer = ObservabilitySerializer()
        store = _document_store(config.document_store)
        repository = ReplayRepository(store, serializer, collections=config.collections)

        return ToolObservability(
            config=config, repository=repository, metrics=ToolRuntimeMetrics(),
            replay_engine=ReplayEngine(), serializer=serializer, clock=the_clock,
            publish=publish, **kwargs,
        )

    @staticmethod
    def default(**kwargs: Any) -> ToolObservability:
        return ObservabilityFactory.create(ObservabilityConfiguration.default(), **kwargs)


def _document_store(cfg: StoreConfig) -> InMemoryDocumentStore:
    if cfg.kind in _LOCAL:
        return InMemoryDocumentStore()
    raise ConfigError(f"unknown document store kind {cfg.kind!r}")
