"""MCP Runtime — Bundle 1 (M6.8B-1) core test suite.

Verifies the server registry, capability registry, connection + session FSMs,
one-time capability negotiation, immutable snapshots (reconnect → new snapshot),
the deterministic loopback transport + EchoMCPServer, deterministic factory,
configuration validation, and the `mcp` dependency boundaries. Fully offline —
no network, no request execution (Bundle 2).
"""

from __future__ import annotations

import ast
import itertools
import pathlib

import pytest

import mcp
from core.errors import ConfigError, PlatformError
from core.fsm import FSMError


def _counter_clock(start: float = 1.0, step: float = 1.0):
    ticks = itertools.count(start, step)
    return lambda: next(ticks)


def _runtime(clock=None):
    return mcp.MCPFactory.default(clock=clock or (lambda: 0.0))


# ── registry ────────────────────────────────────────────────────────────────
def test_register_and_lookup_server():
    rt = _runtime()
    desc = rt.register(mcp.MCPServerManifest(id="crm", namespace="external", version="1.0.0"))
    assert desc.qualified_name == "external.crm"
    assert rt.get_server("external", "crm").identity.server_id == desc.server.identity.server_id
    assert rt.registry.names() == ("external.crm",)


def test_duplicate_registration_rejected():
    rt = _runtime()
    rt.register(mcp.MCPServerManifest(id="crm"))
    with pytest.raises(ConfigError):
        rt.register(mcp.MCPServerManifest(id="crm"))


def test_unknown_server_lookup_raises():
    rt = _runtime()
    with pytest.raises(ConfigError):
        rt.get_server("external", "ghost")


def test_invalid_manifest_rejected():
    rt = _runtime()
    with pytest.raises(ConfigError):
        rt.register(mcp.MCPServerManifest(id="", namespace="external"))
    with pytest.raises(ConfigError):
        rt.register(mcp.MCPServerManifest(id="x", interface_version="9.9"))
    ok, errors = mcp.validate_manifest(mcp.MCPServerManifest(id="x", auth_mode="weird"))
    assert not ok and any("auth_mode" in e for e in errors)


def test_version_resolution_picks_highest():
    rt = _runtime()
    rt.register(mcp.MCPServerManifest(id="crm", version="1.0.0"))
    rt.register(mcp.MCPServerManifest(id="crm", version="2.0.0"))
    assert rt.get_server("external", "crm").version == "2.0.0"  # highest when unspecified
    assert rt.get_server("external", "crm", "1.0.0").version == "1.0.0"


# ── connection FSM ───────────────────────────────────────────────────────────
def test_connection_fsm_valid_path():
    conn = mcp.MCPConnection(
        connection_id="c1", server_id="s1", transport_kind=mcp.MCPTransportKind.LOOPBACK,
        state=mcp.MCPConnectionState.REGISTERED,
    )
    conn = mcp.advance_connection(conn, mcp.MCPConnectionTrigger.CONNECT, clock=lambda: 0.0)
    assert conn.state is mcp.MCPConnectionState.CONNECTING
    conn = mcp.advance_connection(conn, mcp.MCPConnectionTrigger.ESTABLISH, clock=lambda: 0.0)
    assert conn.state is mcp.MCPConnectionState.CONNECTED
    conn = mcp.advance_connection(conn, mcp.MCPConnectionTrigger.DEGRADE, clock=lambda: 0.0)
    assert conn.state is mcp.MCPConnectionState.DEGRADED
    conn = mcp.advance_connection(conn, mcp.MCPConnectionTrigger.RECOVER, clock=lambda: 0.0)
    assert conn.state is mcp.MCPConnectionState.CONNECTED


def test_connection_fsm_rejects_illegal_transition():
    conn = mcp.MCPConnection(
        connection_id="c1", server_id="s1", transport_kind=mcp.MCPTransportKind.LOOPBACK,
        state=mcp.MCPConnectionState.REGISTERED,
    )
    with pytest.raises(FSMError):  # cannot ESTABLISH before CONNECT
        mcp.advance_connection(conn, mcp.MCPConnectionTrigger.ESTABLISH, clock=lambda: 0.0)


