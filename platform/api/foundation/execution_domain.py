"""Conversation execution result domain (M8.14) — additive.

The value objects an `ExecutionEngine` produces when a resolved plan is executed through **real
provider adapters**. These are pure data (no I/O, no provider SDK). The engine + provider
adapters live in the separate `api.adapters` layer (which the Foundation never imports); the
Foundation only defines the seam + these result shapes so the Application can drive real
execution through an injected engine.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


class StreamEventType(StrEnum):
    TOKEN = "token"
    TOOL = "tool"
    VOICE = "voice"
    ERROR = "error"
    DONE = "done"


@dataclass(frozen=True, slots=True)
class TokenUsage:
    input_tokens: int = 0
    output_tokens: int = 0

    @property
    def total(self) -> int:
        return self.input_tokens + self.output_tokens

    def as_dict(self) -> dict[str, int]:
        return {
            "input_tokens": self.input_tokens, "output_tokens": self.output_tokens,
            "total_tokens": self.total,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> TokenUsage:
        return cls(int(d.get("input_tokens", 0)), int(d.get("output_tokens", 0)))


@dataclass(frozen=True, slots=True)
class ExecutionError:
    code: str
    message: str
    provider: str = ""
    retryable: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {
            "code": self.code, "message": self.message, "provider": self.provider,
            "retryable": self.retryable,
        }


@dataclass(frozen=True, slots=True)
class StreamEvent:
    """One event on the execution stream (token / tool / voice / error / done)."""

    seq: int
    type: StreamEventType
    data: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {"seq": self.seq, "type": self.type.value, "data": self.data}


@dataclass(frozen=True, slots=True)
class ConversationMessage:
    """One durably-stored conversation message (M8.14 memory adapter). Append-only per
    session; ordered by `seq`. No vectors, no embeddings — a plain message store."""

    id: str
    org_id: str
    session_id: str
    seq: int
    role: str  # "user" | "assistant" | "tool"
    content: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)
    created_at: float = 0.0

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id, "seq": self.seq, "role": self.role, "content": self.content,
            "metadata": self.metadata, "created_at": self.created_at,
        }


@dataclass(frozen=True, slots=True)
class ExecutionResult:
    """The full result of executing one turn through provider adapters — persisted onto the
    conversation and audited. **No planning logic here** (the plan came from the runtime)."""

    assistant_response: str = ""
    messages: tuple[dict[str, Any], ...] = ()
    tool_results: tuple[dict[str, Any], ...] = ()
    provider_metadata: dict[str, Any] = field(default_factory=dict)
    token_usage: TokenUsage = field(default_factory=TokenUsage)
    latency_ms: int = 0
    errors: tuple[dict[str, Any], ...] = ()
    diagnostics: tuple[dict[str, Any], ...] = ()
    execution_metadata: dict[str, Any] = field(default_factory=dict)
    stream: tuple[dict[str, Any], ...] = ()  # collected stream events (when streamed)

    def as_dict(self) -> dict[str, Any]:
        return {
            "assistant_response": self.assistant_response,
            "messages": list(self.messages),
            "tool_results": list(self.tool_results),
            "provider_metadata": self.provider_metadata,
            "token_usage": self.token_usage.as_dict(),
            "latency_ms": self.latency_ms,
            "errors": list(self.errors),
            "diagnostics": list(self.diagnostics),
            "execution_metadata": self.execution_metadata,
            "stream": list(self.stream),
        }
