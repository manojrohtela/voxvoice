"""Durable conversation memory adapter (M8.14).

Implements the M8.12 `MemoryAdapter` interface over the M8.14 message store
(`ConversationMessageRepository`) — a plain **message store + conversation retrieval**, durable
across turns. **No vector search, no embeddings, no RAG.** The authoritative append happens in the
Foundation `ConversationExecutionService` (one writer); this adapter provides multi-turn
**retrieval** (`history`) that the engine uses to build conversation context, plus the ABC methods.
"""

from __future__ import annotations

from typing import Any

from api.foundation import ConversationMessageRepository, MemoryAdapter


class DurableMemoryAdapter(MemoryAdapter):
    """Reads the durable message store for conversation retrieval. `history(org, session)` is the
    real retrieval used by the engine; the ABC `append`/`recall` are thin (the execution service
    is the single writer, so `append` is a no-op to avoid double-writes)."""

    def __init__(self, messages: ConversationMessageRepository) -> None:
        self._messages = messages

    # ── conversation retrieval (used by the engine) ──────────────────────────────
    def history(self, org_id: str, session_id: str) -> list[dict[str, Any]]:
        return [
            {"role": m.role, "content": m.content}
            for m in self._messages.list(org_id, session_id)
        ]

    # ── MemoryAdapter ABC (single-writer: the execution service persists) ─────────
    def append(self, session_id: str, turn: dict[str, Any]) -> None:
        return None

    def recall(self, session_id: str, *, limit: int = 0) -> list[dict[str, Any]]:
        return []
