"""Real MCP transports (M6.8B-2; hardened M6.8.1) — stdio / http-sse / streamable-http.

Concrete `MCPTransport` implementations behind the frozen seam, using **stdlib
only** (`subprocess`, `urllib`, `ssl`, `select`, `json`) so `mcp → core` (+
stdlib) holds — no third-party dependency. They are **configuration-selected**
(`real_transport_for`); `loopback` remains the default and the sole
offline-deterministic path.

M6.8.1 operational hardening:
- **F1** credential application: bearer/OAuth2 `Authorization` headers are attached
  to every HTTP frame; mTLS cert/key are loaded into the TLS context; stdio passes
  headers to the server process as environment variables. A transport never reads
  a secret itself — it receives a resolved `MCPCredential` via `apply_credential`.
- **F3** request-timeout enforcement on every I/O (`MCPTimeoutError`, never a hang).
- **F4** strict JSON-RPC id correlation (`_parse_response`) — mismatched, late,
  duplicate, or malformed responses are rejected, never silently rewritten.
- **F5** trust: endpoint admission + explicit TLS context come from `MCPTrustPolicy`.
- **F6** incremental SSE / streamable-http framing; notifications expect no body.

No protocol branching lives outside these classes — the executor speaks only
`MCPRequest`/`MCPResponse`; each transport owns its own framing. All failures
surface as typed `MCPError`s.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Iterator

from .domain import (
    MCPHandshakeRequest,
    MCPHandshakeResult,
    MCPTransportDescriptor,
    MCPTransportKind,
)
from .errors import MCPProtocolError, MCPTimeoutError, MCPTransportError
from .protocol import (
    MCPError_,
    MCPMethod,
    MCPNotification,
    MCPRequest,
    MCPResponse,
    MCPResponseChunk,
)
from .transport import MCPTransport
from .trust import MCPTrustPolicy


def _rpc_frame(request: MCPRequest) -> dict:
    return {"jsonrpc": "2.0", "id": request.request_id,
            "method": request.method, "params": dict(request.params)}


def _notification_frame(notification: MCPNotification) -> dict:
    return {"jsonrpc": "2.0", "method": notification.method, "params": dict(notification.params)}


def _parse_response(expected_id: str, payload: dict) -> MCPResponse:
    """Parse a JSON-RPC response, enforcing strict id correlation (F4).

    The wire `id` MUST be present and equal to the request's id — a mismatch is a
    late/duplicate/unexpected response and is rejected, never silently
    re-attributed to the pending request."""
    if "id" not in payload or payload["id"] is None:
        raise MCPProtocolError(
            f"JSON-RPC response for {expected_id!r} is missing its id (malformed)"
        )
    if str(payload["id"]) != expected_id:
        raise MCPProtocolError(
            f"JSON-RPC id mismatch: expected {expected_id!r}, got {payload['id']!r} "
            "(late, duplicate, or unexpected response)"
        )
    if "error" in payload and payload["error"] is not None:
        err = payload["error"]
        return MCPResponse(request_id=expected_id, error=MCPError_(
            code=int(err.get("code", -32000)), message=str(err.get("message", "error")),
            data=err.get("data")))
    return MCPResponse(request_id=expected_id, result=payload.get("result") or {})


def _handshake_from(result: MCPResponse) -> MCPHandshakeResult:
    body = result.result or {}
    caps_obj = body.get("capabilities", {})
    from .domain import MCPCapability

    caps = tuple(MCPCapability(kind=k) for k in sorted(caps_obj)) if isinstance(caps_obj, dict) \
        else ()
    return MCPHandshakeResult(
        server_protocol_version=str(body.get("protocolVersion", "")),
        capabilities=caps, server_info=dict(body.get("serverInfo", {})) if isinstance(
            body.get("serverInfo"), dict) else {},
    )


def parse_sse_stream(lines: Iterable[str], request_id: str) -> Iterator[MCPResponseChunk]:
    """Incremental SSE / streamable-http framing (F6) — a pure, deterministic
    parser over an iterable of decoded lines. Each `data:` payload is a JSON-RPC
    message: a `result` closes the stream (done); `params` (a progress
    notification) is an intermediate chunk. A stream that ends without a terminal
    frame still yields a final `done` chunk (graceful termination)."""
    index = 0
    saw_done = False
    for raw in lines:
        line = raw.rstrip("\n").rstrip("\r")
        if not line.startswith("data:"):
            continue  # ignore SSE comments / event: / id: / blank separators
        data = line[len("data:"):].strip()
        if not data or data == "[DONE]":
            continue
        try:
            message = json.loads(data)
        except json.JSONDecodeError as exc:
            raise MCPProtocolError(f"invalid SSE data frame: {exc}") from exc
        if isinstance(message, dict) and message.get("result") is not None:
            yield MCPResponseChunk(
                request_id=request_id, index=index,
                delta=dict(message["result"]), done=True)
            saw_done = True
            index += 1
            break
        delta = message.get("params", message) if isinstance(message, dict) else {"value": message}
        yield MCPResponseChunk(request_id=request_id, index=index, delta=dict(delta), done=False)
        index += 1
    if not saw_done:
        yield MCPResponseChunk(request_id=request_id, index=index, delta={}, done=True)


def _header_env_key(name: str) -> str:
    """`Authorization` -> `MCP_AUTHORIZATION` — how stdio passes auth to the child."""
    return "MCP_" + name.upper().replace("-", "_")


class StdioTransport(MCPTransport):
    """JSON-RPC over a subprocess's stdin/stdout (newline-delimited)."""

    kind = MCPTransportKind.STDIO

    def __init__(
        self, command: str, *, connect_timeout_s: float = 10.0, request_timeout_s: float = 30.0,
    ) -> None:
        if not command:
            raise MCPProtocolError("stdio transport requires a non-empty command endpoint")
        self._command = command
        self._timeout = connect_timeout_s
        self._request_timeout = request_timeout_s
        self._proc = None
        self._env_extra: dict[str, str] = {}

    def apply_credential(self, credential) -> None:  # noqa: ANN001 — MCPCredential
        """stdio has no request headers; pass auth to the child as environment
        variables (F1). mTLS is not applicable to a local pipe."""
        self._env_extra = {_header_env_key(k): v for k, v in dict(credential.headers).items()}

    def open(self) -> None:
        import os
        import shlex
        import subprocess

        if self._proc is not None:
            return
        env = {**os.environ, **self._env_extra} if self._env_extra else None
        try:
            self._proc = subprocess.Popen(  # noqa: S603 — operator-provided MCP server command
                shlex.split(self._command), stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                text=True, bufsize=1, env=env,
            )
        except OSError as exc:
            raise MCPTransportError(f"failed to start stdio server {self._command!r}: {exc}") \
                from exc

    def close(self) -> None:
        if self._proc is not None:
            self._proc.terminate()
            self._proc = None

    @property
    def is_open(self) -> bool:
        return self._proc is not None

    def _readline_within_timeout(self) -> str:
        """Read one line, enforcing `request_timeout_s` (F3) — never blocks
        forever on a silent server."""
        import select

        assert self._proc is not None and self._proc.stdout is not None
        ready, _, _ = select.select([self._proc.stdout], [], [], self._request_timeout)
        if not ready:
            raise MCPTimeoutError(
                f"stdio request exceeded {self._request_timeout}s with no response"
            )
        return self._proc.stdout.readline()

    def _roundtrip(self, frame: dict) -> dict:
        if self._proc is None or self._proc.stdin is None or self._proc.stdout is None:
            raise MCPTransportError("stdio transport is not open")
        try:
            self._proc.stdin.write(json.dumps(frame) + "\n")
            self._proc.stdin.flush()
            line = self._readline_within_timeout()
        except (BrokenPipeError, OSError) as exc:
            raise MCPTransportError(f"stdio transport I/O failure: {exc}") from exc
        if not line:
            raise MCPTransportError("stdio server closed the stream")
        try:
            return json.loads(line)
        except json.JSONDecodeError as exc:
            raise MCPProtocolError(f"invalid JSON-RPC frame from stdio server: {exc}") from exc

    def handshake(self, request: MCPHandshakeRequest) -> MCPHandshakeResult:
        resp = self.request(MCPRequest(
            request_id="init", method=MCPMethod.INITIALIZE,
            params={"protocolVersion": request.client_protocol_version,
                    "clientInfo": dict(request.client_info)}))
        if resp.is_error:
            raise MCPProtocolError(f"initialize failed: {resp.error.message}")  # type: ignore[union-attr]
        return _handshake_from(resp)

    def request(self, request: MCPRequest) -> MCPResponse:
        return _parse_response(request.request_id, self._roundtrip(_rpc_frame(request)))

    def notify(self, notification: MCPNotification) -> None:
        if self._proc is None or self._proc.stdin is None:
            raise MCPTransportError("stdio transport is not open")
        self._proc.stdin.write(json.dumps(_notification_frame(notification)) + "\n")
        self._proc.stdin.flush()

    def stream(self, request: MCPRequest) -> Iterator[MCPResponseChunk]:
        # stdio has no partial framing here; deliver the final result as one done chunk.
        resp = self.request(request)
        yield MCPResponseChunk(
            request_id=request.request_id, index=0, delta=dict(resp.result or {}), done=True)


