"""Provider Runtime extension points (M5.6B-1 core + M5.6B-2 completion) — interfaces only.

The seams where future adapter kinds plug in. The runtime calls these **only if one is
injected**; by default none is, and behaviour stays deterministic and offline. No
implementations here (non-goal) — neutral interfaces only. Real vendor SDKs, batching,
locality, and enterprise-networking concerns are additive later work; none of this changes
`registry.py`, `catalog.py`, `response.py`, `router.py`, or the repository.

M5.6B-1 shipped: Async / Batch / Streaming / Local / Enterprise adapters. M5.6B-2 completes
the full M5.6A extension surface: GPU / Edge / Consensus / Ensemble / Parallel / Speculative /
Multimodal providers — the same way (interfaces only, no runtime dependency).
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
    "GPUAdapter",
    "EdgeAdapter",
    "ConsensusProvider",
    "EnsembleProvider",
    "ParallelProvider",
    "SpeculativeProvider",
    "MultimodalProvider",
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


# ── M5.6B-2: the remaining M5.6A extension surface ────────────────────────────
class GPUAdapter(ABC):
    """A `LocalAdapter` refinement declaring GPU residency (routing/health hint only)."""

    name: str
    gpu_required: bool

    @abstractmethod
    def stream(self, request: ProviderRequest) -> Iterator[ProviderChunk]: ...


class EdgeAdapter(ABC):
    """Edge/CDN-local inference — declares a region/point-of-presence for routing."""

    name: str
    edge_region: str

    @abstractmethod
    def stream(self, request: ProviderRequest) -> Iterator[ProviderChunk]: ...


class ConsensusProvider(ABC):
    """Fan out a request to N adapters and resolve the response by agreement. Interface
    only — the resolution policy is future work; Provider Runtime never executes this itself."""

    @abstractmethod
    def resolve(self, responses: Sequence[ProviderResponse]) -> ProviderResponse: ...


class EnsembleProvider(ABC):
    """Fan out to N adapters and combine their outputs. Interface only."""

    @abstractmethod
    def combine(self, responses: Sequence[ProviderResponse]) -> ProviderResponse: ...


class ParallelProvider(ABC):
    """Concurrent multi-candidate execution, first-good-wins. Interface only — no concurrency
    primitive shipped; a future orchestrator implements the race."""

    @abstractmethod
    def select_first(self, responses: Sequence[ProviderResponse]) -> ProviderResponse: ...


class SpeculativeProvider(ABC):
    """A fast draft adapter + a verifying adapter pairing. Interface only."""

    @abstractmethod
    def verify(self, draft: ProviderResponse, verified: ProviderResponse) -> ProviderResponse: ...


class MultimodalProvider(ABC):
    """Non-text capability surface (image/audio in+out) on the same `ProviderChunk`/
    `ProviderResponse` shapes with a `modality` discriminator. Interface only; text-only now."""

    name: str

    @abstractmethod
    def stream(self, request: ProviderRequest, *, modality: str) -> Iterator[ProviderChunk]: ...
