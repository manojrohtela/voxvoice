"""AgentPort — agent composition + multi-agent coordination interface (M5.0,
interface only). An agent is a scoped capability set (persona + tools + knowledge +
memory scope + model policy). Shapes provisional until the Agent Runtime lands."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True, slots=True)
class AgentRef:
    agent_id: str
    name: str
    org_id: str | None = None
    config: dict[str, Any] = field(default_factory=dict)


class AgentPort(ABC):
    @abstractmethod
    def resolve(self, agent_id: str) -> AgentRef:
        """Resolve an agent definition."""

    @abstractmethod
    def activate(self, agent_id: str, *, session_id: str) -> AgentRef:
        """Make an agent the active one for a session."""

    @abstractmethod
    def handoff(self, *, session_id: str, to_agent_id: str, reason: str = "") -> AgentRef:
        """Hand the active turn to another agent."""
