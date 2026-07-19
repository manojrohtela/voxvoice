"""Document + Chunk lifecycles (M5.2B-1) — built on `core.fsm`.

Two deterministic state machines, one per aggregate, dogfooding the shared FSM
framework (no hand-written transition logic). Illegal moves raise typed `FSMError`s.

Document: IMPORTED → VALIDATED → PARSED → CHUNKED → EMBEDDED → INDEXED → ACTIVE
          → ARCHIVED → DELETED   (+ ACTIVE/ARCHIVED → DELETED)
Chunk:    CREATED → EMBEDDED → INDEXED → ARCHIVED → DELETED   (+ INDEXED/ARCHIVED → DELETED)

`advance_document`/`advance_chunk` validate one transition on an immutable record and
return it in its new state — records are serialized/immutable, so a fresh machine is
instantiated at the record's current state to check the move.
"""

from __future__ import annotations

from collections.abc import Callable
from enum import StrEnum

from core.fsm import StateMachine, StateMachineDefinition

from .domain import Chunk, ChunkState, Document, DocumentState


class DocumentTrigger(StrEnum):
    VALIDATE = "validate"
    PARSE = "parse"
    CHUNK = "chunk"
    EMBED = "embed"
    INDEX = "index"
    ACTIVATE = "activate"
    ARCHIVE = "archive"
    DELETE = "delete"


class ChunkTrigger(StrEnum):
    EMBED = "embed"
    INDEX = "index"
    ARCHIVE = "archive"
    DELETE = "delete"


def _document_def() -> StateMachineDefinition[DocumentState, DocumentTrigger]:
    d: StateMachineDefinition[DocumentState, DocumentTrigger] = StateMachineDefinition()
    d.add(DocumentState.IMPORTED, DocumentTrigger.VALIDATE, DocumentState.VALIDATED)
    d.add(DocumentState.VALIDATED, DocumentTrigger.PARSE, DocumentState.PARSED)
    d.add(DocumentState.PARSED, DocumentTrigger.CHUNK, DocumentState.CHUNKED)
    d.add(DocumentState.CHUNKED, DocumentTrigger.EMBED, DocumentState.EMBEDDED)
    d.add(DocumentState.EMBEDDED, DocumentTrigger.INDEX, DocumentState.INDEXED)
    d.add(DocumentState.INDEXED, DocumentTrigger.ACTIVATE, DocumentState.ACTIVE)
    d.add(DocumentState.ACTIVE, DocumentTrigger.ARCHIVE, DocumentState.ARCHIVED)
    d.add(DocumentState.ARCHIVED, DocumentTrigger.DELETE, DocumentState.DELETED)
    d.add(DocumentState.ACTIVE, DocumentTrigger.DELETE, DocumentState.DELETED)
    return d


def _chunk_def() -> StateMachineDefinition[ChunkState, ChunkTrigger]:
    d: StateMachineDefinition[ChunkState, ChunkTrigger] = StateMachineDefinition()
    d.add(ChunkState.CREATED, ChunkTrigger.EMBED, ChunkState.EMBEDDED)
    d.add(ChunkState.EMBEDDED, ChunkTrigger.INDEX, ChunkState.INDEXED)
    d.add(ChunkState.INDEXED, ChunkTrigger.ARCHIVE, ChunkState.ARCHIVED)
    d.add(ChunkState.ARCHIVED, ChunkTrigger.DELETE, ChunkState.DELETED)
    d.add(ChunkState.INDEXED, ChunkTrigger.DELETE, ChunkState.DELETED)
    return d


DOCUMENT_LIFECYCLE = _document_def()
CHUNK_LIFECYCLE = _chunk_def()


def advance_document(
    document: Document, trigger: DocumentTrigger, *, clock: Callable[[], float] | None = None
) -> Document:
    """Validate `trigger` against the document's state; return it in the new state."""
    fsm: StateMachine[DocumentState, DocumentTrigger] = StateMachine(
        DOCUMENT_LIFECYCLE, document.state, machine_id=document.id, clock=clock
    )
    return document.with_state(fsm.fire(trigger))


def advance_chunk(
    chunk: Chunk, trigger: ChunkTrigger, *, clock: Callable[[], float] | None = None
) -> Chunk:
    """Validate `trigger` against the chunk's state; return it in the new state."""
    fsm: StateMachine[ChunkState, ChunkTrigger] = StateMachine(
        CHUNK_LIFECYCLE, chunk.state, machine_id=chunk.id, clock=clock
    )
    return chunk.with_state(fsm.fire(trigger))
