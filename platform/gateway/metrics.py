"""GatewayMetrics — in-process gateway counters (M4.2).

Plain integer counters for connections and delivery. Read-only snapshots for
observability; a future exporter (M4.3+) can bridge these to a metrics backend.
Single-threaded in-process → no locking. This is not an analytics platform (a
non-goal) — just operational counters.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass


@dataclass(slots=True)
class GatewayMetrics:
    connections_opened: int = 0
    connections_closed: int = 0
    active_connections: int = 0
    subscriptions_added: int = 0
    subscriptions_removed: int = 0
    events_received: int = 0  # from the bus
    events_delivered: int = 0  # offered to a client stream
    events_dropped: int = 0  # backpressure drops
    slow_disconnects: int = 0
    serialization_failures: int = 0

    def snapshot(self) -> dict[str, int]:
        return asdict(self)
