"""Workflow Runtime extension points (M6.4B-1) — interfaces only.

Neither Skill Runtime nor Hook Framework exposes a `core.ports` entry (per
their own already-ratified ADRs), so Workflow Runtime declares its **own**
seam interfaces for both rather than importing either package directly —
`WorkflowSkillPort` mirrors `skill.extensions.CapabilityResolver`'s posture
exactly: a future composition root injects a concrete adapter wrapping the
real Skill/Hook surface; Workflow Runtime never implements one itself and
never imports `skill`/`hooks` to check.

Reserved for: `WorkflowPolicyEngine`, `WorkflowObservabilitySink`,
`WorkflowReplaySink` (all Bundle 2, ownership declared only per the M6.4A
design §10). None of this changes `registry.py`, `graph.py`, or the
repository.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

__all__ = [
    "WorkflowSkillPort",
    "WorkflowHookAdapter",
    "WorkflowPolicyEngine",
    "WorkflowObservabilitySink",
    "WorkflowReplaySink",
]


class WorkflowSkillPort(ABC):
    """The seam into Skill execution — never implemented by Workflow Runtime
    itself. A future caller (most likely Agent Runtime, the first runtime
    with genuine cross-capability execution authority) injects a concrete
    adapter wrapping the real Skill Runtime surface."""

    @abstractmethod
    def run_skill(self, skill_ref: tuple[str, str, str], variables: Any) -> Any: ...


class WorkflowHookAdapter(ABC):
    """The sole seam into Hook Framework — never implemented by Workflow
    Runtime itself. A future composition root injects a concrete adapter
    wrapping the real `hooks.HookRuntime.run(...)`, mirroring
    `skill.extensions.CapabilityResolver`'s identical posture."""

    @abstractmethod
    def run(self, point: str, event: Any) -> Any: ...


class WorkflowPolicyEngine(ABC):
    """Reserved: allow/deny/visibility verdicts over workflow registration and
    node-level failure handling (Bundle 2). Ownership only — no
    implementation in this bundle."""

    @abstractmethod
    def authorize(self, descriptor: object) -> object: ...


class WorkflowObservabilitySink(ABC):
    """Reserved: execution/checkpoint/recovery audit + metrics (Bundle 2).
    Ownership only — no implementation in this bundle."""

    @abstractmethod
    def record(self, event: object) -> None: ...


class WorkflowReplaySink(ABC):
    """Reserved: checkpoint-chain replay-determinism verification metadata
    (Bundle 2). Ownership only — no implementation in this bundle (this
    bundle has no execution artifact to replay yet — only a future
    checkpoint chain will)."""

    @abstractmethod
    def record(self, replay: object) -> None: ...
