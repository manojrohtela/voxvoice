"""Memory Runtime extension points (M5.1B-2) — interfaces only.

The seams where *intelligence the Memory Runtime must never author itself* plugs in.
The runtime calls these ports **if one is injected**; by default none is, and behaviour
stays deterministic and AI-free. Implementations (a cross-encoder reranker, an
LLM summarizer, a PII redactor) land outside this package in later milestones — the
Memory Runtime never calls a model directly.

No implementations here (non-goal). Neutral DTOs only.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Sequence

from .domain import MemoryHit, MemoryRecord


class Reranker(ABC):
    """Re-order ranked hits with a stronger (possibly learned) signal. Applied *after*
    deterministic ranking; must itself be deterministic for a given model."""

    @abstractmethod
    def rerank(self, query_text: str, hits: Sequence[MemoryHit]) -> Sequence[MemoryHit]: ...


class Summarizer(ABC):
    """Fold many records into one summary record. Typically LLM-backed — hence a port,
    never called by the runtime's own logic."""

    @abstractmethod
    def summarize(self, records: Sequence[MemoryRecord]) -> str: ...


class Compressor(ABC):
    """Shrink a record's content (lossy or lossless) for cold storage."""

    @abstractmethod
    def compress(self, content: str) -> str: ...

    @abstractmethod
    def decompress(self, content: str) -> str: ...


class Redactor(ABC):
    """Remove/mask sensitive spans before a record is persisted (privacy policy)."""

    @abstractmethod
    def redact(self, content: str) -> str: ...
