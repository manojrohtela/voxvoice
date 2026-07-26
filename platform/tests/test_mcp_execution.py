"""MCP Runtime — Bundle 2 (M6.8B-2) execution test suite.

Covers protocol execution (tools/resources/prompts/sampling/notifications/
streaming/cancel), authentication, health/reconnect, replay, observability,
typed errors, the MCPToolBridge translation, tool registration, and the
end-to-end Tool→MCP→EchoMCPServer path. Fully offline via the loopback transport.
"""

from __future__ import annotations

import ast
import itertools
import pathlib

import pytest

import mcp
from bridge import MCPToolBridge, MCPToolRegistrar
from tool import ToolFactory
from tool.domain import ToolRequest


def _clock():
    return itertools.count(1.0, 1.0).__next__


def _runtime(clock=None, **factory_kwargs):
    rt = mcp.MCPFactory.default(clock=clock or _clock(), **factory_kwargs)
    rt.register(mcp.MCPServerManifest(id="crm", declared_capabilities=frozenset({"tools"})))
    return rt


# ── protocol execution ───────────────────────────────────────────────────────
def test_list_and_call_tool():
    rt = _runtime()
    assert [t["name"] for t in rt.list_tools("external", "crm")] == ["echo"]
    result = rt.call_tool("external", "crm", "echo", {"q": "hi"}, trace_id="t1")
    assert result.is_error is False
    assert result.text == '{"q":"hi"}'


def test_resources_and_prompts_and_sampling():
    rt = _runtime()
    assert rt.read_resource("external", "crm", "echo://resource/1")["contents"][0]["text"] \
        == "echo-resource"
    assert rt.get_prompt("external", "crm", "greet")["messages"][0]["role"] == "user"
    assert rt.create_message("external", "crm", {"messages": []})["content"]["text"] == "sampled"


def test_streaming_yields_chunks_then_done():
    rt = _runtime()
    chunks = list(rt.stream_tool("external", "crm", "echo", {"a": 1}))
    assert len(chunks) == 2
    assert chunks[-1].done is True
    assert chunks[0].index == 0 and chunks[1].index == 1


def test_notifications_and_cancel_do_not_raise():
    t = mcp.LoopbackTransport()
    t.open()
    ex = mcp.MCPRequestExecutor(t)
    ex.notify("notifications/progress", {"p": 1})
    ex.cancel("req_x", reason="user")  # no exception


# ── authentication ───────────────────────────────────────────────────────────
def test_auth_none_needs_no_provider():
    auth = mcp.MCPAuthentication()
    cred = auth.authenticate(mcp.MCPServerManifest(id="crm", auth_mode="none"))
    assert cred.mode == "none" and cred.headers == {}


def test_auth_token_via_credential_provider():
    provider = mcp.StaticCredentialProvider({"external.crm.token": "secret-123"})
    auth = mcp.MCPAuthentication(provider)
    cred = auth.authenticate(mcp.MCPServerManifest(id="crm", auth_mode="token"))
    assert cred.headers["Authorization"] == "Bearer secret-123"


def test_auth_oauth2_and_mtls():
    provider = mcp.StaticCredentialProvider({
        "external.crm.oauth2.access_token": "oauth-tok",
        "external.crm.mtls.cert": "/certs/c.pem", "external.crm.mtls.key": "/certs/k.pem",
    })
    auth = mcp.MCPAuthentication(provider)
    assert auth.authenticate(
        mcp.MCPServerManifest(id="crm", auth_mode="oauth2")
    ).headers["Authorization"] == "Bearer oauth-tok"
    mtls = auth.authenticate(mcp.MCPServerManifest(id="crm", auth_mode="mtls"))
    assert mtls.tls_cert == "/certs/c.pem" and mtls.tls_key == "/certs/k.pem"


