"""ConnectionManager — the client connection lifecycle (M4.2).

Owns the set of connected `ClientSession`s: connect (with an **authentication
hook**), disconnect, lookup, heartbeat/idle sweeps, and connection metrics. It owns
client lifecycle only — never Runtime state, never event creation.

Authentication is an **extension point** (a `Callable` returning an opaque auth
context, or `None` to reject) — no implementation here (M4.2 non-goal). Heartbeat /
idle are pure predicates over an injected clock (deterministic; the transport drives
the timing).
"""

from __future__ import annotations

import time
from collections.abc import Callable, Iterable
from typing import Any

from .metrics import GatewayMetrics
from .session import ClientSession, ConnectionState
from .stream import EventStream, OverflowPolicy

# Auth hook: given the connect request context, return an opaque auth context, or
# None to reject the connection. Default (below) accepts everyone.
Authenticator = Callable[[str, dict[str, Any]], Any]


def _allow_all(client_id: str, context: dict[str, Any]) -> Any:  # noqa: ARG001
    return {}  # a permissive, non-None auth context


class ConnectionManager:
    def __init__(
        self,
        *,
        metrics: GatewayMetrics | None = None,
        authenticate: Authenticator | None = None,
        clock: Callable[[], float] | None = None,
        stream_maxsize: int = 1000,
        overflow_policy: OverflowPolicy = OverflowPolicy.DROP_OLDEST,
    ) -> None:
        self._sessions: dict[str, ClientSession] = {}
        self._metrics = metrics or GatewayMetrics()
        self._authenticate = authenticate or _allow_all
        self._clock = clock or time.time
        self._stream_maxsize = stream_maxsize
        self._overflow_policy = overflow_policy

    @property
    def metrics(self) -> GatewayMetrics:
        return self._metrics

    def connect(
        self, client_id: str, *, context: dict[str, Any] | None = None
    ) -> ClientSession:
        """Open a connection. Runs the authentication hook; a `None` result rejects
        (returns no session). Rejects a duplicate client id."""
        if client_id in self._sessions:
            raise ValueError(f"client {client_id!r} already connected")
        auth = self._authenticate(client_id, context or {})
        if auth is None:
            return None  # rejected by the auth hook (extension point)
        now = self._clock()
        session = ClientSession(
            id=client_id,
            stream=EventStream(maxsize=self._stream_maxsize, policy=self._overflow_policy),
            created_at=now,
            last_seen=now,
            auth=auth,
            metadata=dict(context or {}),
        )
        self._sessions[client_id] = session
        self._metrics.connections_opened += 1
        self._metrics.active_connections = len(self._sessions)
        return session

    def disconnect(self, client_id: str) -> None:
        """Close + drop a connection. Idempotent."""
        session = self._sessions.pop(client_id, None)
        if session is None:
            return
        session.state = ConnectionState.CLOSING
        session.close()
        self._metrics.connections_closed += 1
        self._metrics.active_connections = len(self._sessions)

    def get(self, client_id: str) -> ClientSession | None:
        return self._sessions.get(client_id)

    def sessions(self) -> Iterable[ClientSession]:
        return tuple(self._sessions.values())

    def __len__(self) -> int:
        return len(self._sessions)

    # ── heartbeat / idle ─────────────────────────────────────────────────────
    def idle_clients(self, threshold_s: float, *, now: float | None = None) -> list[str]:
        t = now if now is not None else self._clock()
        return [s.id for s in self._sessions.values() if s.is_idle(t, threshold_s)]

    def sweep_idle(self, threshold_s: float, *, now: float | None = None) -> int:
        """Disconnect idle clients (graceful). Returns the number closed."""
        stale = self.idle_clients(threshold_s, now=now)
        for cid in stale:
            self.disconnect(cid)
        return len(stale)

    def close_all(self) -> None:
        for cid in tuple(self._sessions):
            self.disconnect(cid)
