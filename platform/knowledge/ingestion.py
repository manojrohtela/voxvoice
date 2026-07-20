"""Knowledge event ingestion (M5.2B-2) — content events → knowledge intents.

Deterministic, provider-neutral mapping from the M5.0 content-event vocabulary to
*intents* (what document to register / replace / archive / delete). The
`KnowledgeIngestor` only decides; the runtime derives scope from the event envelope and
executes the writes through the pipeline. No AI, no reasoning.

**Self-loop prevention is structural**: `INGEST_TYPES` never includes any `knowledge.*`
event, so the runtime is subscribed only to *other* producers' events and can never
re-ingest what it itself emitted. The runtime additionally guards on `EventSource`.

Mapping:
- `KNOWLEDGE_SOURCE_UPDATED` is **NOT** ingested (self-loop) — knowledge is the producer.
- `REASONING_COMPLETED` (generated doc)  → **register** a GENERATED document.
- `TOOL_COMPLETED` (retrieved/produced)  → **register** an IMPORTED document (if flagged).
- `TRANSCRIPT_COMPLETED`                  → ignored here (that is Memory's job, not ours).
Registration, replacement, version-update, archive and deletion intents are all expressed;
which fire depends on the payload (`knowledge_action`).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum

from core.events import Event, EventSource, EventType

from .domain import ImportOrigin, KnowledgeType


class IngestAction(StrEnum):
    REGISTER = "register"  # import/replace/version — the pipeline decides via content hash
    ARCHIVE = "archive"
    DELETE = "delete"


@dataclass(frozen=True, slots=True)
class RegisterIntent:
    uri: str
    content: str
    knowledge_type: KnowledgeType
    origin: ImportOrigin
    mime: str = "text/plain"
    title: str | None = None


@dataclass(frozen=True, slots=True)
class MaintainIntent:
    """Archive/delete an existing document by id."""

    action: IngestAction
    document_id: str


@dataclass(frozen=True, slots=True)
class IngestionResult:
    registers: tuple[RegisterIntent, ...] = ()
    maintenance: tuple[MaintainIntent, ...] = ()


# events (never knowledge.*) whose payloads may carry a document to register
_REGISTER_MAP: dict[EventType, tuple[KnowledgeType, ImportOrigin, str, tuple[str, ...]]] = {
    EventType.REASONING_COMPLETED: (
        KnowledgeType.GENERATED, ImportOrigin.GENERATED, "generated",
        ("document", "summary", "text", "output"),
    ),
    EventType.TOOL_COMPLETED: (
        KnowledgeType.IMPORTED, ImportOrigin.IMPORTED, "tool",
        ("document", "result", "output", "text"),
    ),
}


class KnowledgeIngestor:
    """Pure mapping from an observed content event to ingestion intents. Never reacts to
    a `knowledge.*` event (self-loop prevention is in the subscription itself)."""

    #: the content events this ingestor reacts to (the runtime's subscription filter).
    #: deliberately excludes every knowledge.* type.
    INGEST_TYPES: frozenset[EventType] = frozenset(_REGISTER_MAP)

    def plan(self, event: Event) -> IngestionResult:
        # hard self-loop guard: never ingest our own emissions
        if event.source is EventSource.KNOWLEDGE or event.type not in self.INGEST_TYPES:
            return IngestionResult()

        # explicit maintenance actions ride any admitted event's payload
        action = event.data.get("knowledge_action")
        doc_id = event.data.get("document_id")
        if action in (IngestAction.ARCHIVE.value, IngestAction.DELETE.value) and doc_id:
            return IngestionResult(
                maintenance=(MaintainIntent(IngestAction(action), str(doc_id)),)
            )

        spec = _REGISTER_MAP.get(event.type)
        if spec is None:
            return IngestionResult()
        kt, origin, source_tag, fields = spec
        # only register when the producer explicitly flags a knowledge document
        if not event.data.get("as_knowledge"):
            return IngestionResult()
        content = _first_str(event.data, fields)
        uri = event.data.get("uri") or f"event://{source_tag}/{event.id}"
        if not content:
            return IngestionResult()
        return IngestionResult(
            registers=(
                RegisterIntent(
                    uri=str(uri),
                    content=content,
                    knowledge_type=kt,
                    origin=origin,
                    title=event.data.get("title"),
                ),
            )
        )


def _first_str(data: dict, fields: Sequence[str]) -> str | None:
    for f in fields:
        v = data.get(f)
        if isinstance(v, str) and v.strip():
            return v
    return None
