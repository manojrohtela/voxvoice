"""provider.stores — local deterministic storage adapters + the EchoProviderAdapter (M5.6B-1,
development only). Real vendor adapters implement the `provider.ports.AdapterPort` seam; real
stores implement the `core.providers.stores` contracts — both additive."""

from .echo_adapter import EchoProviderAdapter
from .local_stores import InMemoryDocumentStore, InMemoryMetadataStore
from .provider_store import ProviderStore

__all__ = [
    "EchoProviderAdapter",
    "InMemoryDocumentStore",
    "InMemoryMetadataStore",
    "ProviderStore",
]
