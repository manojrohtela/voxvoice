"""Typed MCP error hierarchy (M6.8B-2).

One tree under `core.errors.PlatformError` (Constitution §A2.5), owned by the MCP
Runtime. Errors stay **local** to MCP until a boundary translates them — at the
Tool boundary, `MCPToolBridge` lets them propagate and the Tool Runtime
normalizes them into a failed `ToolResult` (design §9). Bundle 1 used bare
`ConfigError` for validation; that remains for *registration/config*, while
these cover *runtime protocol* failures.
"""

from __future__ import annotations

from core.errors import PlatformError


class MCPError(PlatformError):
    """Root of all MCP Runtime runtime errors."""


class MCPTransportError(MCPError):
    """A transport-level failure — connection refused/dropped, TLS failure."""


class MCPProtocolError(MCPError):
    """A malformed frame, version mismatch, or invalid negotiation."""


class MCPAuthenticationError(MCPError):
    """An auth handshake/refresh failure or a missing required credential."""


class MCPTimeoutError(MCPError):
    """A handshake or request deadline was exceeded."""


class MCPCapabilityError(MCPError):
    """A method was requested that the server never advertised."""


class MCPServerError(MCPError):
    """A well-formed JSON-RPC error result returned by the server. Carries the
    protocol error `code` for callers that need it (data, not a transport
    fault)."""

    def __init__(self, message: str, *, code: int | None = None) -> None:
        super().__init__(message)
        self.code = code
