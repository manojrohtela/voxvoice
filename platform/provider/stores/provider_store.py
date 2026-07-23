"""ProviderStore (M5.6B-1) — the store bundle for temporary provider storage.

A frozen bundle of the store contracts the `ProviderRepository` binds to (a `DocumentStore`
for response + session records + a `MetadataStore` for cursors). Provider-neutral: the
factory names the concrete adapters; everything above binds the contracts. Storage is
**temporary only** — durable persistence is the Memory Runtime's, never this runtime's.
"""

from __future__ import annotations

from dataclasses import dataclass

from core.providers.stores import DocumentStore, MetadataStore


@dataclass(frozen=True, slots=True)
class ProviderStore:
    document_store: DocumentStore
    metadata_store: MetadataStore