def test_connection_closed_is_terminal():
    conn = mcp.MCPConnection(
        connection_id="c1", server_id="s1", transport_kind=mcp.MCPTransportKind.LOOPBACK,
        state=mcp.MCPConnectionState.CONNECTED,
    )
    conn = mcp.advance_connection(conn, mcp.MCPConnectionTrigger.CLOSE, clock=lambda: 0.0)
    assert conn.state is mcp.MCPConnectionState.CLOSED
    with pytest.raises(FSMError):
        mcp.advance_connection(conn, mcp.MCPConnectionTrigger.CONNECT, clock=lambda: 0.0)


# ── session FSM ──────────────────────────────────────────────────────────────
def test_session_fsm_valid_path():
    s = mcp.MCPSession(session_id="x", connection_id="c1", server_id="s1")
    for trig, state in [
        (mcp.MCPSessionTrigger.INITIALIZE, mcp.MCPSessionState.INITIALIZING),
        (mcp.MCPSessionTrigger.NEGOTIATE, mcp.MCPSessionState.NEGOTIATING),
        (mcp.MCPSessionTrigger.READY, mcp.MCPSessionState.READY),
        (mcp.MCPSessionTrigger.CLOSE, mcp.MCPSessionState.CLOSING),
        (mcp.MCPSessionTrigger.FINISH, mcp.MCPSessionState.CLOSED),
    ]:
        s = mcp.advance_session(s, trig, clock=lambda: 0.0)
        assert s.state is state


def test_session_fsm_rejects_illegal_transition():
    s = mcp.MCPSession(session_id="x", connection_id="c1", server_id="s1")
    with pytest.raises(FSMError):  # cannot go READY before negotiating
        mcp.advance_session(s, mcp.MCPSessionTrigger.READY, clock=lambda: 0.0)


# ── transport + EchoMCPServer ────────────────────────────────────────────────
def test_loopback_transport_deterministic_handshake():
    t = mcp.LoopbackTransport()
    t.open()
    r1 = t.handshake(mcp.MCPHandshakeRequest(client_protocol_version="2025-06-18"))
    r2 = t.handshake(mcp.MCPHandshakeRequest(client_protocol_version="2025-06-18"))
    assert r1 == r2  # deterministic
    assert r1.server_protocol_version == "2025-06-18"
    assert {c.kind for c in r1.capabilities} == {"tools", "resources", "prompts", "sampling"}


def test_loopback_transport_requires_open():
    t = mcp.LoopbackTransport()
    with pytest.raises(ConfigError):
        t.handshake(mcp.MCPHandshakeRequest(client_protocol_version="2025-06-18"))


def test_non_loopback_transport_unavailable_in_bundle1():
    desc = mcp.MCPTransportDescriptor(kind=mcp.MCPTransportKind.STDIO, endpoint="cmd")
    with pytest.raises(ConfigError):
        mcp.transport_for(desc)


# ── negotiation + immutable snapshots ───────────────────────────────────────
def test_connect_negotiates_ready_session_with_snapshot():
    rt = _runtime()
    rt.register(mcp.MCPServerManifest(id="crm"))
    conn, sess = rt.connect("external", "crm")
    assert conn.state is mcp.MCPConnectionState.CONNECTED
    assert sess.state is mcp.MCPSessionState.READY
    assert sess.protocol_version == "2025-06-18"
    assert sess.snapshot is not None and sess.snapshot.has("tools")


def test_negotiated_snapshot_is_immutable_frozen():
    rt = _runtime()
    rt.register(mcp.MCPServerManifest(id="crm"))
    _, sess = rt.connect("external", "crm")
    with pytest.raises((AttributeError, TypeError)):  # frozen dataclass
        sess.snapshot.protocol_version = "x"  # type: ignore[misc]


def test_reconnect_creates_new_snapshot():
    clock = _counter_clock()
    rt = _runtime(clock=clock)
    rt.register(mcp.MCPServerManifest(id="crm"))
    conn, sess = rt.connect("external", "crm")
    first = sess.snapshot.snapshot_id
    rt._connections.disconnect(conn.connection_id)
    conn2, sess2 = rt.reconnect(conn.connection_id)
    assert conn2.state is mcp.MCPConnectionState.CONNECTED
    assert conn2.attempts == 1
    assert sess2.snapshot.snapshot_id != first          # new snapshot per reconnect
    assert sess2.snapshot.kinds == sess.snapshot.kinds   # same negotiated contract


