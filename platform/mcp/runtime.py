"""MCPRuntime (M6.8B-1 core + M6.8B-2 execution) — the capability facade.

Composes the registry, catalog, connection/session managers, request executor,
authentication, health, replay, and observability into the MCP Runtime's public
surface: register servers, connect (open + establish + negotiate + authenticate),
execute MCP methods (tools/resources/prompts/sampling, notifications, streaming),
reconnect (new snapshot), replay a session's transcript, and report
diagnostics. Owns composition of its **own** managers only — never a sibling
runtime, never planning/reasoning/tool-lifecycle.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator, Mapping
from typing import Any

from tenant import TenantContext

from .authentication import MCPAuthentication, MCPCredential
from .catalog import MCPCapabilityRegistry
from .config import MCPConfiguration
from .connection import MCPConnectionManager
from .diagnostics import MCPRuntimeDiagnostics, build_diagnostics
from .domain import (
    MCPCapabilitySnapshot,
    MCPConnection,
    MCPRuntimeStatistics,
    MCPServer,
    MCPServerDescriptor,
    MCPServerManifest,
    MCPSession,
)
from .health import MCPHealthMonitor
from .observability import (
    InMemoryMCPObservabilitySink,
    MCPEvent,
    MCPEventType,
    MCPMetrics,
)
from .protocol import MCPResponseChunk, MCPToolCallResult
from .registry import MCPServerRegistry
from .replay import MCPReplayEngine, MCPReplayRecord, MCPSessionRecorder
from .request_executor import MCPRequestExecutor
from .session import MCPSessionManager


class MCPRuntime:
    def __init__(
        self,
        *,
        config: MCPConfiguration,
        registry: MCPServerRegistry,
        catalog: MCPCapabilityRegistry,
        connections: MCPConnectionManager,
        sessions: MCPSessionManager,
        authentication: MCPAuthentication,
        health: MCPHealthMonitor,
        observability: InMemoryMCPObservabilitySink | None = None,
        metrics: MCPMetrics | None = None,
        replay_engine: MCPReplayEngine | None = None,
        clock: Callable[[], float] | None = None,
    ) -> None:
        import time

        self._config = config
        self._registry = registry
        self._catalog = catalog
        self._connections = connections
        self._sessions = sessions
        self._auth = authentication
        self._health = health
        self._observability = observability or InMemoryMCPObservabilitySink()
        self._metrics = metrics or MCPMetrics()
        self._replay_engine = replay_engine or MCPReplayEngine()
        self._clock = clock or time.time
        # per-connection execution state (ids/handles only; no capability state)
        self._executors: dict[str, MCPRequestExecutor] = {}
        self._recorders: dict[str, MCPSessionRecorder] = {}
        self._credentials: dict[str, MCPCredential] = {}
        # F2 isolation: the active connection is keyed by (tenant, server), never
        # by server alone — one tenant never shares a connection, credential, or
        # replay transcript with another. Reverse map supports deterministic close.
        self._active_by_key: dict[str, str] = {}   # "<tenant>::<server_id>" -> connection_id
        self._key_by_connection: dict[str, str] = {}

    # ── read-only exposure ─────────────────────────────────────────────────────
    @property
    def registry(self) -> MCPServerRegistry:
        return self._registry

    @property
    def catalog(self) -> MCPCapabilityRegistry:
        return self._catalog

    @property
    def observability(self) -> InMemoryMCPObservabilitySink:
        return self._observability

    @property
    def metrics(self) -> MCPMetrics:
        return self._metrics

    @property
    def health(self) -> MCPHealthMonitor:
        return self._health

    # ── registration / discovery ───────────────────────────────────────────────
    def register(self, manifest: MCPServerManifest) -> MCPServerDescriptor:
        return self._registry.register(manifest)

    def get_server(self, namespace: str, id_: str, version: str | None = None) -> MCPServer:
        return self._registry.get(namespace, id_, version)

    def list_servers(self) -> tuple[MCPServerDescriptor, ...]:
        return self._registry.list()

    # ── tenant isolation (F2) ────────────────────────────────────────────────────
    @staticmethod
    def _isolation_key(
        server_id: str, tenant: TenantContext | None, org_id: str | None
    ) -> str:
        """The (tenant, server) isolation key. A `TenantContext` wins; else a raw
        `org_id` (the field the certified Tool seam carries); else the single
        deterministic default for offline/single-tenant use."""
        who = tenant.org_id if tenant is not None else (org_id or "__default__")
        return f"{who}::{server_id}"

    def active_connection_id(
        self, namespace: str, id_: str, *,
        tenant: TenantContext | None = None, org_id: str | None = None,
    ) -> str | None:
        """The active connection id for a (tenant, server) pair, if any (read-only)."""
        server_id = self._registry.get(namespace, id_).identity.server_id
        return self._active_by_key.get(self._isolation_key(server_id, tenant, org_id))

    # ── connect / reconnect ─────────────────────────────────────────────────────
    def connect(
        self, namespace: str, id_: str, version: str | None = None, *, trace_id: str = "",
        tenant: TenantContext | None = None, org_id: str | None = None,
    ) -> tuple[MCPConnection, MCPSession]:
        server = self._registry.get(namespace, id_, version)
        credential = self._authenticate(server.manifest, trace_id=trace_id)
        connection = self._connections.connect(server)
        transport = self._connections.transport_for_connection(connection.connection_id)
        transport.apply_credential(credential)  # F1: auth on the wire before the handshake
        session, snapshot = self._sessions.establish(connection, transport)
        self._catalog.record(snapshot)
        key = self._isolation_key(server.identity.server_id, tenant, org_id)
        self._wire_execution(connection, session, snapshot, credential, key)
        self._observability.record(MCPEvent(
            type=MCPEventType.CONNECT, server_id=server.identity.server_id,
            session_id=session.session_id, trace_id=trace_id, at=self._clock(),
            data={"protocol": snapshot.protocol_version},
        ))
        return connection, session

    def reconnect(
        self, connection_id: str, *, trace_id: str = ""
    ) -> tuple[MCPConnection, MCPSession]:
        connection = self._connections.reconnect(connection_id)
        transport = self._connections.transport_for_connection(connection.connection_id)
        credential = self._credentials.get(connection_id)
        if credential is not None:
            transport.apply_credential(credential)  # F1: re-auth the reopened transport
        session, snapshot = self._sessions.establish(connection, transport)
        self._catalog.record(snapshot)
        self._metrics.incr("reconnect")
        key = self._key_by_connection.get(
            connection_id, self._isolation_key(connection.server_id, None, None)
        )
        self._wire_execution(connection, session, snapshot, credential, key)
        self._observability.record(MCPEvent(
            type=MCPEventType.RETRY, server_id=connection.server_id,
            session_id=session.session_id, trace_id=trace_id, at=self._clock(),
            data={"attempts": connection.attempts},
        ))
        return connection, session

    def negotiated_capabilities(self, server_id: str) -> MCPCapabilitySnapshot | None:
        return self._catalog.snapshot_for(server_id)

    def close_connection(self, connection_id: str) -> MCPConnection:
        for session in self._sessions.sessions_for(connection_id):
            if session.state.value not in {"closed", "closing"}:
                self._sessions.close(session.session_id)
        conn = self._connections.close(connection_id)
        key = self._key_by_connection.pop(connection_id, None)
        if key is not None and self._active_by_key.get(key) == connection_id:
            self._active_by_key.pop(key, None)
        self._observability.record(MCPEvent(
            type=MCPEventType.DISCONNECT, server_id=conn.server_id, at=self._clock(),
        ))
        return conn

    # ── protocol execution (via the per-connection executor) ────────────────────
    def list_tools(
        self, namespace: str, id_: str, *, trace_id: str = "",
        tenant: TenantContext | None = None, org_id: str | None = None,
    ) -> tuple[Mapping[str, Any], ...]:
        executor = self._executor_for_server(
            namespace, id_, trace_id=trace_id, tenant=tenant, org_id=org_id)
        return executor.list_tools()

    def call_tool(
        self, namespace: str, id_: str, tool: str, arguments: Mapping[str, Any],
        *, trace_id: str = "", session_id: str = "",
        tenant: TenantContext | None = None, org_id: str | None = None,
    ) -> MCPToolCallResult:
        executor, server_id = self._executor_for_server(
            namespace, id_, trace_id=trace_id, with_server_id=True, tenant=tenant, org_id=org_id
        )
        return self._execute(
            "tools/call", server_id, session_id, trace_id,
            lambda: executor.call_tool(tool, arguments),
        )

    def read_resource(
        self, namespace: str, id_: str, uri: str, *, trace_id: str = "", session_id: str = "",
        tenant: TenantContext | None = None, org_id: str | None = None,
    ) -> Mapping[str, Any]:
        executor, server_id = self._executor_for_server(
            namespace, id_, trace_id=trace_id, with_server_id=True, tenant=tenant, org_id=org_id
        )
        return self._execute(
            "resources/read", server_id, session_id, trace_id,
            lambda: executor.read_resource(uri),
        )

    def get_prompt(
        self, namespace: str, id_: str, name: str,
        arguments: Mapping[str, Any] | None = None, *, trace_id: str = "", session_id: str = "",
        tenant: TenantContext | None = None, org_id: str | None = None,
    ) -> Mapping[str, Any]:
        executor, server_id = self._executor_for_server(
            namespace, id_, trace_id=trace_id, with_server_id=True, tenant=tenant, org_id=org_id
        )
        return self._execute(
            "prompts/get", server_id, session_id, trace_id,
            lambda: executor.get_prompt(name, arguments),
        )

    def create_message(
        self, namespace: str, id_: str, params: Mapping[str, Any],
        *, trace_id: str = "", session_id: str = "",
        tenant: TenantContext | None = None, org_id: str | None = None,
    ) -> Mapping[str, Any]:
        executor, server_id = self._executor_for_server(
            namespace, id_, trace_id=trace_id, with_server_id=True, tenant=tenant, org_id=org_id
        )
        return self._execute(
            "sampling/createMessage", server_id, session_id, trace_id,
            lambda: executor.create_message(params),
        )

    def stream_tool(
        self, namespace: str, id_: str, tool: str, arguments: Mapping[str, Any],
        *, trace_id: str = "",
        tenant: TenantContext | None = None, org_id: str | None = None,
    ) -> Iterator[MCPResponseChunk]:
        executor = self._executor_for_server(
            namespace, id_, trace_id=trace_id, tenant=tenant, org_id=org_id)
        return executor.stream_tool(tool, arguments)

    # ── replay ──────────────────────────────────────────────────────────────────
    def replay_record(self, connection_id: str) -> MCPReplayRecord:
        recorder = self._recorders.get(connection_id)
        if recorder is None:
            raise KeyError(f"no session recorder for connection {connection_id!r}")
        return recorder.build(now=self._clock())

    @property
    def replay_engine(self) -> MCPReplayEngine:
        return self._replay_engine

    # ── introspection ──────────────────────────────────────────────────────────
    def statistics(self, *, healthy: bool = True) -> MCPRuntimeStatistics:
        return MCPRuntimeStatistics(
            name=self._config.name, state="ready", healthy=healthy,
            servers=self._registry.names(),
            connections=self._connections.count(), sessions=self._sessions.count(),
            metrics={"registered_servers": len(self._registry.list()),
                     "requests": self._metrics.snapshot().request_count},
        )

    def diagnostics(self, *, healthy: bool = True) -> MCPRuntimeDiagnostics:
        return build_diagnostics(
            statistics=self.statistics(healthy=healthy),
            metrics=self._metrics.snapshot(), healthy=healthy,
        )

    def shutdown(self) -> None:
        for connection in self._connections.list():
            if connection.state.value != "closed":
                self.close_connection(connection.connection_id)

    # ── internals ──────────────────────────────────────────────────────────────
    def _authenticate(self, manifest: MCPServerManifest, *, trace_id: str) -> MCPCredential:
        from .errors import MCPAuthenticationError

        try:
            return self._auth.authenticate(manifest)
        except MCPAuthenticationError:
            self._metrics.incr("auth_failure")
            self._observability.record(MCPEvent(
                type=MCPEventType.FAILURE, trace_id=trace_id, at=self._clock(),
                data={"stage": "authentication", "server": manifest.qualified_name},
            ))
            raise

    def _wire_execution(
        self, connection: MCPConnection, session: MCPSession,
        snapshot: MCPCapabilitySnapshot, credential, key: str,
    ) -> None:
        transport = self._connections.transport_for_connection(connection.connection_id)
        recorder = MCPSessionRecorder(
            server_id=connection.server_id, session_id=session.session_id,
            protocol_version=snapshot.protocol_version,
        )
        executor = MCPRequestExecutor(
            transport, snapshot=snapshot, on_frame=recorder.record, clock=self._clock,
        )
        self._executors[connection.connection_id] = executor
        self._recorders[connection.connection_id] = recorder
        if credential is not None:
            self._credentials[connection.connection_id] = credential
        self._active_by_key[key] = connection.connection_id
        self._key_by_connection[connection.connection_id] = key

    def _executor_for_server(
        self, namespace: str, id_: str, *, trace_id: str, with_server_id: bool = False,
        tenant: TenantContext | None = None, org_id: str | None = None,
    ):
        server = self._registry.get(namespace, id_)
        server_id = server.identity.server_id
        key = self._isolation_key(server_id, tenant, org_id)
        connection_id = self._active_by_key.get(key)
        if connection_id is None:
            connection, _ = self.connect(
                namespace, id_, trace_id=trace_id, tenant=tenant, org_id=org_id)
            connection_id = connection.connection_id
        executor = self._executors[connection_id]
        return (executor, server_id) if with_server_id else executor

    def _execute(self, method: str, server_id: str, session_id: str, trace_id: str, call):
        self._metrics.incr("request")
        self._observability.record(MCPEvent(
            type=MCPEventType.REQUEST, server_id=server_id, session_id=session_id,
            trace_id=trace_id, at=self._clock(), data={"method": method},
        ))
        from .errors import MCPError

        try:
            result = call()
        except MCPError:
            self._metrics.incr("failure")
            self._observability.record(MCPEvent(
                type=MCPEventType.FAILURE, server_id=server_id, session_id=session_id,
                trace_id=trace_id, at=self._clock(), data={"method": method},
            ))
            raise
        self._observability.record(MCPEvent(
            type=MCPEventType.RESPONSE, server_id=server_id, session_id=session_id,
            trace_id=trace_id, at=self._clock(), data={"method": method},
        ))
        return result
