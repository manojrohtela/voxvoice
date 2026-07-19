"""Knowledge events (M5.2B-1) — vocabulary mapping + phase constants.

The Knowledge Runtime is the sole producer of its events. The core milestone reuses the
**existing** M5.0 `knowledge.*` `EventType` members (no core change): lifecycle events
map onto `KNOWLEDGE_SOURCE_UPDATED` with a `phase` discriminator, indexing onto
`KNOWLEDGE_INDEXED`, retrieval onto `KNOWLEDGE_RETRIEVED`. Granular per-lifecycle
`EventType`s (document.imported/deleted/version.created/…) are a deferred **additive
vocabulary extension** (see the design), added when needed — the bus is never modified.
"""

from __future__ import annotations

from enum import StrEnum


class KnowledgePhase(StrEnum):
    """The `phase` field carried on `knowledge.source.updated` lifecycle events."""

    IMPORTED = "imported"
    UPDATED = "updated"
    DEDUPED = "deduped"
    VERSION_CREATED = "version_created"
    ARCHIVED = "archived"
    DELETED = "deleted"
