"""Configuration shapes (M6.7B-1). No runtime logic — validation only."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, ClassVar

from ..errors import IntegrationError

RUNTIME_VERSION = "6.7.0"


@dataclass(frozen=True, slots=True)
class IntegrationConfiguration:
    """The composition root's own tunable knobs — and *only* these (design
    §3.2). Budgets are forwarded verbatim to a capability's own existing field
    (`AgentExecutionEngine.max_revisions`, `LoopStoppingPolicy.max_iterations`)
    — never re-implemented here."""

    enable_plan_revision: bool = True
    max_plan_revisions: int = 1
    max_loop_iterations: int = 5
    max_poll_iterations: int = 64
    trace_id_factory: Callable[[], str] | None = None  # defaults to content-hash, never random()
    # Bounded correlation-store sizes (M6.7.1 — F2). Each store is Integration-
    # owned and grows one entry per request; these caps bound memory on a
    # long-lived shared root. LRU/ring eviction is deterministic given the input
    # sequence — see the store implementations.
    max_correlation_traces: int = 1024   # distinct trace_ids kept in the replay index + span store
    max_spans_per_trace: int = 256       # spans retained per trace
    max_latency_samples: int = 4096      # routing-latency ring-buffer size

    def validate(self) -> None:
        if self.max_plan_revisions < 0:
            raise IntegrationError("max_plan_revisions must be >= 0")
        if self.max_loop_iterations < 1:
            raise IntegrationError("max_loop_iterations must be >= 1")
        if self.max_poll_iterations < 1:
            raise IntegrationError("max_poll_iterations must be >= 1")
        if self.max_correlation_traces < 1:
            raise IntegrationError("max_correlation_traces must be >= 1")
        if self.max_spans_per_trace < 1:
            raise IntegrationError("max_spans_per_trace must be >= 1")
        if self.max_latency_samples < 1:
            raise IntegrationError("max_latency_samples must be >= 1")


@dataclass(frozen=True, slots=True)
class PlatformConfiguration:
    """Holds the *set of already-built runtime instances* the composition root
    wires together, plus the `IntegrationConfiguration` (design §3
    "Configuration flow"). Each field is a capability's own public facade —
    Integration owns none of them, only references them.

    Runtimes are typed `Any` deliberately: importing each capability's concrete
    class *type* here would add nothing (they are already imported where they
    are constructed) and would couple this pure-data shape to every runtime's
    module. Validation is by presence, not by isinstance."""

    provider_pipeline: Any = None      # pipeline.RuntimePipeline (Provider reached via its bridge)
    tool_runtime: Any = None           # tool.ToolRuntime
    skill_runtime: Any = None          # skill.SkillRuntime
    workflow_runtime: Any = None       # workflow.WorkflowRuntime (definition/registry)
    workflow_execution: Any = None     # workflow.execution.WorkflowExecutionRuntime
    agent_execution: Any = None        # agent.execution.AgentExecutionRuntime
    agent_runtime: Any = None          # agent.AgentRuntime (identity/registry)
    integration: IntegrationConfiguration = field(default_factory=IntegrationConfiguration)

    #: The deterministic construction order the composition root guarantees
    #: (design §15 dependency graph; the bundle's "deterministic construction
    #: order" requirement). Provider/Pipeline first, Integration last.
    CONSTRUCTION_ORDER: ClassVar[tuple[str, ...]] = (
        "provider_pipeline",
        "tool_runtime",
        "skill_runtime",
        "workflow_runtime",
        "workflow_execution",
        "loop_engine",
        "agent_execution",
        "integration",
    )

    _REQUIRED: ClassVar[tuple[str, ...]] = (
        "provider_pipeline",
        "tool_runtime",
        "skill_runtime",
        "workflow_runtime",
        "workflow_execution",
        "agent_execution",
        "agent_runtime",
    )

    def validate(self) -> None:
        """Fail at composition time if any required capability was never wired
        (design §12: the sole condition `IntegrationError` is raised for)."""
        missing = [name for name in self._REQUIRED if getattr(self, name) is None]
        if missing:
            raise IntegrationError(
                "PlatformConfiguration is missing required capability instance(s): "
                + ", ".join(missing)
            )
        self.integration.validate()
