"""Tool Runtime extension points (M6.1B-1) — interfaces only.

The seams future capability lands in, per the M6.1A design's explicit non-goals for
this milestone. The runtime calls these **only if one is injected**; by default
none is, and behaviour stays deterministic and offline. No implementations here —
neutral interfaces only.

Reserved for: Policy Engine + Approval Engine (M6.1B-2), Secrets Manager (M6.1B-2),
Streaming (M6.1B-4), Workflow integration (M6.3), MCP (M6.6), A2A (M6.7),
Observability (M6.1B-3). None of this changes `registry.py`, `catalog.py`,
`execution.py`, or the repository.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from enum import StrEnum

from .domain import ToolRequest, ToolResult

__all__ = [
    "PolicyVerdict",
    "PolicyEngine",
    "ApprovalStatus",
    "ApprovalEngine",
    "SecretsManager",
    "StreamingAdapter",
    "WorkflowBridge",
    "MCPBridge",
    "A2ABridge",
    "ObservabilitySink",
]


class PolicyVerdict(StrEnum):
    ALLOW = "allow"
    DENY = "deny"
    PENDING = "pending"  # approval-gated (§10 of the M6.1A design)


class PolicyEngine(ABC):
    """The seam for permission/rate-limit/org-restriction/feature-flag/approval-gate
    verdicts (M6.1B-2). The runtime consults this before `EXECUTING`, never after."""

    @abstractmethod
    def authorize(self, request: ToolRequest) -> PolicyVerdict: ...


class ApprovalStatus(StrEnum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"


class ApprovalEngine(ABC):
    """Resolves a `PolicyVerdict.PENDING` decision to a terminal `ApprovalStatus`
    (M6.1B-2; resolution mechanism deferred — a named risk in the M6.1A design)."""

    @abstractmethod
    def status_for(self, request_id: str) -> ApprovalStatus: ...


class SecretsManager(ABC):
    """Where a `ToolAdapter` resolves its own credentials — never via
    `ToolRequest.arguments` (M6.1B-2; §11 of the M6.1A design)."""

    @abstractmethod
    def resolve(self, key: str) -> str: ...


class StreamingAdapter(ABC):
    """A `ToolAdapter` refinement for long-running tools that report partial
    progress (M6.1B-4). The core seam stays the synchronous, single-result
    `ToolAdapter`."""

    name: str

    @abstractmethod
    def stream(self, request: ToolRequest): ...


class WorkflowBridge(ABC):
    """Where a durable multi-step Workflow (M6.3) invokes a sequence of tool calls.
    Interface only — Tool Runtime never orchestrates a workflow itself."""

    @abstractmethod
    def invoke(self, request: ToolRequest) -> ToolResult: ...


class MCPBridge(ABC):
    """Where an MCP-exposed tool (M6.6) registers into this runtime — mirrors
    `bridge.ProviderModelBridge`'s exact shape. Interface only."""

    @abstractmethod
    def register_external_tools(self) -> None: ...


class A2ABridge(ABC):
    """Where an agent-to-agent protocol (M6.7) exposes this runtime's tools to a
    remote agent. Interface only."""

    @abstractmethod
    def expose(self) -> None: ...


@dataclass(frozen=True, slots=True)
class ObservabilityEvent:
    request_id: str
    phase: str
    detail: str = ""


class ObservabilitySink(ABC):
    """A richer metrics/audit sink than the basic counters this milestone ships
    (M6.1B-3). Interface only."""

    @abstractmethod
    def record(self, event: ObservabilityEvent) -> None: ...
