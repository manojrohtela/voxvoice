"""MCP protocol message shapes (M6.8B-2) — the JSON-RPC 2.0 model, as specified.

Immutable, provider-neutral records mirroring the MCP specification's own
messages. The runtime **adapts to** the protocol — it never redesigns it.
`MCPRequestExecutor` builds these; transports move them as opaque frames.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


class MCPMethod(StrEnum):
    """The MCP methods this runtime speaks (spec method names verbatim)."""

    INITIALIZE = "initialize"
    INITIALIZED = "notifications/initialized"
    PING = "ping"
    TOOLS_LIST = "tools/list"
    TOOLS_CALL = "tools/call"
    RESOURCES_LIST = "resources/list"
    RESOURCES_READ = "resources/read"
    PROMPTS_LIST = "prompts/list"
    PROMPTS_GET = "prompts/get"
    SAMPLING_CREATE = "sampling/createMessage"
    CANCEL = "notifications/cancelled"
    PROGRESS = "notifications/progress"


@dataclass(frozen=True, slots=True)
class MCPRequest:
    """A JSON-RPC request (has an id, expects a response)."""

    request_id: str
    method: str
    params: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class MCPError_:  # noqa: N801 — JSON-RPC "error" object (distinct from the exception hierarchy)
    """A JSON-RPC error object carried inside a response (data, not raised)."""

    code: int
    message: str
    data: Any = None


@dataclass(frozen=True, slots=True)
class MCPResponse:
    """A JSON-RPC response: exactly one of `result` / `error` is set."""

    request_id: str
    result: Mapping[str, Any] | None = None
    error: MCPError_ | None = None

    @property
    def is_error(self) -> bool:
        return self.error is not None


@dataclass(frozen=True, slots=True)
class MCPNotification:
    """A JSON-RPC notification (no id, no response)."""

    method: str
    params: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class MCPResponseChunk:
    """One partial result / progress frame for a streamed request (mirrors
    Provider's `ResponseChunk`)."""

    request_id: str
    index: int
    delta: Mapping[str, Any] = field(default_factory=dict)
    done: bool = False


@dataclass(frozen=True, slots=True)
class MCPToolCallResult:
    """The typed result of a `tools/call` — content blocks + the server's own
    `isError` flag (the server reporting a tool-level error is data, distinct
    from a transport/protocol failure)."""

    content: tuple[Mapping[str, Any], ...] = ()
    is_error: bool = False
    raw: Mapping[str, Any] = field(default_factory=dict)

    @property
    def text(self) -> str:
        """Concatenated text of any text content blocks (convenience)."""
        return "".join(str(b.get("text", "")) for b in self.content if b.get("type") == "text")
