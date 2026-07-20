"""Reasoning runtime-internal seams (M5.5B-1) — the provider-neutral inference port.

`ModelProvider` is the seam where the *model* plugs in: given a neutral `InferenceRequest`,
it streams `ResponseChunk`s. **Reasoning never knows an SDK, HTTP client, or transport** — a
provider adapter (Anthropic/OpenAI/Gemini/local) implements this outside the package (a
Non-goal here). A deterministic `EchoProvider` (in `stores/`) is the dev/test default.

The core seam is a **synchronous** streaming generator (the deterministic local `EchoProvider`
never suspends, so the async engine + `run_coro_sync` drive it deterministically). Async
network-backed streaming providers land behind the `StreamingController` in M5.5B-2.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Iterator

from .domain import InferenceRequest, ResponseChunk


class ModelProvider(ABC):
    """Streaming inference seam. Deterministic for a given (request, model). No network."""

    name: str

    @abstractmethod
    def stream(self, request: InferenceRequest) -> Iterator[ResponseChunk]: ...
