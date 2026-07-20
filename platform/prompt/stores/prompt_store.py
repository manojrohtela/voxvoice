"""PromptStore (M5.4B-1) — the store bundle for temporary prompt storage.

A frozen bundle of the store contracts the `PromptRepository` binds to (a `DocumentStore`
for package + template records + a `MetadataStore` for cursors) plus the injected
`Tokenizer`/`ProviderProfile`/`ModelProfile` ports. Provider-neutral: the factory names the
concrete adapters/profiles; everything above binds the contracts/ports. Storage is
**temporary only** — long-term persistence is the Memory Runtime's.
"""

from __future__ import annotations

from dataclasses import dataclass

from core.providers.stores import DocumentStore, MetadataStore

from ..ports import ModelProfile, ProviderProfile, Tokenizer


@dataclass(frozen=True, slots=True)
class PromptStore:
    document_store: DocumentStore
    metadata_store: MetadataStore
    tokenizer: Tokenizer
    provider_profile: ProviderProfile
    model_profile: ModelProfile
