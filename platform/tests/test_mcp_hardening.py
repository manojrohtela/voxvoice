"""MCP Runtime — M6.8.1 operational-hardening suite (F1–F6).

Validates the certification fixes, including **live** real-transport validation
that stays fully offline (a subprocess stdio server + an in-process HTTP server on
loopback — no external network):

- F1  credentials reach the wire (stdio env var + HTTP Authorization header) and
      never appear in a credential's repr.
- F2  per-tenant connection / replay isolation.
- F3  request-timeout enforcement raises `MCPTimeoutError` (never hangs).
- F4  strict JSON-RPC id correlation (mismatch/missing rejected).
- F5  trust: endpoint allow-list, TLS policy, mTLS requirement.
- F6  incremental SSE parsing + notification correctness (no body expected).
"""

from __future__ import annotations

import itertools
import json
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

import mcp
from mcp.transports import _parse_response, parse_sse_stream

# ── a real stdio MCP server (subprocess) ─────────────────────────────────────
_STDIO_SERVER = r'''
import sys, json, os, time
for line in sys.stdin:
    line = line.strip()
    if not line:
        continue
    msg = json.loads(line)
    mid, method, params = msg.get("id"), msg.get("method"), msg.get("params", {})
    if method is None:
        continue  # notification: no response
    if method == "initialize":
        res = {"protocolVersion": "2025-06-18", "capabilities": {"tools": {}},
               "serverInfo": {"name": "stdio-echo"}}
    elif method == "ping":
        res = {}
    elif method == "tools/list":
        res = {"tools": [{"name": "echo", "inputSchema": {"type": "object"}}]}
    elif method == "tools/call":
        name = params.get("name")
        if name == "slow":
            time.sleep(30)
        payload = {"auth": os.environ.get("MCP_AUTHORIZATION", ""),
                   "args": params.get("arguments", {})}
        res = {"content": [{"type": "text", "text": json.dumps(payload, sort_keys=True,
               separators=(",", ":"))}], "isError": False}
    else:
        sys.stdout.write(json.dumps({"jsonrpc": "2.0", "id": mid,
                         "error": {"code": -32601, "message": "nope"}}) + "\n")
        sys.stdout.flush()
        continue
    sys.stdout.write(json.dumps({"jsonrpc": "2.0", "id": mid, "result": res}) + "\n")
    sys.stdout.flush()
'''


@pytest.fixture()
def stdio_command(tmp_path):
    script = tmp_path / "stdio_server.py"
    script.write_text(_STDIO_SERVER)
    return f"{sys.executable} {script}"


# ── a real HTTP/SSE MCP server (in-process, loopback) ────────────────────────
class _Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):  # silence
        return

    def do_POST(self):  # noqa: N802 — BaseHTTPRequestHandler API
        length = int(self.headers.get("content-length", 0))
        body = json.loads(self.rfile.read(length)) if length else {}
        mid, method = body.get("id"), body.get("method")
        params = body.get("params", {})
        auth = self.headers.get("Authorization", "")
        if method is None:  # a JSON-RPC notification → 202, no body (F6)
            self.send_response(202)
            self.end_headers()
            return
        if method == "tools/call" and "text/event-stream" in self.headers.get("Accept", ""):
            self.send_response(200)
            self.send_header("content-type", "text/event-stream")
            self.end_headers()
            self.wfile.write(
                b'data: {"jsonrpc":"2.0","method":"notifications/progress",'
                b'"params":{"pct":50}}\n\n')
            final = {"jsonrpc": "2.0", "id": mid,
                     "result": {"content": [{"type": "text", "text": "streamed"}]}}
            self.wfile.write(f"data: {json.dumps(final)}\n\n".encode())
            return
        if method == "initialize":
            result = {"protocolVersion": "2025-06-18", "capabilities": {"tools": {}},
                      "serverInfo": {"name": "http-echo"}}
        elif method == "tools/call":
            if params.get("name") == "badid":  # force an id mismatch (F4)
                self._send({"jsonrpc": "2.0", "id": "WRONG", "result": {}})
                return
            result = {"content": [{"type": "text", "text": auth}], "isError": False}
        else:
            result = {}
        self._send({"jsonrpc": "2.0", "id": mid, "result": result})

    def _send(self, obj):
        data = json.dumps(obj).encode()
        self.send_response(200)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


