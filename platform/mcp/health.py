"""MCPHealthMonitor (M6.8B-2) — heartbeat, degraded detection, reconnect policy.

Owns MCP-server health signals and the **deterministic** reconnection policy:
`ping` liveness, a bounded retry budget, and an exponential backoff *schedule*
(computed, never slept — determinism). It makes no failover decision outside MCP
(Provider owns its own failover); it only tells the connection manager *whether*
and *after how long* to reconnect.
"""

from __future__ import annotations

from collections.abc import Callable

from .errors import MCPError
from .protocol import MCPMethod, MCPRequest
from .transport import MCPTransport


class MCPHealthMonitor:
    def __init__(
        self,
        *,
        max_reconnect_attempts: int = 3,
        heartbeat_interval_s: float = 30.0,
        backoff_base_s: float = 0.5,
        backoff_cap_s: float = 30.0,
        clock: Callable[[], float] | None = None,
    ) -> None:
        import time

        self._max = max_reconnect_attempts
        self._heartbeat = heartbeat_interval_s
        self._base = backoff_base_s
        self._cap = backoff_cap_s
        self._clock = clock or time.time

    def ping(self, transport: MCPTransport) -> bool:
        """Liveness check via the MCP `ping` method. Any `MCPError` (or a JSON-RPC
        error) means unhealthy; never raises."""
        try:
            response = transport.request(MCPRequest(request_id="ping", method=MCPMethod.PING))
        except MCPError:
            return False
        return not response.is_error

    def should_reconnect(self, attempts: int) -> bool:
        return attempts < self._max

    def backoff_for(self, attempt: int) -> float:
        """Deterministic exponential backoff, capped. `attempt` is 0-based."""
        if attempt < 0:
            attempt = 0
        return min(self._base * (2 ** attempt), self._cap)

    def reconnect_schedule(self) -> tuple[float, ...]:
        """The full deterministic backoff schedule for the retry budget."""
        return tuple(self.backoff_for(a) for a in range(self._max))

    @property
    def heartbeat_interval_s(self) -> float:
        return self._heartbeat

    @property
    def max_reconnect_attempts(self) -> int:
        return self._max
