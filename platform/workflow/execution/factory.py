"""WorkflowExecutionFactory — the composition root for
`WorkflowExecutionRuntime` (M6.4B-2). Mirrors `skill.planning.factory.
PlanningFactory`'s shape.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from core.errors import ConfigError

from ..extensions import WorkflowHookAdapter, WorkflowSkillPort
from ..registry import WorkflowRegistry
from ..stores import InMemoryDocumentStore
from .config import StoreConfig, WorkflowExecutionConfiguration
from .observability import InMemoryWorkflowObservabilitySink
from .replay_engine import WorkflowReplayEngine
from .repositories import WorkflowExecutionRepository
from .runtime import Publisher, WorkflowExecutionRuntime
from .serializer import WorkflowExecutionSerializer

_LOCAL = frozenset({"in_memory", "local"})


class WorkflowExecutionFactory:
    @staticmethod
    def create(
        config: WorkflowExecutionConfiguration,
        registry: WorkflowRegistry,
        *,
        tool_port: Any | None = None,
        skill_port: WorkflowSkillPort | None = None,
        hook_adapter: WorkflowHookAdapter | None = None,
        clock: Callable[[], float] | None = None,
        publish: Publisher | None = None,
        **kwargs: Any,
    ) -> WorkflowExecutionRuntime:
        import time

        the_clock = clock or time.time
        serializer = WorkflowExecutionSerializer()
        store = _document_store(config.document_store)
        repository = WorkflowExecutionRepository(store, serializer, collections=config.collections)

        return WorkflowExecutionRuntime(
            config=config, repository=repository, registry=registry, tool_port=tool_port,
            skill_port=skill_port, hook_adapter=hook_adapter,
            observability_sink=InMemoryWorkflowObservabilitySink(),
            replay_engine=WorkflowReplayEngine(), serializer=serializer, clock=the_clock,
            publish=publish, **kwargs,
        )

    @staticmethod
    def default(registry: WorkflowRegistry, **kwargs: Any) -> WorkflowExecutionRuntime:
        return WorkflowExecutionFactory.create(
            WorkflowExecutionConfiguration.default(), registry, **kwargs
        )


def _document_store(cfg: StoreConfig) -> InMemoryDocumentStore:
    if cfg.kind in _LOCAL:
        return InMemoryDocumentStore()
    raise ConfigError(f"unknown document store kind {cfg.kind!r}")
