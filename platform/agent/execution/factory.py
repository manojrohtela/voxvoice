"""AgentExecutionFactory — the composition root (M6.5B-3).

Assembles a complete `AgentExecutionRuntime` from an
`AgentExecutionConfiguration` + injected seams. Only local, deterministic,
in-memory adapters ship — no external SDK, no network.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from core.errors import ConfigError
from core.ports.context import ContextPort
from core.ports.reasoning import ReasoningPort

from ..config import StoreConfig
from ..extensions import AgentCapabilityResolver, AgentWorkflowPort
from ..stores import InMemoryDocumentStore
from .config import AgentExecutionConfiguration
from .repositories import AgentGoalExecutionRepository
from .runtime import AgentExecutionRuntime
from .serializer import AgentExecutionSerializer

_LOCAL = frozenset({"in_memory", "local"})


class AgentExecutionFactory:
    @staticmethod
    def create(
        config: AgentExecutionConfiguration,
        *,
        workflow_port: AgentWorkflowPort | None = None,
        capability_resolver: AgentCapabilityResolver | None = None,
        reasoning_port: ReasoningPort | None = None,
        context_port: ContextPort | None = None,
        clock: Callable[[], float] | None = None,
        **kwargs: Any,
    ) -> AgentExecutionRuntime:
        serializer = AgentExecutionSerializer()
        store = _document_store(config.document_store)
        repository = AgentGoalExecutionRepository(store, serializer, collections=config.collections)

        return AgentExecutionRuntime(
            config=config, repository=repository, workflow_port=workflow_port,
            capability_resolver=capability_resolver, reasoning_port=reasoning_port,
            context_port=context_port, clock=clock, **kwargs,
        )

    @staticmethod
    def default(**kwargs: Any) -> AgentExecutionRuntime:
        return AgentExecutionFactory.create(AgentExecutionConfiguration.default(), **kwargs)


def _document_store(cfg: StoreConfig) -> InMemoryDocumentStore:
    if cfg.kind in _LOCAL:
        return InMemoryDocumentStore()
    raise ConfigError(f"unknown document store kind {cfg.kind!r}")
