"""ResponseNormalizer (M5.6B-1) — deterministic chunk → ProviderResponse assembly.

Collects the streamed `ProviderChunk`s into the immutable `ProviderResponse`: joins the
deltas into the final `text`, computes the deterministic `response_hash` + streaming
metadata (via `UnifiedStreamingAssembler`) + usage (via `UsageNormalizer`) + normalized
metadata (via `MetadataNormalizer`), and sets the finish reason. **No provider payload ever
escapes this normalizer** (§11 of the M5.6A design) — pure + deterministic, no vendor
specifics, no networking, no orchestration (the runtime drives the call; this only
assembles). A non-streaming adapter's single payload normalizes identically to a collapsed
single-chunk stream.
"""

from __future__ import annotations

from collections.abc import Sequence

from .domain import (
    ChunkKind,
    FinishReason,
    ProviderChunk,
    ProviderRequest,
    ProviderRequestState,
    ProviderResponse,
    ProviderScope,
)
from .ids import chunk_id_for, response_hash_for, response_id_for
from .metadata import MetadataNormalizer
from .streaming import UnifiedStreamingAssembler
from .usage import UsageNormalizer


class ResponseNormalizer:
    def __init__(
        self,
        *,
        streaming: UnifiedStreamingAssembler | None = None,
        usage: UsageNormalizer | None = None,
        metadata: MetadataNormalizer | None = None,
    ) -> None:
        self._streaming = streaming or UnifiedStreamingAssembler()
        self._usage = usage or UsageNormalizer()
        self._metadata = metadata or MetadataNormalizer()

    def normalize(
        self,
        request: ProviderRequest,
        chunks: Sequence[ProviderChunk],
        *,
        session_id: str,
        scope: ProviderScope,
        now: float,
        seq: int,
        streamed: bool,
        expires_at: float | None,
        request_id: str,
        finish_reason: FinishReason = FinishReason.STOP,
    ) -> ProviderResponse:
        text = "".join(c.delta for c in chunks)
        response_hash = response_hash_for(request.request_hash, tuple(c.delta for c in chunks))
        response_id = response_id_for(session_id, response_hash, seq)

        streaming_meta = self._streaming.metadata_for(tuple(chunks), streamed=streamed)
        usage = self._usage.normalize(
            prompt_text=" ".join(m.content for m in request.messages), completion_text=text,
        )
        metadata = self._metadata.normalize(
            provider=request.provider, model=request.model,
            request_id=request_id, response_id=response_id, model_version="",
            region=scope.region, organization=scope.org_id,
        )
        return ProviderResponse(
            id=response_id,
            session_id=session_id,
            scope=scope,
            state=ProviderRequestState.REQUESTED,  # the runtime drives the lifecycle from here
            text=text,
            chunks=tuple(chunks),
            usage=usage,
            finish_reason=finish_reason,
            streaming=streaming_meta,
            timing={"created_at": now},
            metadata=metadata,
            provider=request.provider,
            model=request.model,
            request_hash=request.request_hash,
            response_hash=response_hash,
            created_at=now,
            expires_at=expires_at,
            org_id=scope.org_id,
            conversation_id=scope.conversation_id,
        )

    def collapse(self, request: ProviderRequest, chunks: list[ProviderChunk]) -> ProviderChunk:
        """Merge a chunk stream into one final chunk (single-shot / non-streaming mode)."""
        text = "".join(c.delta for c in chunks)
        return ProviderChunk(
            id=chunk_id_for(request.request_hash or "req", 0, text),
            index=0, delta=text, kind=ChunkKind.FINAL, done=True,
        )
