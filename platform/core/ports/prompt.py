"""PromptPort — deterministic template rendering interface (M5.0, interface only).
No reasoning, no model calls; renders a registered template + variables into a
neutral fragment. Shapes provisional until the Prompt Runtime lands."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True, slots=True)
class PromptFragment:
    template_id: str
    version: str
    text: str
    metadata: dict[str, Any] = field(default_factory=dict)


class PromptPort(ABC):
    @abstractmethod
    def render(self, template_id: str, variables: Mapping[str, Any]) -> PromptFragment:
        """Render a template with variables into a fragment (pure, deterministic)."""

    @abstractmethod
    def register(self, template_id: str, body: str, *, version: str = "1") -> None:
        """Register/replace a template body."""
