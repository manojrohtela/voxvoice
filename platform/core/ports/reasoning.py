"""ReasoningPort — the model-call loop interface (M5.0, interface only).

Coordinates a model capability + tool calls into decisions; the *intelligence* lives
in the model behind a provider capability, not in code. Shapes provisional until the
Reasoning Runtime lands."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Iterator
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


class DecisionKind(StrEnum):
    TOKEN = "token"  # a streamed output token
    TOOL_CALL = "tool_call"  # a requested tool invocation
    FINAL = "final"  # the completed response
    HANDOFF = "handoff"  # hand to another agent


@dataclass(frozen=True, slots=True)
class ReasoningRequest:
    session_id: str
    context_ref: str  # opaque handle to an AssembledContext
    model: str | None = None
    org_id: str | None = None


@dataclass(frozen=True, slots=True)
class Decision:
    kind: DecisionKind
    payload: dict[str, Any] = field(default_factory=dict)


class ReasoningPort(ABC):
    @abstractmethod
    def run(self, request: ReasoningRequest) -> Iterator[Decision]:
        """Stream reasoning decisions (tokens / tool calls / final)."""
