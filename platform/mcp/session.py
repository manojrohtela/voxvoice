"""MCPSessionManager (M6.8B-1) — session lifecycle + capability negotiation.

Owns the session FSM (`CREATED → INITIALIZING → NEGOTIATING → READY → CLOSING →
CLOSED`) and runs the one-time `initialize` negotiation over the transport the
connection manager opened. The negotiated snapshot is frozen onto the session
and never renegotiated (M6.8A §5). No request execution (Bundle 2).
"""

from __future__ import annotations

from collections.abc import Callable

from core.errors import ConfigError

from .domain import MCPCapabilitySnapshot, MCPConnection, MCPSession, MCPSessionState
from .ids import session_id_for
from .lifecycle import MCPSessionTrigger, advance_session
from .negotiation import negotiate
from .transport import MCPTransport


class MCPSessionManager:
    def __init__(
        self,
        *,
        client_protocol_version: str,
        client_name: str = "voxvoice",
        clock: Callable[[], float] | None = None,
    ) -> None:
        import time

        self._client_protocol_version = client_protocol_version
        self._client_name = client_name
        self._clock = clock or time.time
        self._sessions: dict[str, MCPSession] = {}

    def establish(
        self, connection: MCPConnection, transport: MCPTransport
    ) -> tuple[MCPSession, MCPCapabilitySnapshot]:
        """Drive `initialize → negotiate → ready`, freezing the negotiated
        capability snapshot onto the session. Returns the READY session and its
        snapshot."""
        now = self._clock()
        session = MCPSession(
            session_id=session_id_for(connection.connection_id, now),
            connection_id=connection.connection_id, server_id=connection.server_id,
            state=MCPSessionState.CREATED, created_at=now, updated_at=now,
        )
        session = advance_session(session, MCPSessionTrigger.INITIALIZE, clock=self._clock)
        session = advance_session(session, MCPSessionTrigger.NEGOTIATE, clock=self._clock)
        snapshot = negotiate(
            transport, server_id=connection.server_id,
            client_protocol_version=self._client_protocol_version,
            client_name=self._client_name, clock=self._clock,
        )
        session = session.with_negotiation(snapshot, updated_at=self._clock())
        session = advance_session(session, MCPSessionTrigger.READY, clock=self._clock)
        self._sessions[session.session_id] = session
        return session, snapshot

    def close(self, session_id: str) -> MCPSession:
        session = advance_session(self._get(session_id), MCPSessionTrigger.CLOSE, clock=self._clock)
        session = advance_session(session, MCPSessionTrigger.FINISH, clock=self._clock)
        self._sessions[session_id] = session
        return session

    def get(self, session_id: str) -> MCPSession:
        return self._get(session_id)

    def list(self) -> tuple[MCPSession, ...]:
        return tuple(self._sessions[k] for k in sorted(self._sessions))

    def sessions_for(self, connection_id: str) -> tuple[MCPSession, ...]:
        return tuple(
            s for s in self.list() if s.connection_id == connection_id
        )

    def count(self) -> int:
        return sum(1 for s in self._sessions.values() if s.state is not MCPSessionState.CLOSED)

    def _get(self, session_id: str) -> MCPSession:
        try:
            return self._sessions[session_id]
        except KeyError:
            raise ConfigError(f"unknown session {session_id!r}") from None
