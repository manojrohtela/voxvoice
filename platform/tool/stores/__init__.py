"""tool.stores — local deterministic storage adapters + the EchoToolAdapter
(M6.1B-1, development only). Real tool adapters implement the `tool.ports.ToolAdapter`
seam; real stores implement the `core.providers.stores` contracts — both additive."""

from .echo_adapter import EchoToolAdapter
from .local_stores import InMemoryDocumentStore

__all__ = ["EchoToolAdapter", "InMemoryDocumentStore"]
