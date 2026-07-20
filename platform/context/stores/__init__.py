"""context.stores — local deterministic storage adapters + default tokenizer (M5.3B-1,
development only). Real adapters (Postgres/Redis) implement the same `core.providers.stores`
contracts additively."""

from .context_store import ContextStore
from .local_stores import InMemoryDocumentStore, InMemoryMetadataStore
from .tokenizer import WordTokenizer

__all__ = [
    "ContextStore",
    "InMemoryDocumentStore",
    "InMemoryMetadataStore",
    "WordTokenizer",
]
