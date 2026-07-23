"""tool — the Tool Runtime (Epic 6, M6.1B-1: core).

A tool-neutral, deterministic, event-driven runtime that is the execution layer for
external capabilities an Agent invokes (calendar/email/Slack/CRM/database/search/
filesystem/browser/HTTP/internal services) — it never plans, reasons, orchestrates
workflow, holds agent state, generates prompts, or routes to AI providers. Built on
the M5.0 foundation: a `BusConsumer`, two `core.fsm` lifecycles (descriptor +
execution), a repository layer over the `core.providers.stores.DocumentStore`
contract (**temporary** storage only), and `tool.*` events that Replay/Gateway
observe for free. Its contract is a hybrid: an immutable, versioned `ToolManifest`
(never executable code) paired with a separately registered `ToolAdapter` — the
deterministic `EchoToolAdapter` by default — so `tool` imports no external
integration and no sibling runtime, keeping the graph acyclic (`tool → core`).

M6.1B-1 delivers the core: domain (manifest/descriptor/request/result), both
lifecycles (descriptor + execution), `ToolRegistry` (registration/discovery/
lifecycle — no execution logic) + `ToolCapabilityCatalog` (no intelligence),
structural manifest/argument validation, the deterministic `EchoToolAdapter`, the
temporary repository, serializer/config/factory/observability, and
execute/get/remove/history/expire/statistics. Policy, approval, secrets,
observability engine, streaming, and workflow/MCP/A2A integration are explicit
non-goals here — reserved as additive extension interfaces (M6.1B-2+).
"""

from .catalog import ToolCapabilityCatalog
from .config import CollectionsConfig, StoreConfig, ToolConfiguration
from .domain import (
    Classification,
    DescriptorState,
    ExecutionState,
    ToolAttachment,
    ToolDescriptor,
    ToolExecutionTrace,
    ToolManifest,
    ToolRequest,
    ToolResult,
    ToolStatistics,
    ToolUsage,
    TraceEvent,
    Visibility,
)
from .events import ToolPhase
from .extensions import (
    A2ABridge,
    ApprovalEngine,
    ApprovalStatus,
    MCPBridge,
    ObservabilityEvent,
    ObservabilitySink,
    PolicyEngine,
    PolicyVerdict,
    SecretsManager,
    StreamingAdapter,
    WorkflowBridge,
)
from .factory import ToolFactory
from .ids import content_hash, descriptor_id_for, request_hash_for, result_hash_for
from .lifecycle import (
    TOOL_DESCRIPTOR_LIFECYCLE,
    TOOL_EXECUTION_LIFECYCLE,
    DescriptorTrigger,
    ExecutionTrigger,
    advance_descriptor,
    advance_execution,
)
from .observability import Metric
from .ports import ToolAdapter, ToolExecutionOutcome
from .registry import ToolRegistry
from .repositories import ToolRepository
from .results import ResultNormalizer
from .runtime import ToolRuntime
from .serializer import ToolSerializer
from .stores import EchoToolAdapter, InMemoryDocumentStore
from .validation import validate_arguments, validate_manifest

__all__ = [
    # runtime + composition
    "ToolRuntime",
    "ToolFactory",
    "ToolRegistry",
    "ToolCapabilityCatalog",
    "ResultNormalizer",
    "ToolSerializer",
    # domain
    "ToolManifest",
    "ToolDescriptor",
    "ToolRequest",
    "ToolResult",
    "ToolUsage",
    "ToolAttachment",
    "ToolExecutionTrace",
    "TraceEvent",
    "ToolStatistics",
    "DescriptorState",
    "ExecutionState",
    "Classification",
    "Visibility",
    # ids
    "content_hash",
    "descriptor_id_for",
    "request_hash_for",
    "result_hash_for",
    # lifecycle
    "TOOL_DESCRIPTOR_LIFECYCLE",
    "TOOL_EXECUTION_LIFECYCLE",
    "DescriptorTrigger",
    "ExecutionTrigger",
    "advance_descriptor",
    "advance_execution",
    # ports
    "ToolAdapter",
    "ToolExecutionOutcome",
    # validation
    "validate_manifest",
    "validate_arguments",
    # repository + stores
    "ToolRepository",
    "InMemoryDocumentStore",
    "EchoToolAdapter",
    # observability + events
    "Metric",
    "ToolPhase",
    # config
    "ToolConfiguration",
    "StoreConfig",
    "CollectionsConfig",
    # extension hooks (interfaces only)
    "PolicyVerdict",
    "PolicyEngine",
    "ApprovalStatus",
    "ApprovalEngine",
    "SecretsManager",
    "StreamingAdapter",
    "WorkflowBridge",
    "MCPBridge",
    "A2ABridge",
    "ObservabilityEvent",
    "ObservabilitySink",
]
