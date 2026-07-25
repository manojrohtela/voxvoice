"""Loop Engine configuration (M6.6B-1) — frozen, validated value objects.

Unlike every other Epic 6 capability's own configuration (`AgentConfiguration`,
`WorkflowConfiguration`, ...), `LoopConfiguration` declares **no
`document_store`/`collections`/`*_ttl_s` fields at all** — Loop Engine ships
no persistence of any kind (design §9 ADR item 6). It only declares the
default `LoopStoppingPolicy` a caller's `LoopDefinition` falls back to when
not otherwise overridden.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from core.errors import ConfigError

from .domain import LoopStoppingPolicy

RUNTIME_VERSION = "M6.6B-1"


@dataclass(frozen=True, slots=True)
class LoopConfiguration:
    name: str = "loop"
    default_stopping_policy: LoopStoppingPolicy = field(default_factory=LoopStoppingPolicy)

    def __post_init__(self) -> None:
        if not self.name:
            raise ConfigError("name must be non-empty")
        if self.default_stopping_policy.max_iterations < 1:
            raise ConfigError("default_stopping_policy.max_iterations must be >= 1")
        threshold = self.default_stopping_policy.confidence_threshold
        if threshold is not None and not (0.0 <= threshold <= 1.0):
            raise ConfigError("default_stopping_policy.confidence_threshold must be in [0, 1]")

    @classmethod
    def default(cls) -> LoopConfiguration:
        return cls()

    def fingerprint(self) -> str:
        from .ids import content_hash

        policy = self.default_stopping_policy
        return content_hash(
            self.name, policy.max_iterations, policy.confidence_threshold,
            policy.stop_on_validation_success, policy.stop_on_convergence,
        )
