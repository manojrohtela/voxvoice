"""tool.observability — Tool Runtime Observability & Replay (M6.1B-1 + M6.1B-3).

M6.1B-1 shipped `Metric` (basic execution counters only, kept verbatim in
`legacy.py` — `tool/runtime.py`, frozen, still imports `Metric` from this
package's top level; no behaviour change here).

M6.1B-3 adds the operational intelligence layer: `ExecutionTrace` +
`ToolGovernanceDecisionTrace` (pure projections of an already-finished
`ToolResult`/`GovernanceDecision`, never re-executing or re-evaluating anything),
`DecisionReason` (deterministic, no AI), `ReplayRecord` + `ReplayEngine`
(deterministic replay against `EchoToolAdapter` only), `ToolRuntimeMetrics`
(in-memory counters only), `ToolRuntimeDiagnostics`, `FailureAnalysis` (a fixed
lookup table, no heuristics), `ToolAuditRecord`, a temporary `ReplayRepository`,
expanded `ToolRuntimeStatistics`, and 8 additive `tool.*` events. `ToolObservability`
(the facade) never executes a tool and never evaluates governance — `tool/runtime.py`
and `tool/governance/` are untouched by this milestone. Streaming, async execution,
cancellation propagation, Workflow/Agent/MCP/A2A/Skills/Hooks/Loop-Engine
integration, and real telemetry backends are explicit non-goals (reserved as
additive extension interfaces in `extensions.py`).
"""

from .audit import audit_record_for
from .config import (
    RUNTIME_VERSION,
    ObservabilityCollectionsConfig,
    ObservabilityConfiguration,
)
from .diagnostics import ToolRuntimeDiagnostics, build_diagnostics
from .domain import (
    DecisionReason,
    ExecutionTrace,
    FailureAnalysis,
    FailureCategory,
    ReasonCode,
    ReplayComparison,
    ReplayRecord,
    SourceLevel,
    ToolAuditRecord,
    ToolGovernanceDecisionTrace,
)
from .events import PHASE_EVENT_TYPE, ObservabilityPhase
from .extensions import (
    CloudLoggingSink,
    EnterpriseSIEMSink,
    ExternalAuditSink,
    GrafanaDashboardProvider,
    OpenTelemetryExporter,
    PrometheusExporter,
)
from .factory import ObservabilityFactory
from .failure import analyze_failure
from .governance_trace import governance_trace_for
from .ids import audit_id_for, replay_id_for, trace_id_for
from .legacy import Metric
from .metrics import LatencySample, ToolRuntimeMetrics
from .reasons import classify_error, matched_rule_repr, reason_for_decision
from .replay import replay_record_for
from .replay_engine import ReplayEngine
from .repository import ReplayRepository
from .runtime import ToolObservability
from .serializer import ObservabilitySerializer
from .statistics import (
    LatencyDistribution,
    ToolRuntimeStatistics,
    compute_statistics,
    latency_distribution,
)
from .trace import execution_trace_for

__all__ = [
    # legacy (M6.1B-1)
    "Metric",
    # facade + factory
    "ToolObservability",
    "ObservabilityFactory",
    # domain
    "ReasonCode",
    "SourceLevel",
    "DecisionReason",
    "ToolGovernanceDecisionTrace",
    "ExecutionTrace",
    "ReplayRecord",
    "ReplayComparison",
    "FailureCategory",
    "FailureAnalysis",
    "ToolAuditRecord",
    # builders
    "execution_trace_for",
    "governance_trace_for",
    "replay_record_for",
    "audit_record_for",
    "analyze_failure",
    "reason_for_decision",
    "classify_error",
    "matched_rule_repr",
    # replay
    "ReplayEngine",
    "ReplayRepository",
    # metrics + diagnostics
    "ToolRuntimeMetrics",
    "LatencySample",
    "ToolRuntimeDiagnostics",
    "build_diagnostics",
    # statistics
    "ToolRuntimeStatistics",
    "LatencyDistribution",
    "compute_statistics",
    "latency_distribution",
    # repository + serializer + config
    "ObservabilitySerializer",
    "ObservabilityConfiguration",
    "ObservabilityCollectionsConfig",
    "RUNTIME_VERSION",
    # ids
    "trace_id_for",
    "replay_id_for",
    "audit_id_for",
    # events
    "ObservabilityPhase",
    "PHASE_EVENT_TYPE",
    # enterprise extension interfaces (no implementation)
    "OpenTelemetryExporter",
    "PrometheusExporter",
    "GrafanaDashboardProvider",
    "CloudLoggingSink",
    "EnterpriseSIEMSink",
    "ExternalAuditSink",
]
