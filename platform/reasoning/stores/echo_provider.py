"""EchoProvider (M5.5B-1) — the deterministic, offline default `ModelProvider`.

Purpose: replay, tests, offline execution. It **never** calls an API, an SDK, or the network
— it derives a deterministic response *from the request itself* (the last user message,
echoed as a short acknowledgement) and streams it as word-delta `ResponseChunk`s. Given the
same request it always produces the same chunks, so orchestration is byte-for-byte
reproducible without a real model.
"""

from __future__ import annotations

from collections.abc import Iterator

from ..domain import ChunkKind, InferenceRequest, ResponseChunk
from ..ids import chunk_id_for


class EchoProvider:
    name = "echo"

    def __init__(self, *, prefix: str = "echo:") -> None:
        self._prefix = prefix

    def stream(self, request: InferenceRequest) -> Iterator[ResponseChunk]:
        text = self._response_text(request)
        words = text.split()
        rhash = request.request_hash or "req"
        if not words:
            yield ResponseChunk(
                id=chunk_id_for(rhash, 0, ""), index=0, delta="", kind=ChunkKind.FINAL, done=True
            )
            return
        for i, word in enumerate(words):
            delta = word if i == 0 else f" {word}"
            last = i == len(words) - 1
            yield ResponseChunk(
                id=chunk_id_for(rhash, i, delta),
                index=i,
                delta=delta,
                kind=ChunkKind.FINAL if last else ChunkKind.TOKEN,
                done=last,
            )

    def _response_text(self, request: InferenceRequest) -> str:
        """Deterministic echo: acknowledge the last user message (or the last message)."""
        user = [m for m in request.messages if m.role == "user"]
        source = (user[-1] if user else request.messages[-1]) if request.messages else None
        body = source.content.strip() if source is not None else ""
        return f"{self._prefix} {body}".strip()
