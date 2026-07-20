"""Reasoning Runtime extension points (M5.5B-2) — interfaces only.

The seams where *capabilities the Reasoning Runtime must never author itself* plug in, and the
seams downstream runtimes consume the finished response through. The runtime/engines call
these ports **only if one is injected**; by default none is, and behaviour stays deterministic,
provider-neutral, and offline. Implementations (real provider adapters, an Agent/Workflow
runtime, MCP/A2A bridges, response/reasoning optimizers, multi-modal responses) land **outside**
this package — Reasoning never calls a model, SDK, or network directly.

No implementations here (non-goal). Neutral DTOs only. `ModelProvider` is the core inference
seam from `ports.py` (re-exported so the full extension surface lives in one place); the rest
are new. These make **Provider-Adapter / Agent / Workflow / MCP / A2A / Response-Optimizer /
Reasoning-Optimizer / Multi-modal** land as implementations **without changing the engine,
assembler, controllers, or repository**.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import AsyncIterator, Sequence

from .domain import InferenceRequest, ResponseChunk, ResponsePackage
from .ports import ModelProvider  # re-exported core seam

__all__ = [
    "ModelProvider",
    "AsyncModelProvider",
    "ReasoningConsumer",
    "ResponseOptimizer",
    "ReasoningOptimizer",
    "MultiModalResponder",
    "ToolProvider",
    "AgentBridge",
    "WorkflowBridge",
]


# ── future provider adapters (network-backed streaming) ───────────────────────
class AsyncModelProvider(ABC):
    """The async variant of `ModelProvider` for network-backed streaming adapters (behind the
    `StreamingController`). Future; the core seam stays the synchronous `ModelProvider`."""

    name: str

    @abstractmethod
    def astream(self, request: InferenceRequest) -> AsyncIterator[ResponseChunk]: ...


# ── downstream delivery seam (Agent / Workflow / MCP / A2A) ───────────────────
class ReasoningConsumer(ABC):
    """The delivery seam for the future Agent/Workflow runtimes: they consume the finished
    `ResponsePackage` (text + usage + lineage) — they never re-run inference."""

    @abstractmethod
    def consume(self, package: ResponsePackage) -> None: ...


class AgentBridge(ABC):
    """Future Agent Runtime seam: turn a response (with tool decisions) into the next agent
    step. Never a Reasoning dependency."""

    @abstractmethod
    def step(self, package: ResponsePackage) -> None: ...


class WorkflowBridge(ABC):
    """Future Workflow Runtime seam: route a completed response into a workflow node."""

    @abstractmethod
    def advance(self, package: ResponsePackage) -> None: ...


class ToolProvider(ABC):
    """Future MCP/A2A tool-source seam: supply tool specs the `ToolDecisionCoordinator` may
    coordinate over the injected `ToolPort`. Reasoning still never executes a tool."""

    @abstractmethod
    def tools(self) -> Sequence[str]: ...


# ── optimization hooks (AI/heuristic; never authored in-runtime) ──────────────
class ResponseOptimizer(ABC):
    """Post-process a finished response (e.g. rerank/rewrite) — deterministic per impl. Off by
    default; applied around the deterministic core."""

    @abstractmethod
    def optimize(self, package: ResponsePackage) -> ResponsePackage: ...


class ReasoningOptimizer(ABC):
    """Adjust an inference request before the loop (e.g. provider/model selection heuristics)."""

    @abstractmethod
    def optimize(self, request: InferenceRequest) -> InferenceRequest: ...


# ── multi-modal (future) ──────────────────────────────────────────────────────
class MultiModalResponder(ABC):
    """Emit non-text response parts (image/audio) as chunks. Future; text-only now. Neutral
    data only — no SDK, no network."""

    @abstractmethod
    def parts(self, package: ResponsePackage) -> Sequence[ResponseChunk]: ...
