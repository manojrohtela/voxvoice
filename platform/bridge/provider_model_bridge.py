"""ProviderModelBridge (M5.6B-2) — the composition-root seam wiring Provider Runtime into
the closed Reasoning Runtime.

Implements Reasoning's **already-frozen** `reasoning.ports.ModelProvider` interface by
delegating to an injected `provider.ProviderRuntime`. This is deliberately a **separate,
third top-level package** (`bridge/`) — not part of either `provider/` or `reasoning/` — so
neither sibling runtime ever imports the other; only this composition-root module imports
both. Per the M5.6A design's dependency diagram: `[composition root] → reasoning, provider`.

**Reasoning Runtime remains completely untouched.** This bridge changes nothing about
`reasoning/`'s engine, ports, controllers, or lineage — it is simply a new implementation of
an interface that already existed, exactly as `EchoProvider` (Reasoning's own deterministic
default) already is. `ProviderChunk` mirrors `ResponseChunk` 1:1 (same `ChunkKind` string
values), so translation is a structural pass-through, not a remapping.
"""

from __future__ import annotations

from collections.abc import Iterator

from provider.domain import GenerationParameters as ProviderGenerationParameters
from provider.domain import ProviderMessage, ProviderRequest, ProviderScope
from provider.runtime import ProviderRuntime
from reasoning.domain import ChunkKind, InferenceRequest, ResponseChunk
from reasoning.ports import ModelProvider


class ProviderModelBridge(ModelProvider):
    """A `ModelProvider` backed by the Provider Runtime. Synchronous, mirroring the
    `ModelProvider` seam's shape (the deterministic local adapters never suspend)."""

    def __init__(self, runtime: ProviderRuntime, *, name: str = "provider-runtime") -> None:
        self.name = name
        self._runtime = runtime

    def stream(self, request: InferenceRequest) -> Iterator[ResponseChunk]:
        provider_request = self._to_provider_request(request)
        response = self._runtime.execute(provider_request)
        for chunk in response.chunks:
            yield ResponseChunk(
                id=chunk.id, index=chunk.index, delta=chunk.delta,
                kind=ChunkKind(chunk.kind.value), done=chunk.done,
                metadata=dict(chunk.metadata),
            )

    def _to_provider_request(self, request: InferenceRequest) -> ProviderRequest:
        """Note: `request.provider` selects a `ModelProvider` in **Reasoning's own**
        registry (this bridge is one such entry — typically named "provider-runtime") and is
        a different namespace from the **inner** `provider.ProviderRegistry`'s adapter names.
        It is therefore never forwarded; the inner registry auto-selects by capability (or the
        caller pins an inner adapter via `request.model`, forwarded as-is)."""
        gen = request.generation
        return ProviderRequest(
            session_id=request.session_id,
            messages=tuple(
                ProviderMessage(role=m.role, content=m.content, metadata=dict(m.metadata))
                for m in request.messages
            ),
            capability="chat",
            model=request.model,
            generation=ProviderGenerationParameters(
                temperature=gen.temperature, max_tokens=gen.max_tokens, top_p=gen.top_p,
                stop=gen.stop, seed=gen.seed, extra=dict(gen.extra),
            ),
            streaming=request.streaming,
            timeout_s=request.timeout_s,
            version=request.version,
            scope=ProviderScope(
                org_id=request.scope.org_id, conversation_id=request.scope.conversation_id,
                session_id=request.scope.session_id,
            ),
        )
