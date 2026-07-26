"""MCPCapabilityRegistry (M6.8B-1) — the discovered-capability catalog.

Distinct from `MCPServerRegistry` (which holds *declared* servers): this records
the immutable `MCPCapabilitySnapshot` negotiated live per server, and answers
capability discovery queries. No intelligence, no network — it stores what
negotiation produced (mirrors `ToolCapabilityCatalog`'s read-only posture).
"""

from __future__ import annotations

from .domain import MCPCapabilityDescriptor, MCPCapabilitySnapshot


class MCPCapabilityRegistry:
    """Maps `server_id -> latest negotiated MCPCapabilitySnapshot`. Recording a
    new snapshot (e.g. after a reconnect) replaces the prior one for that
    server — the snapshot itself is immutable."""

    def __init__(self) -> None:
        self._snapshots: dict[str, MCPCapabilitySnapshot] = {}

    def record(self, snapshot: MCPCapabilitySnapshot) -> MCPCapabilitySnapshot:
        self._snapshots[snapshot.server_id] = snapshot
        return snapshot

    def snapshot_for(self, server_id: str) -> MCPCapabilitySnapshot | None:
        return self._snapshots.get(server_id)

    def server_ids(self) -> tuple[str, ...]:
        return tuple(sorted(self._snapshots))

    def discover(self, kind: str) -> tuple[str, ...]:
        """The server_ids whose negotiated snapshot advertises `kind`, sorted
        (deterministic)."""
        return tuple(sorted(
            sid for sid, snap in self._snapshots.items() if snap.has(kind)
        ))

    def descriptors_for(self, server_id: str) -> tuple[MCPCapabilityDescriptor, ...]:
        """The negotiated capabilities of one server, as provenance-carrying
        descriptors (catalog entry form)."""
        snap = self._snapshots.get(server_id)
        if snap is None:
            return ()
        return tuple(
            MCPCapabilityDescriptor(
                capability=cap, server_id=server_id,
                protocol_version=snap.protocol_version, negotiated_at=snap.negotiated_at,
            )
            for cap in snap.capabilities
        )