def test_protocol_mismatch_fails_fast():
    server = mcp.EchoMCPServer(protocol_version="1999-01-01")  # unsupported by this client
    t = mcp.LoopbackTransport(server=server)
    t.open()
    with pytest.raises(ConfigError):
        mcp.negotiate(t, server_id="s1", client_protocol_version="2025-06-18", clock=lambda: 0.0)


# ── catalog ──────────────────────────────────────────────────────────────────
def test_catalog_records_and_discovers():
    rt = _runtime()
    rt.register(mcp.MCPServerManifest(id="crm"))
    _, sess = rt.connect("external", "crm")
    sid = sess.server_id
    assert rt.negotiated_capabilities(sid).snapshot_id == sess.snapshot.snapshot_id
    assert sid in rt.catalog.discover("tools")
    assert rt.catalog.discover("nonexistent") == ()
    # EchoMCPServer advertises tools/resources/prompts/sampling (sampling added M6.8B-2).
    assert len(rt.catalog.descriptors_for(sid)) == 4


# ── factory / config ─────────────────────────────────────────────────────────
def test_factory_default_is_deterministic():
    a = mcp.MCPFactory.default(clock=lambda: 0.0)
    b = mcp.MCPFactory.default(clock=lambda: 0.0)
    da = a.register(mcp.MCPServerManifest(id="crm"))
    db = b.register(mcp.MCPServerManifest(id="crm"))
    assert da.server.identity.server_id == db.server.identity.server_id  # content-addressed


def test_configuration_validates():
    mcp.MCPConfiguration.default()  # ok
    for bad in (
        {"connect_timeout_s": 0},
        {"heartbeat_interval_s": -1},
        {"max_reconnect_attempts": -1},
        {"max_connections": 0},
        {"client_protocol_version": "nope"},
        {"default_namespace": ""},
    ):
        with pytest.raises(ConfigError):
            mcp.MCPConfiguration(**bad)  # type: ignore[arg-type]


def test_max_connections_enforced():
    rt = mcp.MCPFactory.create(mcp.MCPConfiguration(max_connections=1), clock=_counter_clock())
    rt.register(mcp.MCPServerManifest(id="a"))
    rt.register(mcp.MCPServerManifest(id="b"))
    rt.connect("external", "a")
    with pytest.raises(ConfigError):
        rt.connect("external", "b")


def test_shutdown_closes_connections():
    rt = _runtime(clock=_counter_clock())
    rt.register(mcp.MCPServerManifest(id="crm"))
    rt.connect("external", "crm")
    assert rt.statistics().connections == 1
    rt.shutdown()
    assert rt.statistics().connections == 0


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
        "core", "tenant",  # tenant permitted (isolation)
        "mcp", "__future__", "abc", "collections", "dataclasses", "enum", "typing", "hashlib",
        "time", "itertools",
        # M6.8B-2 / M6.8.1 real-transport stdlib deps (no third-party dependency)
        "json", "shlex", "subprocess", "urllib", "ssl", "select", "os",
    }
    tops = _imports("mcp")
    assert tops <= allowed, tops - allowed
    # never a sibling runtime
    for forbidden in ("tool", "agent", "workflow", "skill", "provider", "pipeline",
                      "reasoning", "integration", "bridge", "memory", "knowledge",
                      "context", "prompt"):
        assert forbidden not in tops, forbidden


def test_no_certified_capability_imports_mcp():
    # `bridge` is the composition root and legitimately imports mcp (MCPToolBridge,
    # M6.8B-2) — the ProviderModelBridge precedent; it is excluded, like other
    # composition roots (integration).
    for pkg in ("core", "agent", "workflow", "loop", "tool", "skill", "hooks", "pipeline",
                "reasoning", "provider", "context", "prompt", "memory", "knowledge",
                "integration"):
        assert "mcp" not in _imports(pkg), pkg


def test_mcp_error_types_are_platform_errors():
    # Bundle 1 reuses core's typed errors (the MCPError hierarchy is Bundle 2).
    assert issubclass(ConfigError, PlatformError)
