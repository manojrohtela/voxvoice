"""Workflow execution configuration (M6.4B-2) — frozen, validated value
objects.

**No `*_ttl_s` field exists here**, unlike every other closed runtime's
temporary-repository configuration (`SkillConfiguration.registration_ttl_s`,
`ObservabilityConfiguration.replay_ttl_s`, ...) — the absence itself is the
durability signal (§7 of the M6.4A design: workflow execution state is the
platform's one deliberate durable-repository exception). The shipped
`document_store` default (`kind="in_memory"`) is still the same
deterministic, offline dev adapter every closed runtime ships by default —
it is **not**, by itself, magically durable; genuine durability requires
configuring `document_store.kind` to a real, persistent adapter (Postgres/
S3-backed) at deployment time, exactly like every other closed runtime's
factory dispatches by `kind` and fails fast on an unknown one.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from core.errors import ConfigError

from ..config import StoreConfig

RUNTIME_VERSION = "M6.4B-2"


@dataclass(frozen=True, slots=True)
class WorkflowExecutionCollectionsConfig:
    executions: str = "workflow_executions"
    checkpoints: str = "workflow_checkpoints"
    snapshots: str = "workflow_snapshots"
    replays: str = "workflow_execution_replays"
    traces: str = "workflow_execution_traces"


@dataclass(frozen=True, slots=True)
class WorkflowExecutionConfiguration:
    name: str = "workflow_execution"
    document_store: StoreConfig = field(default_factory=StoreConfig)
    collections: WorkflowExecutionCollectionsConfig = field(
        default_factory=WorkflowExecutionCollectionsConfig
    )

    def __post_init__(self) -> None:
        if not self.name:
            raise ConfigError("name must be non-empty")

    @classmethod
    def default(cls) -> WorkflowExecutionConfiguration:
        return cls()

    def fingerprint(self) -> str:
        from ..ids import content_hash

        return content_hash(
            self.name, self.document_store.kind, self.collections.executions,
            self.collections.checkpoints, self.collections.snapshots,
            self.collections.replays, self.collections.traces,
        )