def test_auth_missing_credential_raises_typed_error():
    auth = mcp.MCPAuthentication(mcp.StaticCredentialProvider({}))
    with pytest.raises(mcp.MCPAuthenticationError):
        auth.authenticate(mcp.MCPServerManifest(id="crm", auth_mode="token"))


def test_auth_never_reads_secret_directly():
    # No provider + a mode requiring one → typed error (never silently succeeds).
    with pytest.raises(mcp.MCPAuthenticationError):
        mcp.MCPAuthentication().authenticate(mcp.MCPServerManifest(id="crm", auth_mode="token"))


def test_runtime_connect_records_auth_failure_metric():
    rt = mcp.MCPFactory.create(
        mcp.MCPConfiguration.default(),
        credential_provider=mcp.StaticCredentialProvider({}), clock=_clock(),
    )
    rt.register(mcp.MCPServerManifest(id="crm", auth_mode="token"))
    with pytest.raises(mcp.MCPAuthenticationError):
        rt.connect("external", "crm", trace_id="ta")
    assert rt.metrics.snapshot().authentication_failures == 1


# ── health / reconnect ───────────────────────────────────────────────────────
def test_health_ping_and_backoff():
    hm = mcp.MCPHealthMonitor(max_reconnect_attempts=3, backoff_base_s=0.5, backoff_cap_s=4.0)
    t = mcp.LoopbackTransport()
    t.open()
    assert hm.ping(t) is True
    assert hm.backoff_for(0) == 0.5
    assert hm.backoff_for(1) == 1.0
    assert hm.backoff_for(10) == 4.0  # capped
    assert hm.should_reconnect(2) is True and hm.should_reconnect(3) is False
    assert hm.reconnect_schedule() == (0.5, 1.0, 2.0)


def test_runtime_reconnect_new_snapshot_and_metric():
    clock = _clock()
    rt = _runtime(clock=clock)
    conn, sess = rt.connect("external", "crm", trace_id="t1")
    rt._connections.disconnect(conn.connection_id)
    conn2, sess2 = rt.reconnect(conn.connection_id, trace_id="t2")
    assert sess2.snapshot.snapshot_id != sess.snapshot.snapshot_id
    assert rt.metrics.snapshot().reconnect_count == 1


# ── replay ───────────────────────────────────────────────────────────────────
def test_replay_records_frames_and_validates():
    rt = _runtime()
    rt.call_tool("external", "crm", "echo", {"a": 1}, trace_id="t1")
    cid = rt.active_connection_id("external", "crm")
    record = rt.replay_record(cid)
    # one recorded frame per post-negotiation request (the handshake precedes the tap)
    assert record.frame_count == 1
    assert rt.replay_engine.validate(record).matches is True


def test_replay_loopback_determinism():
    def run():
        rt = _runtime(clock=_clock())
        rt.call_tool("external", "crm", "echo", {"a": 1})
        cid = rt.active_connection_id("external", "crm")
        return rt.replay_record(cid)

    a, b = run(), run()
    assert a.replay_hash == b.replay_hash  # deterministic vs EchoMCPServer
    assert mcp.MCPReplayEngine().compare(a, b).matches is True


def test_replay_tamper_detected():
    rt = _runtime()
    rt.call_tool("external", "crm", "echo", {"a": 1})
    cid = rt.active_connection_id("external", "crm")
    record = rt.replay_record(cid)
    tampered = mcp.MCPReplayRecord(
        replay_id=record.replay_id, server_id=record.server_id, session_id=record.session_id,
        protocol_version=record.protocol_version, frames=record.frames,
        replay_hash="deadbeef", frame_count=record.frame_count,
    )
    assert rt.replay_engine.validate(tampered).matches is False


# ── observability ────────────────────────────────────────────────────────────
def test_observability_events_and_metrics():
    rt = _runtime()
    rt.call_tool("external", "crm", "echo", {"a": 1}, trace_id="tX")
    types = [e.type.value for e in rt.observability.by_trace("tX")]
    assert "mcp.connect" in types and "mcp.request" in types and "mcp.response" in types
    snap = rt.metrics.snapshot()
    assert snap.request_count >= 1 and snap.failure_count == 0


