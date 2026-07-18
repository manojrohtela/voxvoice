"""ContextPort — the single assembly seam (M5.0, interface only).

The Context Runtime assembles Memory + Knowledge + Prompt + live input into one
budgeted `AssembledContext`. It depends only on the Memory/Knowledge/Prompt *ports*
(never their packages). No reasoning. Shapes provisional until the Context Runtime
lands."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True, slots=True)
class ContextRequest:
    session_id: str
    org_id: str | None = None
    conversation_id: str | None = None
    query: str | None = None
    budget_tokens: int = 0  # 0 → unbounded
    include_memory: bool = True
    include_knowledge: bool = True


@dataclass(frozen=True, slots=True)
class ContextItem:
    kind: str  # "memory" | "knowledge" | "prompt" | "input"
    text: str
    score: float
    provenance: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class AssembledContext:
    request: ContextRequest
    items: tuple[ContextItem, ...] = ()
    truncated: bool = False
    used_tokens: int = 0


class ContextPort(ABC):
    @abstractmethod
    def assemble(self, request: ContextRequest) -> AssembledContext:
        """Assemble a budgeted, ranked, provenance-tagged context."""