class _HttpTransport(MCPTransport):
    """Shared JSON-RPC-over-HTTP(S) base for http-sse / streamable-http."""

    def __init__(
        self, url: str, *, connect_timeout_s: float = 10.0, request_timeout_s: float = 30.0,
        trust: MCPTrustPolicy | None = None,
    ) -> None:
        if not url:
            raise MCPProtocolError(f"{self.kind.value} transport requires a URL endpoint")
        self._url = url
        self._timeout = connect_timeout_s
        self._request_timeout = request_timeout_s
        self._trust = trust
        self._open = False
        self._headers: dict[str, str] = {}
        self._tls_cert = ""
        self._tls_key = ""

    def apply_credential(self, credential) -> None:  # noqa: ANN001 — MCPCredential
        """Attach bearer/OAuth2 headers and mTLS cert/key (F1). Values are held
        only for live requests — never logged, replayed, or emitted."""
        self._headers = dict(credential.headers)
        self._tls_cert, self._tls_key = credential.tls_cert, credential.tls_key

    def open(self) -> None:
        self._open = True

    def close(self) -> None:
        self._open = False

    @property
    def is_open(self) -> bool:
        return self._open

    def _ssl_context(self):
        """Explicit TLS context from the trust policy (F5); never library-default
        for an https endpoint under a policy."""
        if not self._url.lower().startswith("https"):
            return None
        policy = self._trust or MCPTrustPolicy(enforce_allow_list=False)
        return policy.ssl_context(tls_cert=self._tls_cert, tls_key=self._tls_key)

    def _open_url(self, request, *, accept: str):  # noqa: ANN001 — urllib.request.Request
        import urllib.error
        import urllib.request

        request.add_header("content-type", "application/json")
        request.add_header("accept", accept)
        for name, value in self._headers.items():
            request.add_header(name, value)
        try:
            return urllib.request.urlopen(  # noqa: S310 — operator-configured MCP endpoint URL
                request, timeout=self._request_timeout, context=self._ssl_context())
        except TimeoutError as exc:
            raise MCPTimeoutError(f"http request to {self._url} timed out") from exc
        except urllib.error.URLError as exc:
            raise MCPTransportError(f"http transport failure to {self._url}: {exc}") from exc

    def _post(self, frame: dict) -> dict:
        import urllib.request

        req = urllib.request.Request(  # noqa: S310
            self._url, data=json.dumps(frame).encode("utf-8"))
        with self._open_url(req, accept="application/json") as resp:
            body = resp.read().decode("utf-8")
        try:
            return json.loads(body)
        except json.JSONDecodeError as exc:
            raise MCPProtocolError(f"invalid JSON-RPC response from {self._url}: {exc}") from exc

    def handshake(self, request: MCPHandshakeRequest) -> MCPHandshakeResult:
        resp = self.request(MCPRequest(
            request_id="init", method=MCPMethod.INITIALIZE,
            params={"protocolVersion": request.client_protocol_version,
                    "clientInfo": dict(request.client_info)}))
        if resp.is_error:
            raise MCPProtocolError(f"initialize failed: {resp.error.message}")  # type: ignore[union-attr]
        return _handshake_from(resp)

    def request(self, request: MCPRequest) -> MCPResponse:
        return _parse_response(request.request_id, self._post(_rpc_frame(request)))

    def notify(self, notification: MCPNotification) -> None:
        """Fire-and-forget POST (F6): a JSON-RPC notification expects no body;
        an empty/202 response is success, not a protocol error."""
        import urllib.request

        req = urllib.request.Request(  # noqa: S310
            self._url, data=json.dumps(_notification_frame(notification)).encode("utf-8"))
        with self._open_url(req, accept="application/json") as resp:
            resp.read()  # drain; body (if any) is intentionally ignored

    def stream(self, request: MCPRequest) -> Iterator[MCPResponseChunk]:
        """Incremental SSE / streamable-http framing (F6) — request an event
        stream and parse `data:` frames as they arrive; close the upstream
        response on generator teardown (graceful termination / cancel)."""
        import urllib.request

        req = urllib.request.Request(  # noqa: S310
            self._url, data=json.dumps(_rpc_frame(request)).encode("utf-8"))
        resp = self._open_url(req, accept="text/event-stream")
        try:
            lines = (line.decode("utf-8") for line in resp)
            yield from parse_sse_stream(lines, request.request_id)
        finally:
            resp.close()


