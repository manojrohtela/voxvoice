"""Conversation Runtime adapter interfaces (M8.12) — **interfaces only**.

The seams the *real* execution plane (frozen Epic 5 Runtime Platform / Epic 6 Execution
Platform) will satisfy in a later milestone. This module defines the abstract contracts and
**nothing else** — no implementations, no provider SDKs, no I/O. The Conversation Runtime in
this milestone produces an execution *plan* against these interfaces; it never instantiates or
invokes one (no LLM call, no tool execution, no voice synthesis).

Keeping the interfaces in the control-plane Foundation (rather than importing the frozen
runtime) preserves the dependency boundary (ADR-0021: the control plane owns no runtime state
and never imports Epic 5/6/7) while giving the future runtime a typed contract to implement.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any


class LLMAdapter(ABC):
    """The model seam. A future runtime supplies a provider-independent implementation;
    M8.12 never calls it."""

    @abstractmethod
    def complete(self, prompt: str, *, options: dict[str, Any]) -> Any:
        """Produce a completion for an assembled prompt. Not invoked in M8.12."""


class VoiceAdapter(ABC):
    """The voice seam (STT/TTS). Interface only — no synthesis in M8.12."""

    @abstractmethod
    def synthesize(self, text: str, *, options: dict[str, Any]) -> Any:
        """Render text to audio. Not invoked in M8.12."""

    @abstractmethod
    def transcribe(self, audio: Any, *, options: dict[str, Any]) -> str:
        """Transcribe audio to text. Not invoked in M8.12."""


class ToolExecutor(ABC):
    """The tool-invocation seam. A resolved tool plan is handed to an implementation of this
    interface by the future runtime; M8.12 only *plans*, never executes."""

    @abstractmethod
    def invoke(self, tool_id: str, *, arguments: dict[str, Any], config: dict[str, Any]) -> Any:
        """Invoke one resolved tool step. Not invoked in M8.12."""


class MemoryAdapter(ABC):
    """The conversation-memory seam. Interface only — no storage/recall in M8.12
    (no message history, no vectors)."""

    @abstractmethod
    def append(self, session_id: str, turn: dict[str, Any]) -> None:
        """Persist a conversation turn. Not invoked in M8.12."""

    @abstractmethod
    def recall(self, session_id: str, *, limit: int) -> list[dict[str, Any]]:
        """Recall recent turns. Not invoked in M8.12."""


# The adapter kinds the runtime plan can require — names only (used in the execution plan's
# adapter-requirement map). The concrete classes above are the contracts a real runtime binds.
ADAPTER_KINDS: tuple[str, ...] = ("llm", "voice", "tool_executor", "memory")