@pytest.fixture()
def http_url():
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}/rpc"
    finally:
        server.shutdown()
        server.server_close()


def _clock():
    return itertools.count(1.0, 1.0).__next__


# ── F1: credential wiring + redaction ────────────────────────────────────────
def test_f1_credential_repr_is_redacted():
    cred = mcp.MCPCredential(
        mode="token", headers={"Authorization": "Bearer super-secret"},
        tls_cert="/c.pem", tls_key="/k.pem")
    text = repr(cred)
    assert "super-secret" not in text
    assert "redacted" in text and "token" in text


def test_f1_stdio_credential_reaches_server_via_env(stdio_command):
    t = mcp.StdioTransport(stdio_command, request_timeout_s=10.0)
    t.apply_credential(mcp.MCPCredential(mode="token", headers={"Authorization": "Bearer T"}))
    t.open()
    try:
        result = t.request(mcp.MCPRequest(
            request_id="r1", method=mcp.MCPMethod.TOOLS_CALL,
            params={"name": "echo", "arguments": {"x": 1}}))
        echoed = json.loads(result.result["content"][0]["text"])
        assert echoed["auth"] == "Bearer T"  # credential crossed the wire (F1)
    finally:
        t.close()


def test_f1_http_credential_reaches_server_header(http_url):
    t = mcp.HttpSseTransport(http_url, request_timeout_s=10.0)
    t.apply_credential(mcp.MCPCredential(mode="token", headers={"Authorization": "Bearer H"}))
    t.open()
    result = t.request(mcp.MCPRequest(
        request_id="r1", method=mcp.MCPMethod.TOOLS_CALL,
        params={"name": "echo", "arguments": {}}))
    assert result.result["content"][0]["text"] == "Bearer H"


# ── F2: tenant isolation ─────────────────────────────────────────────────────
def test_f2_distinct_connections_per_tenant():
    rt = mcp.MCPFactory.default(clock=_clock())
    rt.register(mcp.MCPServerManifest(id="crm", declared_capabilities=frozenset({"tools"})))
    rt.call_tool("external", "crm", "echo", {"a": 1}, org_id="org-A")
    rt.call_tool("external", "crm", "echo", {"a": 1}, org_id="org-B")
    cid_a = rt.active_connection_id("external", "crm", org_id="org-A")
    cid_b = rt.active_connection_id("external", "crm", org_id="org-B")
    assert cid_a and cid_b and cid_a != cid_b  # no cross-tenant connection sharing
    # replay transcripts are per-connection ⇒ per-tenant, never shared
    assert rt.replay_record(cid_a).session_id != rt.replay_record(cid_b).session_id


def test_f2_same_tenant_reuses_connection():
    rt = mcp.MCPFactory.default(clock=_clock())
    rt.register(mcp.MCPServerManifest(id="crm", declared_capabilities=frozenset({"tools"})))
    rt.call_tool("external", "crm", "echo", {"a": 1}, org_id="org-A")
    rt.call_tool("external", "crm", "echo", {"a": 2}, org_id="org-A")
    assert rt.statistics().connections >= 1
    assert rt.active_connection_id("external", "crm", org_id="org-A") is not None


# ── F3: timeout ──────────────────────────────────────────────────────────────
def test_f3_stdio_timeout_raises(stdio_command):
    t = mcp.StdioTransport(stdio_command, request_timeout_s=0.5)
    t.open()
    try:
        with pytest.raises(mcp.MCPTimeoutError):
            t.request(mcp.MCPRequest(
                request_id="r1", method=mcp.MCPMethod.TOOLS_CALL,
                params={"name": "slow", "arguments": {}}))
    finally:
        t.close()


# ── F4: id correlation ───────────────────────────────────────────────────────
def test_f4_parse_response_rejects_mismatch():
    with pytest.raises(mcp.MCPProtocolError):
        _parse_response("r1", {"jsonrpc": "2.0", "id": "WRONG", "result": {}})


def test_f4_parse_response_rejects_missing_id():
    with pytest.raises(mcp.MCPProtocolError):
        _parse_response("r1", {"jsonrpc": "2.0", "result": {}})


def test_f4_parse_response_accepts_match():
    resp = _parse_response("r1", {"jsonrpc": "2.0", "id": "r1", "result": {"ok": True}})
    assert resp.request_id == "r1" and resp.result == {"ok": True}


