"""Tool events (M6.1B-1) — phase constants.

The Tool Runtime is the sole producer of its events. Per the M6.1A design and the
Memory/Knowledge/Context/Prompt/Reasoning/Provider precedent, the core reuses the
**existing** M5.0 `tool.*` `EventType` members (no core/bus change):
`TOOL_REQUESTED`/`TOOL_COMPLETED`/`TOOL_FAILED` with a `phase` discriminator carries
the full execution lifecycle. Descriptor-lifecycle changes (register/activate/
deprecate/retire) are bookkeeping, not content events, and are not published in this
milestone (no `tool.registered`-style `EventType` exists yet — an additive
vocabulary extension, not a bus change, if ever needed).
"""

from __future__ import annotations

from enum import StrEnum


class ToolPhase(StrEnum):
    """The `phase` field carried on `tool.*` events."""

    RECEIVED = "received"
    VALIDATING = "validating"
    EXECUTING = "executing"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
