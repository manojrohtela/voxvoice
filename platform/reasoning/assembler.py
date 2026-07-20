"""Response assembler (M5.5B-1) — deterministic chunk → ResponsePackage assembly.

Collects the streamed `ResponseChunk`s into the immutable `ResponsePackage`: joins the
deltas into the final `text`, computes the deterministic `response_hash` + streaming metadata
+ token usage (via a supplied counter) + full `ReasoningLineage`, and sets the finish reason.
Pure + deterministic — no provider specifics, no networking, no orchestration (the engine
drives the stream; the assembler only assembles).
"""

from __future__ import annotations

from collections.abc import Callable, Sequence

from .domain import (
    FinishReason,
    InferenceRequest,
    InferenceState,
    ReasoningLineage,
    ReasoningScope,
    ResponseChunk,
    ResponseMetadata,
    ResponsePackage,
    StreamingMetadata,
    TokenUsage,
)
from .ids import package_id_for, response_hash_for


class ResponseAssembler:
    def __init__(self, *, token_counter: Callable[[str], int] | None = None) -> None:
        # deterministic default: whitespace word count (no vendor tokenizer in the core)
        self._count = token_counter or (lambda s: len(s.split()))

    def assemble(
        self,
        request: InferenceRequest,
        chunks: Sequence[ResponseChunk],
        *,
        session_id: str,
        scope: ReasoningScope,
        now: float,
        seq: int,
        streamed: bool,
        expires_at: float | None,
        finish_reason: FinishReason = FinishReason.STOP,
    ) -> ResponsePackage:
        text = "".join(c.delta for c in chunks)
        response_hash = response_hash_for(request.request_hash, tuple(c.delta for c in chunks))
        package_id = package_id_for(session_id, response_hash, seq)

        streaming = StreamingMetadata(
            streamed=streamed,
            chunk_count=len(chunks),
            first_chunk_index=chunks[0].index if chunks else 0,
        )
        usage = TokenUsage(
            prompt_tokens=sum(self._count(m.content) for m in request.messages),
            completion_tokens=self._count(text),
        )
        lineage = ReasoningLineage(
            prompt_hash=request.prompt_hash,
            prompt_fingerprint=request.prompt_fingerprint,
            inference_request_hash=request.request_hash,
            provider=request.provider,
            model=request.model,
            streaming=streaming,
            response_hash=response_hash,
        )
        return ResponsePackage(
            id=package_id,
            session_id=session_id,
            scope=scope,
            state=InferenceState.REQUESTED,  # the runtime drives the lifecycle from here
            text=text,
            chunks=tuple(chunks),
            usage=usage,
            finish_reason=finish_reason,
            streaming=streaming,
            timing={"created_at": now},
            metadata=ResponseMetadata(),
            lineage=lineage,
            provider=request.provider,
            model=request.model,
            request_hash=request.request_hash,
            response_hash=response_hash,
            created_at=now,
            expires_at=expires_at,
            org_id=scope.org_id,
            conversation_id=scope.conversation_id,
        )