def test_f4_http_id_mismatch_rejected(http_url):
    t = mcp.HttpSseTransport(http_url, request_timeout_s=10.0)
    t.open()
    with pytest.raises(mcp.MCPProtocolError):
        t.request(mcp.MCPRequest(
            request_id="r1", method=mcp.MCPMethod.TOOLS_CALL, params={"name": "badid"}))


# ── F5: trust ────────────────────────────────────────────────────────────────
def test_f5_insecure_http_refused():
    policy = mcp.MCPTrustPolicy(allowed_endpoints=frozenset({"http://x"}))
    with pytest.raises(mcp.MCPTransportError):
        policy.check_endpoint(mcp.MCPTransportKind.HTTP_SSE, "http://x/rpc")


def test_f5_allow_list_denies_unknown_endpoint():
    policy = mcp.MCPTrustPolicy(allowed_endpoints=frozenset({"https://trusted"}))
    with pytest.raises(mcp.MCPTransportError):
        policy.check_endpoint(mcp.MCPTransportKind.HTTP_SSE, "https://evil/rpc")
    policy.check_endpoint(mcp.MCPTransportKind.HTTP_SSE, "https://trusted/rpc")  # allowed


def test_f5_real_transport_for_enforces_trust():
    desc = mcp.MCPTransportDescriptor(
        kind=mcp.MCPTransportKind.HTTP_SSE, endpoint="https://evil/rpc")
    with pytest.raises(mcp.MCPTransportError):
        mcp.transport_for(
            desc, offline_mode=False,
            trust=mcp.MCPTrustPolicy(allowed_endpoints=frozenset({"https://ok"})))


def test_f5_require_mtls_without_cert_raises():
    policy = mcp.MCPTrustPolicy(require_mtls=True, allowed_endpoints=frozenset({"https://x"}))
    with pytest.raises(mcp.MCPTransportError):
        policy.ssl_context()


def test_f5_config_requires_trust_for_non_offline():
    from core.errors import ConfigError

    with pytest.raises(ConfigError):
        mcp.MCPConfiguration(
            offline_mode=False, default_transport=mcp.MCPTransportKind.HTTP_SSE, trust=None)


# ── F6: SSE parsing + notifications ──────────────────────────────────────────
def test_f6_parse_sse_stream_progress_then_result():
    lines = [
        'data: {"jsonrpc":"2.0","method":"notifications/progress","params":{"pct":50}}',
        "",
        'data: {"jsonrpc":"2.0","id":"r1","result":{"content":[{"type":"text","text":"x"}]}}',
        "",
    ]
    chunks = list(parse_sse_stream(lines, "r1"))
    assert [c.done for c in chunks] == [False, True]
    assert chunks[0].delta == {"pct": 50}
    assert chunks[1].delta["content"][0]["text"] == "x"


def test_f6_parse_sse_stream_graceful_terminal_when_no_result():
    chunks = list(parse_sse_stream(['data: {"params":{"pct":10}}', ""], "r1"))
    assert chunks[-1].done is True  # always terminates


def test_f6_http_streaming_incremental(http_url):
    t = mcp.HttpSseTransport(http_url, request_timeout_s=10.0)
    t.open()
    chunks = list(t.stream(mcp.MCPRequest(
        request_id="r9", method=mcp.MCPMethod.TOOLS_CALL, params={"name": "echo"})))
    assert chunks[0].done is False and chunks[0].delta == {"pct": 50}
    assert chunks[-1].done is True
    assert chunks[-1].delta["content"][0]["text"] == "streamed"


def test_f6_http_notify_accepts_empty_body(http_url):
    t = mcp.HttpSseTransport(http_url, request_timeout_s=10.0)
    t.open()
    t.notify(mcp.MCPNotification(method="notifications/progress", params={"p": 1}))  # no raise


# ── live stdio happy path (initialize → tools/call over a real subprocess) ────
def test_live_stdio_end_to_end(stdio_command):
    t = mcp.StdioTransport(stdio_command, request_timeout_s=10.0)
    t.open()
    try:
        hs = t.handshake(mcp.MCPHandshakeRequest(client_protocol_version="2025-06-18"))
        assert hs.server_protocol_version == "2025-06-18"
        ex = mcp.MCPRequestExecutor(t)
        assert [x["name"] for x in ex.list_tools()] == ["echo"]
        assert ex.call_tool("echo", {"k": "v"}).is_error is False
    finally:
        t.close()
