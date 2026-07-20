"""reasoning.stores — local deterministic storage adapters + the EchoProvider (M5.5B-1,
development only). Real provider adapters implement the `reasoning.ports.ModelProvider` seam;
real stores implement the `core.providers.stores` contracts — both additive."""

from .echo_provider import EchoProvider
from .local_stores import InMemoryDocumentStore, InMemoryMetadataStore
from .reasoning_store import ReasoningStore

__all__ = [
    "EchoProvider",
    "InMemoryDocumentStore",
    "InMemoryMetadataStore",
    "ReasoningStore",
]
