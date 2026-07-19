"""Knowledge ingestion ports (M5.2B-1) — the vendor/format seams.

`Parser` (format → text) and `ChunkStrategy` (text → chunk specs) are the two
provider-neutral seams the document pipeline calls. The runtime never parses with a
vendor library or hard-codes a chunking algorithm inline — it calls these ports.
Deterministic local defaults ship in `pipeline/` (a plain-text parser + a sliding-window
chunker); richer implementations (PDF/HTML parsers, semantic/hierarchical chunkers) are
additive and land as adapters. Reranker/Summarizer/etc. are retrieval-intelligence
extension points and are **not** defined in this core milestone.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Sequence
from dataclasses import dataclass


class Parser(ABC):
    """Raw bytes (+ mime) → normalized-ready text. Deterministic for a given input."""

    name: str

    @abstractmethod
    def parse(self, raw: bytes, *, mime: str) -> str: ...


@dataclass(frozen=True, slots=True)
class ChunkSpec:
    """A strategy's output: the content + its offsets in the source text. The pipeline
    turns these into `Chunk`s (assigning ids/relationships)."""

    content: str
    start: int
    end: int
    token_count: int
    parent_index: int | None = None


class ChunkStrategy(ABC):
    """Text → ordered chunk specs. Deterministic; the pipeline owns identity/versioning."""

    name: str

    @abstractmethod
    def chunk(self, text: str) -> Sequence[ChunkSpec]: ...
