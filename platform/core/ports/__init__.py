"""core.ports — dependency-inversion interfaces for the AI runtime modules (M5.0).

Each future runtime *implements* its port; consumers depend on the port (in `core`),
never on another runtime package — so no runtime package imports another and the
dependency graph stays acyclic. Interfaces + neutral DTOs only; no implementation.
Shapes are provisional until each module lands (M5.1+).
"""

from .agent import AgentPort, AgentRef
from .context import (
    AssembledContext,
    ContextItem,
    ContextPort,
    ContextRequest,
)
from .knowledge import KnowledgePassage, KnowledgePort, KnowledgeQuery
from .memory import MemoryHit, MemoryPort, MemoryQuery, MemoryScope, MemoryTier
from .prompt import PromptFragment, PromptPort
from .reasoning import Decision, DecisionKind, ReasoningPort, ReasoningRequest
from .tool import ToolCall, ToolPort, ToolResult, ToolSpec
from .workflow import WorkflowHandle, WorkflowPort, WorkflowStart

__all__ = [
    "MemoryPort", "MemoryQuery", "MemoryHit", "MemoryScope", "MemoryTier",
    "PromptPort", "PromptFragment",
    "KnowledgePort", "KnowledgeQuery", "KnowledgePassage",
    "ContextPort", "ContextRequest", "ContextItem", "AssembledContext",
    "ReasoningPort", "ReasoningRequest", "Decision", "DecisionKind",
    "ToolPort", "ToolSpec", "ToolCall", "ToolResult",
    "WorkflowPort", "WorkflowStart", "WorkflowHandle",
    "AgentPort", "AgentRef",
]
