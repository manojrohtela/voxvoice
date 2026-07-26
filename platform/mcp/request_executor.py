"""MCPRequestExecutor (M6.8B-2) — protocol execution only.

Owns the request/response exchange for every MCP method over one session's
transport: `tools/list|call`, `resources/list|read`, `prompts/list|get`,
`sampling/createMessage`, notifications, cancellation, and streaming. It **owns
only protocol execution** — never planning, workflow, tool lifecycle, reasoning,
or provider routing. Deterministic given (transport, request-sequence);
capability-guarded by the session's negotiated snapshot; every failure is a
typed `MCPError`.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator, Mapping
from typing import Any

from .domain import MCPCapabilitySnapshot
from .errors import MCPCapabilityError, MCPServerError
from .ids import content_hash
from .protocol import (
    MCPMethod,
    MCPNotification,
    MCPRequest,
    MCPResponseChunk,
    MCPToolCallResult,
)
from .transport import MCPTransport

# MCP method -> the capability kind that must be advertised to use it.
_METHOD_CAPABILITY = {
    MCPMethod.TOOLS_LIST: "tools",
    MCPMethod.TOOLS_CALL: "tools",
    MCPMethod.RESOURCES_LIST: "resources",
    MCPMethod.RESOURCES_READ: "resources",
    MCPMethod.PROMPTS_LIST: "prompts",
    MCPMethod.PROMPTS_GET: "prompts",
    MCPMethod.SAMPLING_CREATE: "sampling",
}


class MCPRequestExecutor:
    def __init__(
        self,
        transport: MCPTransport,
        *,
        snapshot: MCPCapabilitySnapshot | None = None,
        on_frame: Callable[[MCPRequest, Any], None] | None = None,
        clock: Callable[[], float] | None = None,
    ) -> None:
        import time

        self._transport = transport
        self._snapshot = snapshot
        self._on_frame = on_frame  # replay/observability tap (request, response) — set by runtime
        self._clock = clock or time.time
        self._seq = 0

    # ── discovery ──────────────────────────────────────────────────────────────
    def list_tools(self) -> tuple[Mapping[str, Any], ...]:
        result = self._call(MCPMethod.TOOLS_LIST, {})
        return tuple(result.get("tools", ()))

    def list_resources(self) -> tuple[Mapping[str, Any], ...]:
        return tuple(self._call(MCPMethod.RESOURCES_LIST, {}).get("resources", ()))

    def list_prompts(self) -> tuple[Mapping[str, Any], ...]:
        return tuple(self._call(MCPMethod.PROMPTS_LIST, {}).get("prompts", ()))

    # ── execution ──────────────────────────────────────────────────────────────
    def call_tool(self, name: str, arguments: Mapping[str, Any]) -> MCPToolCallResult:
        result = self._call(MCPMethod.TOOLS_CALL, {"name": name, "arguments": dict(arguments)})
        return MCPToolCallResult(
            content=tuple(result.get("content", ())),
            is_error=bool(result.get("isError", False)),
            raw=result,
        )

    def read_resource(self, uri: str) -> Mapping[str, Any]:
        return self._call(MCPMethod.RESOURCES_READ, {"uri": uri})

    def get_prompt(
        self, name: str, arguments: Mapping[str, Any] | None = None
    ) -> Mapping[str, Any]:
        return self._call(MCPMethod.PROMPTS_GET, {"name": name, "arguments": dict(arguments or {})})

    def create_message(self, params: Mapping[str, Any]) -> Mapping[str, Any]:
        """MCP `sampling/createMessage` — a server-initiated LLM request the
        client fulfils. The executor performs the *protocol exchange only*; the
        actual completion is bridged to the platform's Reasoning path elsewhere
        (never owned here)."""
        return self._call(MCPMethod.SAMPLING_CREATE, dict(params))

    def ping(self) -> bool:
        self._request(MCPMethod.PING, {})
        return True

    # ── notifications / cancellation / streaming ───────────────────────────────
    def notify(self, method: str, params: Mapping[str, Any] | None = None) -> None:
        self._transport.notify(MCPNotification(method=method, params=dict(params or {})))

    def cancel(self, request_id: str, *, reason: str = "cancelled") -> None:
        self.notify(MCPMethod.CANCEL, {"requestId": request_id, "reason": reason})

    def stream_tool(
        self, name: str, arguments: Mapping[str, Any]
    ) -> Iterator[MCPResponseChunk]:
        self._guard(MCPMethod.TOOLS_CALL)
        request = self._build(MCPMethod.TOOLS_CALL, {"name": name, "arguments": dict(arguments)})
        yield from self._transport.stream(request)

    # ── internals ──────────────────────────────────────────────────────────────
    def _guard(self, method: str) -> None:
        needed = _METHOD_CAPABILITY.get(method)
        if needed and self._snapshot is not None and not self._snapshot.has(needed):
            raise MCPCapabilityError(
                f"server did not advertise {needed!r}; cannot call {method}"
            )

    def _build(self, method: str, params: Mapping[str, Any]) -> MCPRequest:
        self._seq += 1
        return MCPRequest(
            request_id="req_" + content_hash(method, sorted(params.items()), self._seq),
            method=method, params=dict(params),
        )

    def _request(self, method: str, params: Mapping[str, Any]):
        self._guard(method)
        request = self._build(method, params)
        response = self._transport.request(request)  # may raise MCPTimeoutError/MCPTransportError
        if self._on_frame is not None:
            self._on_frame(request, response)
        return response

    def _call(self, method: str, params: Mapping[str, Any]) -> Mapping[str, Any]:
        response = self._request(method, params)
        if response.is_error:
            raise MCPServerError(response.error.message, code=response.error.code)  # type: ignore[union-attr]
        return response.result or {}