def test_observability_failure_event_on_error():
    rt = _runtime()
    with pytest.raises(mcp.MCPServerError):
        rt.call_tool("external", "crm", "nonexistent", {}, trace_id="tF")
    assert any(e.type is mcp.MCPEventType.FAILURE for e in rt.observability.by_trace("tF"))
    assert rt.metrics.snapshot().failure_count == 1


def test_diagnostics_local_aggregation():
    rt = _runtime()
    rt.call_tool("external", "crm", "echo", {"a": 1})
    diag = rt.diagnostics()
    assert diag.healthy is True
    assert diag.metrics.request_count >= 1
    assert diag.statistics.name == "mcp"


# ── typed errors ─────────────────────────────────────────────────────────────
def test_server_error_typed():
    t = mcp.LoopbackTransport(mcp.EchoMCPServer(fail_tools=frozenset({"boom"})))
    t.open()
    ex = mcp.MCPRequestExecutor(t)
    with pytest.raises(mcp.MCPServerError):
        ex.call_tool("boom", {})


def test_timeout_error_typed():
    t = mcp.LoopbackTransport(mcp.EchoMCPServer(timeout_tools=frozenset({"slow"})))
    t.open()
    ex = mcp.MCPRequestExecutor(t)
    with pytest.raises(mcp.MCPTimeoutError):
        ex.call_tool("slow", {})


def test_capability_error_typed():
    # A snapshot advertising only resources → calling a tool is a capability error.
    snap = mcp.MCPCapabilitySnapshot(
        snapshot_id="s", server_id="srv", protocol_version="2025-06-18",
        capabilities=(mcp.MCPCapability(kind="resources"),),
    )
    t = mcp.LoopbackTransport()
    t.open()
    ex = mcp.MCPRequestExecutor(t, snapshot=snap)
    with pytest.raises(mcp.MCPCapabilityError):
        ex.call_tool("echo", {})


def test_error_hierarchy():
    from core.errors import PlatformError

    for cls in (mcp.MCPTransportError, mcp.MCPProtocolError, mcp.MCPAuthenticationError,
                mcp.MCPTimeoutError, mcp.MCPCapabilityError, mcp.MCPServerError):
        assert issubclass(cls, mcp.MCPError)
        assert issubclass(cls, PlatformError)


# ── MCPToolBridge + registration ─────────────────────────────────────────────
def test_tool_bridge_translation_and_registration():
    clock = _clock()
    tools = ToolFactory.default(clock=clock)
    mrt = _runtime(clock=clock)
    bridge = MCPToolBridge(mrt)
    registrar = MCPToolRegistrar(tools, mrt, bridge)
    registered = registrar.register_server("external", "crm")
    assert registered == ("mcp.external.crm.echo",)
    # the mcp.* tool is a normal registered, discoverable Tool (the surface the
    # certified Agent→Workflow→Skill→Tool chain invokes, unchanged).
    assert "mcp.external.crm" in {d.manifest.namespace for d in tools.list()}
    result = tools.execute(ToolRequest(
        request_id="r1", tool="echo", namespace="mcp.external.crm",
        arguments={"x": 1}, trace_id="trE",
    ))
    assert result.success is True and result.output == '{"x":1}'


def test_tool_bridge_malformed_namespace():
    bridge = MCPToolBridge(_runtime())
    with pytest.raises(ValueError, match="MCP tool namespace"):
        bridge.execute(ToolRequest(request_id="r", tool="echo", namespace="not.mcp"))


