"""MCPConnectionManager (M6.8B-1) — connection lifecycle + transitions only.

Owns the connection FSM and the transport handle per connection (open/close is
connection lifecycle, not request execution). It never performs a JSON-RPC
request; negotiation is the session manager's concern, driven over the transport
this manager opened. Deterministic; enforces `max_connections`.
"""

from __future__ import annotations

from collections.abc import Callable

from core.errors import ConfigError

from .domain import MCPConnection, MCPConnectionState, MCPServer
from .ids import connection_id_for
from .lifecycle import MCPConnectionTrigger, advance_connection
from .transport import MCPTransport, transport_for


class MCPConnectionManager:
    def __init__(
        self,
        *,
        offline_mode: bool = True,
        max_connections: int = 64,
        connect_timeout_s: float = 10.0,
        request_timeout_s: float = 30.0,
        trust=None,  # noqa: ANN001 — MCPTrustPolicy | None (avoids a hard import here)
        clock: Callable[[], float] | None = None,
    ) -> None:
        import time

        self._offline = offline_mode
        self._max = max_connections
        self._connect_timeout = connect_timeout_s
        self._request_timeout = request_timeout_s
        self._trust = trust
        self._clock = clock or time.time
        self._connections: dict[str, MCPConnection] = {}
        self._transports: dict[str, MCPTransport] = {}

    def connect(self, server: MCPServer) -> MCPConnection:
        """REGISTERED → CONNECTING → CONNECTED, opening the transport. Raises if
        the active-connection cap is exceeded."""
        if self._active_count() >= self._max:
            raise ConfigError(f"max_connections ({self._max}) reached")
        now = self._clock()
        conn = MCPConnection(
            connection_id=connection_id_for(server.identity.server_id, 0, now),
            server_id=server.identity.server_id,
            transport_kind=server.manifest.transport.kind,
            state=MCPConnectionState.REGISTERED, attempts=0, created_at=now, updated_at=now,
        )
        transport = transport_for(
            server.manifest.transport, offline_mode=self._offline,
            connect_timeout_s=self._connect_timeout, request_timeout_s=self._request_timeout,
            trust=self._trust,
        )
        conn = advance_connection(conn, MCPConnectionTrigger.CONNECT, clock=self._clock)
        transport.open()
        conn = advance_connection(conn, MCPConnectionTrigger.ESTABLISH, clock=self._clock)
        self._connections[conn.connection_id] = conn
        self._transports[conn.connection_id] = transport
        return conn

    def reconnect(self, connection_id: str) -> MCPConnection:
        """DISCONNECTED → CONNECTING → CONNECTED, reopening the transport and
        incrementing the attempt count. A fresh negotiation (new snapshot) is
        the caller's next step."""
        conn = self._get(connection_id)
        if conn.state is not MCPConnectionState.DISCONNECTED:
            conn = advance_connection(conn, MCPConnectionTrigger.DISCONNECT, clock=self._clock)
        conn = conn.with_attempt(updated_at=self._clock())
        conn = advance_connection(conn, MCPConnectionTrigger.RECONNECT, clock=self._clock)
        self._transports[connection_id].open()
        conn = advance_connection(conn, MCPConnectionTrigger.ESTABLISH, clock=self._clock)
        self._connections[connection_id] = conn
        return conn

    def degrade(self, connection_id: str) -> MCPConnection:
        return self._advance(connection_id, MCPConnectionTrigger.DEGRADE)

    def recover(self, connection_id: str) -> MCPConnection:
        return self._advance(connection_id, MCPConnectionTrigger.RECOVER)

    def disconnect(self, connection_id: str) -> MCPConnection:
        return self._advance(connection_id, MCPConnectionTrigger.DISCONNECT)

    def close(self, connection_id: str) -> MCPConnection:
        conn = advance_connection(
            self._get(connection_id), MCPConnectionTrigger.CLOSE, clock=self._clock
        )
        self._connections[connection_id] = conn
        transport = self._transports.pop(connection_id, None)
        if transport is not None:
            transport.close()
        return conn

    def transport_for_connection(self, connection_id: str) -> MCPTransport:
        try:
            return self._transports[connection_id]
        except KeyError:
            raise ConfigError(f"no open transport for connection {connection_id!r}") from None

    def get(self, connection_id: str) -> MCPConnection:
        return self._get(connection_id)

    def list(self) -> tuple[MCPConnection, ...]:
        return tuple(self._connections[k] for k in sorted(self._connections))

    def count(self) -> int:
        return self._active_count()

    # ── internals ────────────────────────────────────────────────────────────
    def _advance(self, connection_id: str, trigger: MCPConnectionTrigger) -> MCPConnection:
        conn = advance_connection(self._get(connection_id), trigger, clock=self._clock)
        self._connections[connection_id] = conn
        return conn

    def _get(self, connection_id: str) -> MCPConnection:
        try:
            return self._connections[connection_id]
        except KeyError:
            raise ConfigError(f"unknown connection {connection_id!r}") from None

    def _active_count(self) -> int:
        return sum(
            1 for c in self._connections.values() if c.state is not MCPConnectionState.CLOSED
        )
