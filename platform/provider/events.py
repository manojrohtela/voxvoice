"""Provider events (M5.6B-1) — phase constants.

The Provider Runtime is the sole producer of its events. Per the approved M5.6A design (§19)
and the Memory/Knowledge/Context/Prompt/Reasoning precedent, the core reuses the **existing**
M5.0 `provider.*` / `model.*` `EventType` members (no core/bus change): lifecycle events map
onto `PROVIDER_CONNECTED`/`PROVIDER_DISCONNECTED` with a `phase` discriminator, streamed
chunks onto `MODEL_TOKEN`, and usage onto `MODEL_USAGE`. Granular per-phase `EventType`s are a
deferred **additive vocabulary extension** — the bus is never modified.
"""

from __future__ import annotations

from enum import StrEnum


class ProviderPhase(StrEnum):
    """The `phase` field carried on `provider.*` / `model.*` events."""

    REQUESTED = "requested"
    ROUTED = "routed"
    EXECUTING = "executing"
    STREAMING = "streaming"
    CHUNK = "chunk"
    COMPLETED = "completed"
    DELIVERED = "delivered"
    FAILED = "failed"
    EXPIRED = "expired"
    REGISTERED = "registered"
    DEREGISTERED = "deregistered"
