"""ContextStore (M5.3B-1) — the store bundle for temporary context storage.

A frozen bundle of the store contracts the `ContextRepository` binds to (a `DocumentStore`
for package records + a `MetadataStore` for session/window cursors) plus the `Tokenizer`
port. Provider-neutral: the factory names the concrete adapters; everything above binds the
contracts. Storage is **temporary only** — long-term persistence is the Memory Runtime's.
"""

from __future__ import annotations

from dataclasses import dataclass

from core.providers.stores import DocumentStore, MetadataStore

from ..ports import Tokenizer


@dataclass(frozen=True, slots=True)
class ContextStore:
    document_store: DocumentStore
    metadata_store: MetadataStore
    tokenizer: Tokenizer
