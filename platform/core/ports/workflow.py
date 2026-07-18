"""WorkflowPort — durable multi-step orchestration interface (M5.0, interface only).
Declarative graphs, not code-as-intelligence. Shapes provisional until the Workflow
Runtime lands."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True, slots=True)
class WorkflowStart:
    workflow_id: str
    session_id: str
    inputs: dict[str, Any] = field(default_factory=dict)
    org_id: str | None = None


@dataclass(frozen=True, slots=True)
class WorkflowHandle:
    instance_id: str
    workflow_id: str
    status: str  # "running" | "paused" | "completed" | "failed"


class WorkflowPort(ABC):
    @abstractmethod
    def start(self, start: WorkflowStart) -> WorkflowHandle:
        """Begin a workflow instance."""

    @abstractmethod
    def signal(self, instance_id: str, name: str, payload: dict[str, Any]) -> None:
        """Deliver an external signal (e.g. a human step's completion)."""

    @abstractmethod
    def status(self, instance_id: str) -> WorkflowHandle:
        """Current instance status (from durable state)."""