class HttpSseTransport(_HttpTransport):
    kind = MCPTransportKind.HTTP_SSE


class StreamableHttpTransport(_HttpTransport):
    kind = MCPTransportKind.STREAMABLE_HTTP


def real_transport_for(
    descriptor: MCPTransportDescriptor, *, connect_timeout_s: float = 10.0,
    request_timeout_s: float = 30.0, trust: MCPTrustPolicy | None = None,
) -> MCPTransport:
    """Construct a non-loopback transport from its descriptor (config-selected).
    A non-`None` `trust` policy admits the endpoint fail-closed first (F5)."""
    if trust is not None:
        trust.check_endpoint(descriptor.kind, descriptor.endpoint)
    if descriptor.kind is MCPTransportKind.STDIO:
        return StdioTransport(
            descriptor.endpoint, connect_timeout_s=connect_timeout_s,
            request_timeout_s=request_timeout_s)
    if descriptor.kind is MCPTransportKind.HTTP_SSE:
        return HttpSseTransport(
            descriptor.endpoint, connect_timeout_s=connect_timeout_s,
            request_timeout_s=request_timeout_s, trust=trust)
    if descriptor.kind is MCPTransportKind.STREAMABLE_HTTP:
        return StreamableHttpTransport(
            descriptor.endpoint, connect_timeout_s=connect_timeout_s,
            request_timeout_s=request_timeout_s, trust=trust)
    raise MCPProtocolError(f"unknown transport kind {descriptor.kind!r}")
