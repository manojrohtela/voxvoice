"""Inference engine (M5.5B-1) — the deterministic model-call orchestrator.

Owns orchestration only: **provider selection → streaming session → response assembly**. It
selects the injected `ModelProvider` from the `ProviderRegistry`, pulls its `ResponseChunk`
stream (deterministic + local in the core), and hands the chunks to the `ResponseAssembler`.
No retries, no cancellation, no tool loop, no scheduling (all M5.5B-2). No provider SDK, no
networking — the model is the injected port.

Async-first (the sync `ReasoningPort` drives it via the bridge); the local `EchoProvider`
never suspends, so the loop is deterministic.
"""

from __future__ import annotations

from core.errors import ConfigError

from .assembler import ResponseAssembler
from .domain import (
    ChunkKind,
    FinishReason,
    InferenceRequest,
    ReasoningScope,
    ResponseChunk,
    ResponsePackage,
)
from .ids import chunk_id_for
from .ports import ModelProvider


class ProviderRegistry:
    """Maps a provider name → the injected `ModelProvider`. The only place providers are
    named; an unknown provider fails fast with `ConfigError`."""

    def __init__(self) -> None:
        self._providers: dict[str, ModelProvider] = {}

    def register(self, provider: ModelProvider) -> None:
        self._providers[provider.name] = provider

    def get(self, name: str) -> ModelProvider:
        provider = self._providers.get(name)
        if provider is None:
            raise ConfigError(f"unknown reasoning provider {name!r}")
        return provider

    def names(self) -> tuple[str, ...]:
        return tuple(sorted(self._providers))


class InferenceEngine:
    def __init__(self, *, registry: ProviderRegistry, assembler: ResponseAssembler) -> None:
        self._registry = registry
        self._assembler = assembler

    @property
    def providers(self) -> tuple[str, ...]:
        return self._registry.names()

    async def empty(
        self,
        request: InferenceRequest,
        *,
        session_id: str,
        scope: ReasoningScope,
        now: float,
        seq: int,
    ) -> ResponsePackage:
        """Assemble an empty package **without selecting a provider** — used by the
        CancellationManager for a token cancelled before the loop started (M5.5B-2)."""
        return self._assembler.assemble(
            request, [], session_id=session_id, scope=scope, now=now, seq=seq,
            streamed=False, expires_at=None, finish_reason=FinishReason.CANCELLED,
        )

    async def infer(
        self,
        request: InferenceRequest,
        *,
        session_id: str,
        scope: ReasoningScope,
        now: float,
        seq: int,
        expires_at: float | None,
    ) -> ResponsePackage:
        # 1. provider selection (deterministic; unknown → ConfigError)
        provider = self._registry.get(request.provider)
        # 2. streaming session — pull the provider's chunk stream (local, non-suspending)
        chunks = list(provider.stream(request))
        streamed = request.streaming
        if not streamed:  # single-shot → collapse to one final chunk
            chunks = [_collapse(request, chunks)]
        finish = FinishReason.STOP if chunks and chunks[-1].done else FinishReason.LENGTH
        # 3. response assembly (deterministic)
        return self._assembler.assemble(
            request, chunks, session_id=session_id, scope=scope, now=now, seq=seq,
            streamed=streamed, expires_at=expires_at, finish_reason=finish,
        )


def _collapse(request: InferenceRequest, chunks: list[ResponseChunk]) -> ResponseChunk:
    """Merge a chunk stream into one final chunk (single-shot / non-streaming mode)."""
    text = "".join(c.delta for c in chunks)
    return ResponseChunk(
        id=chunk_id_for(request.request_hash or "req", 0, text),
        index=0, delta=text, kind=ChunkKind.FINAL, done=True,
    )
