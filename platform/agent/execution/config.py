"""Agent goal execution configuration (M6.5B-3) — frozen, validated value
objects.

Unlike Workflow Runtime's execution configuration (which deliberately omits
any `*_ttl_s` field, since workflow execution state is the platform's one
named durable-repository exception), Agent Runtime's own goal-execution
repository stays the platform's **standard temporary convention** (§10.3 of
the M6.5A design): a goal's state is fully reconstructible from its
content-hash-chained `AgentDecision` history plus the delegated workflow's
own durable snapshot, so no second durable exception is warranted here.
`execution_ttl_s` is therefore present, mirroring `SkillConfiguration.
registration_ttl_s`'s own convention.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from core.errors import ConfigError

from ..config import StoreConfig

RUNTIME_VERSION = "M6.5B-3"


@dataclass(frozen=True, slots=True)
class AgentExecutionCollectionsConfig:
    executions: str = "agent_goal_executions"
    snapshots: str = "agent_goal_snapshots"
    replays: str = "agent_goal_replays"
    traces: str = "agent_goal_traces"


@dataclass(frozen=True, slots=True)
class AgentExecutionConfiguration:
    name: str = "agent_execution"
    document_store: StoreConfig = field(default_factory=StoreConfig)
    collections: AgentExecutionCollectionsConfig = field(
        default_factory=AgentExecutionCollectionsConfig
    )
    max_revisions: int = 1
    execution_ttl_s: float | None = None  # temporary registration-record TTL; None -> no expiry

    def __post_init__(self) -> None:
        if not self.name:
            raise ConfigError("name must be non-empty")
        if self.max_revisions < 0:
            raise ConfigError("max_revisions must be >= 0")
        if self.execution_ttl_s is not None and self.execution_ttl_s <= 0:
            raise ConfigError("execution_ttl_s must be > 0 or None")

    @classmethod
    def default(cls) -> AgentExecutionConfiguration:
        return cls()

    def fingerprint(self) -> str:
        from ..ids import content_hash

        return content_hash(
            self.name, self.document_store.kind, self.collections.executions,
            self.collections.snapshots, self.collections.replays, self.collections.traces,
            self.max_revisions, self.execution_ttl_s,
        )
