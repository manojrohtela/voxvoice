"""Context runtime-internal seams (M5.3B-1) — the provider-neutral ports the assembler
calls. Vendor/format concerns live behind these; the runtime never hard-codes a tokenizer
or couples to a source implementation.

- `Tokenizer` — text → token estimate. A deterministic local default ships in `stores/`;
  a model-exact tokenizer (tiktoken/HF) is a future additive adapter (model-aware budgeting
  is M5.3B-2, but the seam is here so the estimate is provider-neutral now).
- `ContextSource` — the **universal input adapter**: Memory/Knowledge/Prompt wrap into this
  in `sources.py`, and future Tool/Workflow/Agent/Federated results land as new
  `ContextSource`s with no assembler change. Sync (the query ports are sync).

`AssemblyRequest` + `SourceContribution` are the neutral DTOs crossing these seams.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Sequence
from dataclasses import dataclass, field

from .domain import ContextKind, ContextScope, SourceRuntime


@dataclass(frozen=True, slots=True)
class AssemblyRequest:
    """The resolved, scoped request a `ContextSource` answers. Built by the runtime from
    the frozen `core.ports.ContextRequest` + the resolved scope."""

    session_id: str
    scope: ContextScope
    query: str | None = None
    budget_tokens: int = 0
    include_memory: bool = True
    include_knowledge: bool = True
    limit: int = 10  # per-source hit cap


@dataclass(frozen=True, slots=True)
class SourceContribution:
    """A source's raw offering — normalized into a `ContextItem` by the `ContextBuilder`
    (which assigns the deterministic id + provenance). Confidence defaults to the score."""

    kind: ContextKind
    text: str
    source_runtime: SourceRuntime
    source_id: str
    score: float = 0.0
    confidence: float | None = None  # None → derive from score
    metadata: dict[str, str] = field(default_factory=dict)


class Tokenizer(ABC):
    """Text → token estimate. Deterministic for a given input."""

    name: str

    @abstractmethod
    def count(self, text: str) -> int: ...


class ContextSource(ABC):
    """A pluggable input to assembly. `fetch` returns candidate contributions for the
    scoped request. Deterministic for a given (request, upstream state)."""

    name: str

    @abstractmethod
    def fetch(self, request: AssemblyRequest) -> Sequence[SourceContribution]: ...
