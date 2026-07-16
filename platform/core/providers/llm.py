"""Large Language Model capability contract (Constitution §A6). Interface only."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class LLMCapabilities:
    tools: bool
    structured_output: bool
    context_window: int


@dataclass(frozen=True, slots=True)
class TokenDelta:
    text: str


@dataclass(frozen=True, slots=True)
class ToolCallDelta:
    name: str
    arguments_fragment: str


class LLMProvider(ABC):
    name: str
    capabilities: LLMCapabilities

    @abstractmethod
    async def stream(
        self, *, context: list[dict[str, Any]], tools: list[dict[str, Any]] | None = None
    ) -> AsyncIterator[TokenDelta | ToolCallDelta]:
        """Stream token and tool-call deltas for the assembled context."""
