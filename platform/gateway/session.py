"""ClientSession — one connected external client (M4.2).

Identity + lifecycle + delivery buffer + subscriptions for a single connection. It
holds **no Runtime state** — only the client's own channels and its `EventStream`.
Heartbeat/idle are expressed as pure predicates over an injected clock so they are
deterministic and testable (the transport drives the timing).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from core.events import Event

from .scope import EventChannel
from .stream import EventStream


class ConnectionState(StrEnum):
    CONNECTING = "connecting"
    CONNECTED = "connected"
    CLOSING = "closing"
    CLOSED = "closed"


@dataclass(slots=True)
class ClientSession:
    id: str
    stream: EventStream
    created_at: float
    last_seen: float
    state: ConnectionState = ConnectionState.CONNECTED
    # Opaque auth context from the authentication hook (never interpreted here).
    auth: Any = None
    metadata: dict[str, Any] = field(default_factory=dict)
    channels: set[EventChannel] = field(default_factory=set)

    # ── subscriptions ────────────────────────────────────────────────────────
    def matches(self, event: Event) -> bool:
        """True if any of this client's channels admits the event (dedup: one
        delivery per client regardless of how many channels match)."""
        return any(ch.matches(event) for ch in self.channels)

    # ── heartbeat / idle (pure; the transport supplies `now`) ───────────────
    def touch(self, now: float) -> None:
        """Record client liveness (a pong / any inbound frame)."""
        self.last_seen = now

    def is_idle(self, now: float, threshold_s: float) -> bool:
        return (now - self.last_seen) >= threshold_s

    def close(self) -> None:
        self.state = ConnectionState.CLOSED
        self.stream.close()