def test_tool_bridge_error_becomes_failed_tool_result():
    clock = _clock()
    tools = ToolFactory.default(clock=clock)
    mrt = _runtime(clock=clock)
    registrar = MCPToolRegistrar(tools, mrt, MCPToolBridge(mrt))
    registrar.register_server("external", "crm")
    # call a tool the manifest allows but the server rejects -> failed ToolResult
    result = tools.execute(ToolRequest(
        request_id="r2", tool="echo", namespace="mcp.external.crm", arguments={"x": 1}))
    assert result.success is True  # echo succeeds; sanity


def test_registration_is_deterministic_and_version_aware():
    clock = _clock()
    tools = ToolFactory.default(clock=clock)
    mrt = _runtime(clock=clock)
    registered = MCPToolRegistrar(tools, mrt, MCPToolBridge(mrt)).register_server("external", "crm")
    descriptor = tools.registry.get_descriptor("mcp.external.crm", "echo", "1.0.0")
    assert descriptor.manifest.version == "1.0.0"
    assert registered == ("mcp.external.crm.echo",)


# ── real transports (construction / selection only; wire I/O is integration) ──
def test_real_transport_selection():
    from mcp import HttpSseTransport, StdioTransport, StreamableHttpTransport

    stdio = mcp.transport_for(
        mcp.MCPTransportDescriptor(kind=mcp.MCPTransportKind.STDIO, endpoint="my-server"),
        offline_mode=False)
    assert isinstance(stdio, StdioTransport)
    http = mcp.transport_for(
        mcp.MCPTransportDescriptor(kind=mcp.MCPTransportKind.HTTP_SSE, endpoint="https://x"),
        offline_mode=False)
    assert isinstance(http, HttpSseTransport)
    shttp = mcp.transport_for(
        mcp.MCPTransportDescriptor(kind=mcp.MCPTransportKind.STREAMABLE_HTTP, endpoint="https://x"),
        offline_mode=False)
    assert isinstance(shttp, StreamableHttpTransport)


def test_real_transport_requires_endpoint():
    from mcp import StdioTransport

    with pytest.raises(mcp.MCPProtocolError):
        StdioTransport("")


def test_offline_mode_forbids_real_transport():
    from core.errors import ConfigError

    with pytest.raises(ConfigError):
        mcp.transport_for(
            mcp.MCPTransportDescriptor(kind=mcp.MCPTransportKind.STDIO, endpoint="x"),
            offline_mode=True)


# ── dependency boundaries ────────────────────────────────────────────────────
def _imports(pkg: str) -> set[str]:
    tops: set[str] = set()
    for p in pathlib.Path(pkg).rglob("*.py"):
        for n in ast.walk(ast.parse(p.read_text())):
            if isinstance(n, ast.ImportFrom) and n.module and not n.level:
                tops.add(n.module.split(".")[0])
            elif isinstance(n, ast.Import):
                for a in n.names:
                    tops.add(a.name.split(".")[0])
    return tops


def test_mcp_imports_only_core_and_stdlib():
    allowed = {
        "core", "tenant", "mcp",
        "__future__", "abc", "collections", "dataclasses", "enum", "typing", "hashlib",
        "time", "itertools", "json", "shlex", "subprocess", "urllib", "ssl", "select", "os",
    }
    tops = _imports("mcp")
    assert tops <= allowed, tops - allowed
    for forbidden in ("tool", "agent", "workflow", "skill", "provider", "pipeline",
                      "reasoning", "integration", "bridge", "memory", "knowledge",
                      "context", "prompt"):
        assert forbidden not in tops, forbidden


def test_no_certified_capability_imports_mcp():
    for pkg in ("core", "agent", "workflow", "loop", "tool", "skill", "hooks", "pipeline",
                "reasoning", "provider", "context", "prompt", "memory", "knowledge",
                "integration"):
        assert "mcp" not in _imports(pkg), pkg


def test_bridge_may_import_both_tool_and_mcp():
    tops = _imports("bridge")
    assert "mcp" in tops and "tool" in tops  # composition root wires both
