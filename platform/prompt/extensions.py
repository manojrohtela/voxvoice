"""Prompt Runtime extension points (M5.4B-2) — interfaces only.

The seams where *intelligence the Prompt Runtime must never author itself* plugs in, and the
seams downstream runtimes consume the finished prompt through. The runtime/engines call
these ports **only if one is injected**; by default none is, and behaviour stays
deterministic, AI-free, and provider-neutral. Implementations (a prompt optimizer, a
multi-modal renderer, a Reasoning/Agent consumer) land outside this package — the Prompt
Runtime never calls a model or a vendor SDK directly.

No implementations here (non-goal). Neutral DTOs only. `Tokenizer`/`ProviderProfile`/
`ModelProfile` are defined in `ports.py` (the core seams) and re-exported here so the full
extension surface lives in one place; the rest are new.

These make **Reasoning / Agent / Tool / Workflow / MCP / A2A / Prompt-Optimizer /
Multi-modal** land as `PromptConsumer`/`PromptOptimizer`/`MultiModalRenderer` implementations
**without changing the builder, renderer, budget, templates, or repository**.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Sequence

from .domain import PromptMessage, PromptPackage, PromptSection
from .ports import ModelProfile, ProviderProfile, Tokenizer  # re-exported core seams

__all__ = [
    "Tokenizer",
    "ProviderProfile",
    "ModelProfile",
    "PromptOptimizer",
    "PromptRewriter",
    "PromptCompressor",
    "PromptEnhancer",
    "PromptAnalyzer",
    "MultiModalRenderer",
    "PromptConsumer",
]


# ── optimization hooks (AI/heuristic; never authored in-runtime) ──────────────
class PromptOptimizer(ABC):
    """Optimize a built section set (e.g. reorder/merge) before rendering. Applied around
    the deterministic core; must be deterministic for a given implementation. Off by default."""

    @abstractmethod
    def optimize(self, sections: Sequence[PromptSection]) -> Sequence[PromptSection]: ...


class PromptRewriter(ABC):
    """Rewrite a section's content (e.g. clarify, translate) — typically LLM-backed."""

    @abstractmethod
    def rewrite(self, section: PromptSection) -> str: ...


class PromptCompressor(ABC):
    """Shrink a section's content (lossy/lossless) to a token target."""

    @abstractmethod
    def compress(self, content: str, *, target_tokens: int) -> str: ...


class PromptEnhancer(ABC):
    """Add derived sections (e.g. few-shot exemplars) to a prompt."""

    @abstractmethod
    def enhance(self, sections: Sequence[PromptSection]) -> Sequence[PromptSection]: ...


class PromptAnalyzer(ABC):
    """Analyze a finished package (quality/complexity metrics) — read-only, no mutation."""

    @abstractmethod
    def analyze(self, package: PromptPackage) -> dict[str, float]: ...


# ── multi-modal (future) ──────────────────────────────────────────────────────
class MultiModalRenderer(ABC):
    """Render non-text prompt parts (image/audio) into provider messages. Future; text-only
    now. Neutral data only — no SDK, no network."""

    @abstractmethod
    def render_parts(self, sections: Sequence[PromptSection]) -> Sequence[PromptMessage]: ...


# ── downstream delivery seam (Reasoning / Agent / Tool / Workflow / MCP / A2A) ─
class PromptConsumer(ABC):
    """The delivery seam for the future Reasoning/Agent runtimes: they consume the finished
    `PromptPackage` (and call the model) — they never reconstruct a prompt or re-query
    Context/Memory/Knowledge."""

    @abstractmethod
    def consume(self, package: PromptPackage) -> None: ...
