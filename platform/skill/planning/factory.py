"""PlanningFactory — the composition root for `SkillPlanningRuntime`
(M6.2B-2). Mirrors `tool.observability.factory.ObservabilityFactory`.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from core.errors import ConfigError

from ..config import StoreConfig
from ..extensions import SkillPolicyEngine
from ..registry import SkillRegistry
from ..stores import InMemoryDocumentStore
from .config import PlanningConfiguration
from .planner import SkillExecutionPlanner
from .policy import AllowAllSkillPolicy
from .replay_engine import PlanReplayEngine
from .repository import PlanningRepository
from .runtime import Publisher, SkillPlanningRuntime
from .serializer import PlanningSerializer

_LOCAL = frozenset({"in_memory", "local"})


class PlanningFactory:
    @staticmethod
    def create(
        config: PlanningConfiguration,
        registry: SkillRegistry,
        *,
        policy_engine: SkillPolicyEngine | None = None,
        clock: Callable[[], float] | None = None,
        publish: Publisher | None = None,
        **kwargs: Any,
    ) -> SkillPlanningRuntime:
        import time

        the_clock = clock or time.time
        serializer = PlanningSerializer()
        store = _document_store(config.document_store)
        repository = PlanningRepository(store, serializer, collections=config.collections)

        return SkillPlanningRuntime(
            config=config, repository=repository, registry=registry,
            policy_engine=policy_engine or AllowAllSkillPolicy(),
            planner=SkillExecutionPlanner(), replay_engine=PlanReplayEngine(),
            serializer=serializer, clock=the_clock, publish=publish, **kwargs,
        )

    @staticmethod
    def default(registry: SkillRegistry, **kwargs: Any) -> SkillPlanningRuntime:
        return PlanningFactory.create(PlanningConfiguration.default(), registry, **kwargs)


def _document_store(cfg: StoreConfig) -> InMemoryDocumentStore:
    if cfg.kind in _LOCAL:
        return InMemoryDocumentStore()
    raise ConfigError(f"unknown document store kind {cfg.kind!r}")
