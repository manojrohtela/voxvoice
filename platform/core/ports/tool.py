"""ToolPort — tool registration + execution interface (M5.0, interface only).
Typed, validated, sandboxed dispatch; no reasoning. Shapes provisional until the Tool
Runtime lands."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True, slots=True)
class ToolSpec:
    name: str
    description: str = ""
    schema: Mapping[str, Any] = field(default_factory=dict)  # JSON-schema-like


@dataclass(frozen=True, slots=True)
class ToolCall:
    call_id: str
    name: str
    arguments: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ToolResult:
    call_id: str
    ok: bool
    output: Any = None
    error: str | None = None


class ToolPort(ABC):
    @abstractmethod
    def register(self, spec: ToolSpec) -> None:
        """Register a tool definition."""

    @abstractmethod
    def list(self) -> Sequence[ToolSpec]:
        """The registered tools (a tenant-scoped allow-list at runtime)."""

    @abstractmethod
    async def invoke(self, call: ToolCall) -> ToolResult:
        """Validate + execute a tool call (sandboxed); return a typed result."""
