"""Context events (M5.3B-1) — phase constants.

The Context Runtime is the sole producer of its events. Per the approved design (and the
Memory/Knowledge precedent), the core reuses the **existing** M5.0 `context.*`
`EventType` members (no core/bus change): lifecycle events map onto `CONTEXT_ASSEMBLED`
with a `phase` discriminator, and truncation/expiry onto `CONTEXT_TRUNCATED`. Granular
per-phase `EventType`s are a deferred **additive vocabulary extension** — the bus is never
modified.
"""

from __future__ import annotations

from enum import StrEnum


class ContextPhase(StrEnum):
    """The `phase` field carried on `context.*` lifecycle events."""

    REQUESTED = "requested"
    BUILT = "built"
    VALIDATED = "validated"
    COMPRESSED = "compressed"
    REJECTED = "rejected"
    DELIVERED = "delivered"
    EXPIRED = "expired"
