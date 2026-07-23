"""Provider Runtime extension points (M5.6B-1) — interfaces only.

The seams where future adapter kinds plug in. The runtime calls these **only if one is
injected**; by default none is, and behaviour stays deterministic and offline. No
implementations here (non-goal) — neutral interfaces only. Real vendor SDKs, batching,
locality, and enterprise-networking concerns are additive later work; none of this changes
`registry.py`, `catalog.py`, `response.py`, or the repository.

Scope for M5.6B-1 (per the milestone prompt): Async / Batch / Streaming / Local / Enterprise
adapters. The remaining M5.6A-design extension surface (GPU/Edge/Consensus/Ensemble/
Parallel/Speculative/Multimodal providers) is deferred to M5.6B-2, added the same way.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import AsyncIterator, Iterator, Sequence

from .domain import ProviderChunk, ProviderRequest, ProviderResponse

__all__ = [
    "AsyncAdapter",
    "BatchAdapter",
    "StreamingAdapter",
    "LocalAdapter",
    "EnterpriseAdapter",
]


class AsyncAdapter(ABC):
    """The async-native `AdapterPort` variant for network-backed vendor adapters. Future; the
    core seam stays the synchronous `AdapterPort`."""

    name: str

    @abstractmethod
    def astream(self, request: ProviderRequest) -> AsyncIterator[ProviderChunk]: ...


class BatchAdapter(ABC):
    """Request/response batching (non-streaming bulk execution) for vendors that price or
    perform better in bulk."""

    name: str

    @abstractmethod
    def execute_batch(self, requests: Sequence[ProviderRequest]) -> Sequence[ProviderResponse]: ...


class StreamingAdapter(ABC):
    """A marker refinement of `AdapterPort` for streaming-first vendors (declares the
    streaming capability is first-class, not an emulation over polling)."""

    name: str

    @abstractmethod
    def stream(self, request: ProviderRequest) -> Iterator[ProviderChunk]: ...


class LocalAdapter(ABC):
    """In-process / on-device model execution (no network). A refinement of `AdapterPort`
    that declares locality — useful for routing/health decisions later (M5.6B-2)."""

    name: str

    @abstractmethod
    def stream(self, request: ProviderRequest) -> Iterator[ProviderChunk]: ...


class EnterpriseAdapter(ABC):
    """Private-endpoint / VPC / dedicated-capacity adapters. Same `AdapterPort` shape with an
    additional declared deployment context (interface only — no networking implementation)."""

    name: str
    endpoint: str

    @abstractmethod
    def stream(self, request: ProviderRequest) -> Iterator[ProviderChunk]: ...
