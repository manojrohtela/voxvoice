"""Large Language Model capability contract (Constitution §A6). Interface only."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True, slots=True)
class LLMCapabilities:
    tools: bool
    structured_output: bool
    context_window: int
    #: Model identifiers this provider can serve, as opaque provider-declared strings (M11.4).
    #: Empty means *"this provider does not enumerate models"* — the honest declaration for the
    #: local profile — and the runtime then accepts any selection rather than inventing a
    #: whitelist. A non-empty tuple is validated at configuration time (AR-22, AR-25), which is
    #: what turns `llm_model` from an accepted-and-ignored field into a real one.
    models: tuple[str, ...] = field(default_factory=tuple)


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
        self,
        *,
        context: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        model: str | None = None,
    ) -> AsyncIterator[TokenDelta | ToolCallDelta]:
        """Stream token and tool-call deltas for the assembled context.

        `model` is the session's selected model (M11.4), an **opaque provider-declared string** —
        never a vendor branch the runtime reads (AR-21). It is additive with a `None` default, so
        every existing implementation and call site is unaffected; `None` means *"the provider's
        own default"*. Adding it is what discharges AR-25 for `llm_model`, which the Control Plane
        has accepted since M2C.2 and the runtime has never read."""
