"""Context Runtime extension points (M5.3B-2) — interfaces only.

The seams where *intelligence the Context Runtime must never author itself* plugs in, and
the seams downstream runtimes consume assembled context through. The runtime/engines call
these ports **only if one is injected**; by default none is, and behaviour stays
deterministic, AI-free, and provider-neutral. Implementations (a real tokenizer, an LLM
summarizer, a PII redactor, a Prompt/Reasoning/Agent consumer) land outside this package —
the Context Runtime never calls a model or a vendor SDK directly.

No implementations here (non-goal). Neutral DTOs only. `Tokenizer` + `ContextSource` are
defined in `ports.py` (the core seams) and re-exported here so the full extension surface
lives in one place; the rest are new.

These interfaces make **Prompt / Reasoning / Tool / Workflow / Agent / MCP / A2A / Streaming
Context** land as `ContextSource`/`ContextConsumer`/`StreamingContextSink` implementations
**without changing the assembler, engines, or repository**.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Sequence

from .domain import ContextItem, ContextPackage
from .ports import ContextSource, Tokenizer  # re-exported core seams

__all__ = [
    "Tokenizer",
    "ContextSource",
    "ModelProfile",
    "Summarizer",
    "Compressor",
    "Redactor",
    "SemanticDeduplicator",
    "Reranker",
    "ContextConsumer",
    "StreamingContextSink",
]


# ── budgeting / tokenization ──────────────────────────────────────────────────
class ModelProfile(ABC):
    """Model-aware budgeting: the target model's context window + a token counter. Lets the
    BudgetManager size to an exact model without the runtime knowing any vendor."""

    name: str

    @abstractmethod
    def context_window(self) -> int: ...

    @abstractmethod
    def count(self, text: str) -> int: ...


# ── compression / privacy (AI, never authored in-runtime) ─────────────────────
class Summarizer(ABC):
    """Fold many items into one synthesized summary. Typically LLM-backed — hence a port."""

    @abstractmethod
    def summarize(self, items: Sequence[ContextItem]) -> str: ...


class Compressor(ABC):
    """Shrink one item's text (lossy or lossless) for context packing."""

    @abstractmethod
    def compress(self, text: str, *, target_tokens: int) -> str: ...


class Redactor(ABC):
    """Remove/mask sensitive spans from an item's text before it leaves the runtime."""

    @abstractmethod
    def redact(self, text: str) -> str: ...


# ── retrieval-quality extensions ──────────────────────────────────────────────
class SemanticDeduplicator(ABC):
    """Near-duplicate detection beyond exact content-hash. Returns groups of item ids that
    are semantically equivalent; the DeduplicationEngine merges each group deterministically."""

    @abstractmethod
    def duplicate_groups(self, items: Sequence[ContextItem]) -> Sequence[Sequence[str]]: ...


class Reranker(ABC):
    """Re-order ranked items with a stronger (possibly learned) signal, applied *after*
    deterministic ranking; must itself be deterministic for a given model."""

    @abstractmethod
    def rerank(self, query: str | None, items: Sequence[ContextItem]) -> Sequence[ContextItem]: ...


# ── downstream delivery seams (Prompt / Reasoning / Agent / MCP / A2A) ────────
class ContextConsumer(ABC):
    """The delivery seam for the future Prompt/Reasoning/Agent runtimes: they consume the
    finished `ContextPackage` and never call back into Memory or Knowledge."""

    @abstractmethod
    def consume(self, package: ContextPackage) -> None: ...


class StreamingContextSink(ABC):
    """Future incremental/streaming delivery: receive items as they are finalized."""

    @abstractmethod
    def on_item(self, item: ContextItem) -> None: ...

    @abstractmethod
    def on_complete(self, package: ContextPackage) -> None: ...
