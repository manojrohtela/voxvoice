"""MCP lifecycles (M6.8B-1) — two deterministic FSMs on `core.fsm`.

Dual-FSM shape (like Tool/Workflow Runtime): a **connection** FSM (transport
liveness) and a **session** FSM (protocol state atop a live connection). Both
dogfood the shared framework — no hand-written transition tables. Illegal moves
raise typed `FSMError`s. These own *lifecycle mechanics only*; the managers own
*when* to fire a trigger, and the transport owns the wire.
"""

from __future__ import annotations

from collections.abc import Callable
from enum import StrEnum

from core.fsm import StateMachine, StateMachineDefinition

from .domain import (
    MCPConnection,
    MCPConnectionState,
    MCPSession,
    MCPSessionState,
)


# ── connection FSM ───────────────────────────────────────────────────────────
class MCPConnectionTrigger(StrEnum):
    CONNECT = "connect"        # REGISTERED  -> CONNECTING
    ESTABLISH = "establish"    # CONNECTING  -> CONNECTED
    FAIL = "fail"              # CONNECTING  -> DISCONNECTED
    DEGRADE = "degrade"        # CONNECTED   -> DEGRADED
    RECOVER = "recover"        # DEGRADED    -> CONNECTED
    DISCONNECT = "disconnect"  # CONNECTED/DEGRADED -> DISCONNECTED
    RECONNECT = "reconnect"    # DISCONNECTED -> CONNECTING
    CLOSE = "close"            # any non-terminal -> CLOSED


def _connection_def() -> StateMachineDefinition[MCPConnectionState, MCPConnectionTrigger]:
    d: StateMachineDefinition[MCPConnectionState, MCPConnectionTrigger] = StateMachineDefinition()
    S, T = MCPConnectionState, MCPConnectionTrigger
    d.add(S.REGISTERED, T.CONNECT, S.CONNECTING)
    d.add(S.CONNECTING, T.ESTABLISH, S.CONNECTED)
    d.add(S.CONNECTING, T.FAIL, S.DISCONNECTED)
    d.add(S.CONNECTED, T.DEGRADE, S.DEGRADED)
    d.add(S.DEGRADED, T.RECOVER, S.CONNECTED)
    d.add(S.CONNECTED, T.DISCONNECT, S.DISCONNECTED)
    d.add(S.DEGRADED, T.DISCONNECT, S.DISCONNECTED)
    d.add(S.DISCONNECTED, T.RECONNECT, S.CONNECTING)
    d.add(S.REGISTERED, T.CLOSE, S.CLOSED)
    d.add(S.CONNECTING, T.CLOSE, S.CLOSED)
    d.add(S.CONNECTED, T.CLOSE, S.CLOSED)
    d.add(S.DEGRADED, T.CLOSE, S.CLOSED)
    d.add(S.DISCONNECTED, T.CLOSE, S.CLOSED)
    return d


MCP_CONNECTION_LIFECYCLE = _connection_def()


def advance_connection(
    connection: MCPConnection, trigger: MCPConnectionTrigger,
    *, clock: Callable[[], float] | None = None,
) -> MCPConnection:
    """Validate `trigger` against the connection's state; return it in the new
    state (with `updated_at` refreshed)."""
    import time

    the_clock = clock or time.time
    fsm: StateMachine[MCPConnectionState, MCPConnectionTrigger] = StateMachine(
        MCP_CONNECTION_LIFECYCLE, connection.state,
        machine_id=connection.connection_id, clock=the_clock,
    )
    return connection.with_state(fsm.fire(trigger), updated_at=the_clock())


# ── session FSM ──────────────────────────────────────────────────────────────
class MCPSessionTrigger(StrEnum):
    INITIALIZE = "initialize"  # CREATED      -> INITIALIZING
    NEGOTIATE = "negotiate"    # INITIALIZING -> NEGOTIATING
    READY = "ready"            # NEGOTIATING  -> READY
    CLOSE = "close"            # READY        -> CLOSING
    FINISH = "finish"          # CLOSING      -> CLOSED


def _session_def() -> StateMachineDefinition[MCPSessionState, MCPSessionTrigger]:
    d: StateMachineDefinition[MCPSessionState, MCPSessionTrigger] = StateMachineDefinition()
    S, T = MCPSessionState, MCPSessionTrigger
    d.add(S.CREATED, T.INITIALIZE, S.INITIALIZING)
    d.add(S.INITIALIZING, T.NEGOTIATE, S.NEGOTIATING)
    d.add(S.NEGOTIATING, T.READY, S.READY)
    d.add(S.READY, T.CLOSE, S.CLOSING)
    d.add(S.CLOSING, T.FINISH, S.CLOSED)
    # A session may be abandoned before READY (e.g. a failed handshake).
    d.add(S.CREATED, T.CLOSE, S.CLOSING)
    d.add(S.INITIALIZING, T.CLOSE, S.CLOSING)
    d.add(S.NEGOTIATING, T.CLOSE, S.CLOSING)
    return d


MCP_SESSION_LIFECYCLE = _session_def()


def advance_session(
    session: MCPSession, trigger: MCPSessionTrigger,
    *, clock: Callable[[], float] | None = None,
) -> MCPSession:
    """Validate `trigger` against the session's state; return it in the new
    state (with `updated_at` refreshed)."""
    import time

    the_clock = clock or time.time
    fsm: StateMachine[MCPSessionState, MCPSessionTrigger] = StateMachine(
        MCP_SESSION_LIFECYCLE, session.state,
        machine_id=session.session_id, clock=the_clock,
    )
    return session.with_state(fsm.fire(trigger), updated_at=the_clock())
