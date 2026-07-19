"""Event ingestion (M5.1B-2) — content events → memory intents.

Deterministic, provider-neutral mapping from the M5.0 content-event vocabulary to
*intents* (what memory to form). The `MemoryIngestor` only decides; the runtime derives
scope from the event envelope and executes the writes. No AI, no reasoning.

Mapping:
- `INPUT_PROCESSED` / `TRANSCRIPT_COMPLETED` → **Conversation** memory (utterance)
- `REASONING_COMPLETED`                      → **Semantic** memory (fact)
- `TOOL_COMPLETED`                           → **Episodic** memory (tool result)
- `CONTEXT_ASSEMBLED`                         → **access** feedback (bump used memories)
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from core.events import Event, EventType

from .domain import MemoryTier, MemoryType


@dataclass(frozen=True, slots=True)
class StoreIntent:
    content: str
    tier: MemoryTier
    type: MemoryType
    importance: float
    origin: str
    tags: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class AccessIntent:
    """Memories referenced by an assembled context — bump their access/importance."""

    memory_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class IngestionResult:
    stores: tuple[StoreIntent, ...] = ()
    accesses: tuple[AccessIntent, ...] = ()


# (tier, type, origin, importance, content-field-candidates)
_MAP: dict[EventType, tuple[MemoryTier, MemoryType, str, float, tuple[str, ...]]] = {
    EventType.INPUT_PROCESSED: (
        MemoryTier.CONVERSATION, MemoryType.UTTERANCE, "input", 0.4, ("text", "content"),
    ),
    EventType.TRANSCRIPT_COMPLETED: (
        MemoryTier.CONVERSATION, MemoryType.UTTERANCE, "transcript", 0.5, ("text", "transcript"),
    ),
    EventType.REASONING_COMPLETED: (
        MemoryTier.SEMANTIC, MemoryType.FACT, "reasoning", 0.6, ("summary", "text", "output"),
    ),
    EventType.TOOL_COMPLETED: (
        MemoryTier.EPISODIC, MemoryType.TOOL_RESULT, "tool", 0.6, ("result", "output", "text"),
    ),
}


class MemoryIngestor:
    """Pure mapping from an observed content event to ingestion intents."""

    #: the content events this ingestor reacts to (the runtime's subscription filter)
    INGEST_TYPES: frozenset[EventType] = frozenset(_MAP) | {EventType.CONTEXT_ASSEMBLED}

    def plan(self, event: Event) -> IngestionResult:
        if event.type is EventType.CONTEXT_ASSEMBLED:
            ids = event.data.get("memory_ids") or ()
            ids = tuple(str(i) for i in ids)
            return IngestionResult(accesses=(AccessIntent(ids),)) if ids else IngestionResult()

        spec = _MAP.get(event.type)
        if spec is None:
            return IngestionResult()
        tier, mtype, origin, importance, fields = spec
        content = _first_str(event.data, fields)
        if not content:
            return IngestionResult()
        tags = _tags(event.data)
        return IngestionResult(
            stores=(StoreIntent(content, tier, mtype, importance, origin, tags),)
        )


def _first_str(data: dict, fields: Sequence[str]) -> str | None:
    for f in fields:
        v = data.get(f)
        if isinstance(v, str) and v.strip():
            return v
    return None


def _tags(data: dict) -> tuple[str, ...]:
    raw = data.get("tags")
    if isinstance(raw, (list, tuple)):
        return tuple(str(t) for t in raw)
    return ()
