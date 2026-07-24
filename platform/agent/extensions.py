"""Agent Runtime extension points (M6.5B-1) — interfaces only.

Six declared, unimplemented seams, per the M6.5A design (§6, §9, §10.5):

- `AgentCapabilityResolver` — mirrors `skill.extensions.CapabilityResolver`
  a second time: resolves one declared `AgentCapability` requirement against
  whichever registry (Skill/Tool/Workflow) its `kind` implies. Agent Runtime
  never imports those registries directly to do this itself.
- `AgentWorkflowPort` — the seam into Workflow execution (`resolve`/
  `submit`/`status`/`signal`), never `core.ports.workflow.WorkflowPort`
  directly, since `workflow.execution.WorkflowExecutionRuntime`'s literal
  conformance to that frozen M5.0 port was never verified (the same honest,
  unresolved gap as Tool Runtime's own M6.1B-5 debt) — depending on it here
  would silently assume an unverified conformance shim.
- `AgentHookAdapter` — mirrors `workflow.extensions.WorkflowHookAdapter`
  exactly, since Hook Framework exposes no `core.ports` entry either.
- `AgentPolicyEngine`, `AgentObservabilitySink`, `AgentReplaySink` — reserved
  for Bundle 3 (execution/observability/replay). Ownership declared only —
  no implementation in this bundle.

**Not declared here, by design**: no seam for Reasoning/Memory/Knowledge/
Prompt/Context. Per §8/§11 item 4 of the M6.5A design, those five concrete
Epic 5 runtimes already literally implement their respective `core.ports`
interfaces (verified by direct inspection), so Agent Runtime depends on
those frozen ports directly wherever it needs them — a duplicate seam here
would be pure ceremony with no honesty benefit. This bundle declares no
planning/reasoning call sites at all (an explicit Bundle 2 non-goal), so
none of the five ports are imported here either.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Mapping
from typing import Any

__all__ = [
    "AgentCapabilityResolver",
    "AgentWorkflowPort",
    "AgentHookAdapter",
    "AgentPolicyEngine",
    "AgentObservabilitySink",
    "AgentReplaySink",
]


class AgentCapabilityResolver(ABC):
    """Resolves one declared `AgentCapability` requirement against
    whichever registry (Skill/Tool/Workflow) the `kind` implies — never
    implemented by Agent Runtime itself. A future composition root injects a
    concrete adapter."""

    @abstractmethod
    def resolve(self, requirement: Any) -> Any: ...


class AgentWorkflowPort(ABC):
    """The Agent-owned seam into Workflow execution — never implemented by
    Agent Runtime itself, and never assumed to be satisfied by
    `core.ports.workflow.WorkflowPort` (see this module's own docstring)."""

    @abstractmethod
    def resolve(self, workflow_ref: str) -> str:
        """Resolve a namespace.id[@version] reference to a concrete,
        registered workflow; return an opaque handle. Raises if
        unregistered."""

    @abstractmethod
    def submit(self, handle: str, inputs: Mapping[str, Any]) -> str:
        """Submit an execution request; return an execution_id. Never
        blocks on completion."""

    @abstractmethod
    def status(self, execution_id: str) -> str:
        """One of the WorkflowExecutionState values, as a string."""

    @abstractmethod
    def signal(self, execution_id: str, name: str, payload: Mapping[str, Any]) -> None:
        """Deliver an external signal (mirrors `resume_after_event`)."""


class AgentHookAdapter(ABC):
    """The sole seam into Hook Framework — never implemented by Agent
    Runtime itself, mirroring `workflow.extensions.WorkflowHookAdapter`'s
    identical posture."""

    @abstractmethod
    def run(self, point: str, payload: Mapping[str, Any]) -> None: ...


class AgentPolicyEngine(ABC):
    """Reserved: allow/deny/visibility verdicts over agent registration and
    goal-level decisions (Bundle 3). Ownership only — no implementation in
    this bundle."""

    @abstractmethod
    def authorize(self, descriptor: object) -> object: ...


class AgentObservabilitySink(ABC):
    """Reserved: goal/decision/delegation audit + metrics (Bundle 3).
    Ownership only — no implementation in this bundle."""

    @abstractmethod
    def record(self, event: object) -> None: ...


class AgentReplaySink(ABC):
    """Reserved: decision-chain replay-determinism verification metadata
    (Bundle 3). Ownership only — no implementation in this bundle (this
    bundle has no decision artifact to replay yet)."""

    @abstractmethod
    def record(self, replay: object) -> None: ...
