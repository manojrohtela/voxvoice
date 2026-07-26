"""MCP transport abstraction + the deterministic loopback default.

`MCPTransport` is the pluggable wire. **M6.8B-1** exposed the `initialize`
handshake; **M6.8B-2** additively extends the seam with general request
execution (`request`), notifications (`notify`), and streaming (`stream`) — the
request surface every method (`tools/call`, `resources/read`, …) flows through.
`LoopbackTransport` wraps an in-process `EchoMCPServer` for fully-offline,
deterministic execution with no external dependency (the `EchoToolAdapter`
precedent). Real transports (stdio / http-sse / streamable-http) live in
`mcp.transports` behind this same seam and are configuration-selected.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Iterator

from core.errors import ConfigError

from .config import SUPPORTED_PROTOCOL_VERSIONS
from .domain import (
    MCPCapability,
    MCPHandshakeRequest,
    MCPHandshakeResult,
    MCPTransportDescriptor,
    MCPTransportKind,
)
from .errors import MCPTimeoutError
from .protocol import (
    MCPError_,
    MCPMethod,
    MCPNotification,
    MCPRequest,
    MCPResponse,
    MCPResponseChunk,
)


class MCPTransport(ABC):
    """The wire seam. Moves opaque JSON-RPC frames; interprets no tool
    semantics. Deterministic for a given (descriptor, request) in the offline
    profile."""

    kind: MCPTransportKind

    @abstractmethod
    def open(self) -> None:
        """Establish the underlying transport connection. Idempotent."""

    @abstractmethod
    def close(self) -> None:
        """Tear down the transport connection. Idempotent."""

    @property
    @abstractmethod
    def is_open(self) -> bool: ...

    @abstractmethod
    def handshake(self, request: MCPHandshakeRequest) -> MCPHandshakeResult:
        """Perform the MCP `initialize` exchange."""

    @abstractmethod
    def request(self, request: MCPRequest) -> MCPResponse:
        """Send a JSON-RPC request and return its response (M6.8B-2)."""

    @abstractmethod
    def notify(self, notification: MCPNotification) -> None:
        """Send a JSON-RPC notification (no response; M6.8B-2)."""

    @abstractmethod
    def stream(self, request: MCPRequest) -> Iterator[MCPResponseChunk]:
        """Send a request and yield partial-result / progress chunks (M6.8B-2)."""

    def apply_credential(self, credential) -> None:  # noqa: ANN001 — MCPCredential (avoids a cycle)
        """Attach resolved auth material (bearer/OAuth2 headers, mTLS cert/key) to
        this transport before the handshake (M6.8.1, F1). Default: no-op — the
        loopback transport needs no auth. The credential is supplied by the
        runtime, resolved through the injected `CredentialProvider`; a transport
        never looks a secret up itself."""
        return None


class EchoMCPServer:
    """A deterministic, in-process MCP server used only for tests and
    certification (mirrors `EchoToolAdapter`). Answers every MCP method
    deterministically; supports fault injection (`fail_tools`/`timeout_tools`)
    for typed-error tests. Never touches the network."""

    def __init__(
        self,
        *,
        name: str = "echo-mcp",
        protocol_version: str = SUPPORTED_PROTOCOL_VERSIONS[0],
        capabilities: tuple[MCPCapability, ...] = (
            MCPCapability(kind="tools"),
            MCPCapability(kind="resources"),
            MCPCapability(kind="prompts"),
            MCPCapability(kind="sampling"),
        ),
        tools: tuple[str, ...] = ("echo",),
        fail_tools: frozenset[str] = frozenset(),
        timeout_tools: frozenset[str] = frozenset(),
    ) -> None:
        self.name = name
        self.protocol_version = protocol_version
        self._capabilities = capabilities
        self._tools = tools
        self._fail_tools = fail_tools
        self._timeout_tools = timeout_tools

    def initialize(self, request: MCPHandshakeRequest) -> MCPHandshakeResult:
        return MCPHandshakeResult(
            server_protocol_version=self.protocol_version,
            capabilities=self._capabilities,
            server_info={"name": self.name, "client": dict(request.client_info).get("name", "")},
        )

    def handle(self, request: MCPRequest) -> MCPResponse:
        """Deterministic dispatch by method. Raises `MCPTimeoutError` for a
        fault-injected timeout; returns a JSON-RPC error result for a
        fault-injected failure or an unknown method."""
        method, params = request.method, dict(request.params)
        if method == MCPMethod.PING:
            return MCPResponse(request_id=request.request_id, result={})
        if method == MCPMethod.TOOLS_LIST:
            return MCPResponse(request_id=request.request_id, result={
                "tools": [
                    {"name": t, "description": f"echo tool {t}",
                     "inputSchema": {"type": "object"}}
                    for t in self._tools
                ]
            })
        if method == MCPMethod.TOOLS_CALL:
            name = str(params.get("name", ""))
            if name in self._timeout_tools:
                raise MCPTimeoutError(f"echo server: tool {name!r} timed out")
            if name in self._fail_tools:
                return MCPResponse(request_id=request.request_id, error=MCPError_(
                    code=-32000, message=f"tool {name!r} failed"))
            if name not in self._tools:
                return MCPResponse(request_id=request.request_id, error=MCPError_(
                    code=-32602, message=f"unknown tool {name!r}"))
            return MCPResponse(request_id=request.request_id, result={
                "content": [{"type": "text", "text": _json(params.get("arguments", {}))}],
                "isError": False,
            })
        if method == MCPMethod.RESOURCES_LIST:
            return MCPResponse(request_id=request.request_id, result={
                "resources": [{"uri": "echo://resource/1", "name": "r1"}]})
        if method == MCPMethod.RESOURCES_READ:
            return MCPResponse(request_id=request.request_id, result={
                "contents": [{"uri": params.get("uri", ""), "text": "echo-resource"}]})
        if method == MCPMethod.PROMPTS_LIST:
            return MCPResponse(request_id=request.request_id, result={
                "prompts": [{"name": "greet"}]})
        if method == MCPMethod.PROMPTS_GET:
            return MCPResponse(request_id=request.request_id, result={
                "messages": [{"role": "user",
                              "content": {"type": "text", "text": "hello"}}]})
        if method == MCPMethod.SAMPLING_CREATE:
            return MCPResponse(request_id=request.request_id, result={
                "role": "assistant", "model": "echo",
                "content": {"type": "text", "text": "sampled"}})
        return MCPResponse(request_id=request.request_id, error=MCPError_(
            code=-32601, message=f"method not found: {method}"))

    def stream(self, request: MCPRequest) -> Iterator[MCPResponseChunk]:
        """Deterministic 2-chunk stream for a tools/call, then done."""
        final = self.handle(request)
        text = ""
        if final.result is not None:
            content = final.result.get("content", [])
            text = content[0].get("text", "") if content else ""
        mid = len(text) // 2
        yield MCPResponseChunk(request_id=request.request_id, index=0, delta={"text": text[:mid]})
        yield MCPResponseChunk(
            request_id=request.request_id, index=1, delta={"text": text[mid:]}, done=True,
        )


def _json(obj: object) -> str:
    import json

    return json.dumps(obj, sort_keys=True, separators=(",", ":"))


class LoopbackTransport(MCPTransport):
    """An offline transport backed by an `EchoMCPServer`. Deterministic, no
    external dependency — the default and the certification harness."""

    kind = MCPTransportKind.LOOPBACK

    def __init__(self, server: EchoMCPServer | None = None) -> None:
        self._server = server or EchoMCPServer()
        self._open = False

    def open(self) -> None:
        self._open = True

    def close(self) -> None:
        self._open = False

    @property
    def is_open(self) -> bool:
        return self._open

    def _require_open(self) -> None:
        if not self._open:
            raise ConfigError("loopback transport is not open; call open() first")

    def handshake(self, request: MCPHandshakeRequest) -> MCPHandshakeResult:
        self._require_open()
        return self._server.initialize(request)

    def request(self, request: MCPRequest) -> MCPResponse:
        self._require_open()
        return self._server.handle(request)

    def notify(self, notification: MCPNotification) -> None:
        self._require_open()  # deterministic no-op sink (echo server ignores notifications)

    def stream(self, request: MCPRequest) -> Iterator[MCPResponseChunk]:
        self._require_open()
        yield from self._server.stream(request)


def transport_for(
    descriptor: MCPTransportDescriptor,
    *,
    offline_mode: bool = True,
    connect_timeout_s: float = 10.0,
    request_timeout_s: float = 30.0,
    trust=None,  # noqa: ANN001 — MCPTrustPolicy | None (lazy; avoids an import cycle)
) -> MCPTransport:
    """Construct the transport for a descriptor. `loopback` is always available;
    real transports (stdio/http) are configuration-selected and constructed in
    `mcp.transports` (M6.8B-2). `offline_mode` forbids a non-loopback kind. A
    non-`None` `trust` (M6.8.1, F5) is enforced fail-closed before construction."""
    if descriptor.kind is MCPTransportKind.LOOPBACK:
        return LoopbackTransport()
    if offline_mode:
        raise ConfigError(
            f"offline_mode forbids transport kind {descriptor.kind.value!r}; use loopback"
        )
    from .transports import real_transport_for  # lazy import avoids an import cycle

    return real_transport_for(
        descriptor, connect_timeout_s=connect_timeout_s,
        request_timeout_s=request_timeout_s, trust=trust,
    )
