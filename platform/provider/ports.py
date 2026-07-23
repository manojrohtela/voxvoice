"""Provider runtime-internal seams (M5.6B-1) — the provider-neutral adapter port.

`AdapterPort` is the seam where a *vendor* plugs in: given a neutral `ProviderRequest`, it
streams `ProviderChunk`s. **Provider Runtime never knows an SDK, HTTP client, or transport** —
a vendor adapter (Anthropic/OpenAI/Gemini/local) implements this outside this package's core
(a Non-goal here). A deterministic `EchoProviderAdapter` (in `stores/`) is the dev/test
default and validates offline execution.

The core seam is a **synchronous** streaming generator (the deterministic local
`EchoProviderAdapter` never suspends, so the runtime + `run_coro_sync` drive it
deterministically). Async network-backed streaming adapters land behind the `AsyncAdapter`
extension hook (M5.6B-2).
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Iterator

from .domain import ProviderCapabilities, ProviderChunk, ProviderRequest


class AdapterPort(ABC):
    """Streaming execution seam. Deterministic for a given (request, adapter). No network."""

    name: str
    capabilities: ProviderCapabilities

    @abstractmethod
    def stream(self, request: ProviderRequest) -> Iterator[ProviderChunk]: ...
