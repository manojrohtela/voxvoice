"""ToolObservability (M6.1B-3) — the observability + replay facade.

Ties execution tracing, governance-decision tracing, replay recording/validation,
metrics, diagnostics, failure analysis, and audit recording into one object. Never
executes a tool, never evaluates governance, never mutates `ToolRuntime`/
`ToolGovernance` state — everything here is built *from* their already-produced
`ToolResult`/`GovernanceDecision`. `tool/runtime.py` and `tool/governance/` are
untouched by this milestone.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from typing import Any

from core.events import Event, EventType

from .._run import run_coro_sync
from ..domain import ToolRequest, ToolResult
from ..governance.domain import GovernanceDecision, GovernanceIdentity
from ..serializer import ToolSerializer
from .audit import audit_record_for
from .config import ObservabilityConfiguration
from .diagnostics import ToolRuntimeDiagnostics, build_diagnostics
from .domain import (
    ExecutionTrace,
    FailureAnalysis,
    ReplayComparison,
    ReplayRecord,
    ToolAuditRecord,
    ToolGovernanceDecisionTrace,
)
from .events import PHASE_EVENT_TYPE, ObservabilityPhase
from .failure import analyze_failure as _analyze_failure
from .governance_trace import governance_trace_for
from .metrics import ToolRuntimeMetrics
from .replay import replay_record_for
from .replay_engine import ReplayEngine
from .repository import ReplayRepository
from .serializer import ObservabilitySerializer
from .statistics import ToolRuntimeStatistics, compute_statistics
from .trace import execution_trace_for

Publisher = Callable[..., Event | None]


class ToolObservability:
    def __init__(
        self,
        *,
        config: ObservabilityConfiguration,
        repository: ReplayRepository,
        metrics: ToolRuntimeMetrics | None = None,
        replay_engine: ReplayEngine | None = None,
        serializer: ObservabilitySerializer | None = None,
        tool_serializer: ToolSerializer | None = None,
        clock: Callable[[], float] | None = None,
        publish: Publisher | None = None,
    ) -> None:
        import time

        self._config = config
        self._repo = repository
        self._metrics = metrics or ToolRuntimeMetrics()
        self._replay_engine = replay_engine or ReplayEngine()
        self._serializer = serializer or ObservabilitySerializer()
        self._tool_serializer = tool_serializer or ToolSerializer()
        self._clock = clock or time.time
        self._publish = publish

    @property
    def metrics(self) -> ToolRuntimeMetrics:
        return self._metrics

    # ── execution + governance tracing (pure projections) ────────────────────
    def trace_execution(
        self, request: ToolRequest, result: ToolResult, *,
        started_at: float | None = None, ended_at: float | None = None,
        metadata: Mapping[str, str] | None = None,
    ) -> ExecutionTrace:
        trace = execution_trace_for(
            request, result, started_at=started_at, ended_at=ended_at, metadata=metadata,
        )
        duration = ended_at - started_at if started_at is not None and ended_at is not None else 0.0
        self._metrics.record_execution(result, duration_ms=max(duration, 0.0) * 1000.0)
        self._emit(ObservabilityPhase.EXECUTION_TRACED, request_id=request.request_id,
                   extra={"trace_id": trace.trace_id})
        self._emit(ObservabilityPhase.METRICS_UPDATED, request_id=request.request_id)
        return trace

    def trace_governance(
        self, decision: GovernanceDecision, *, secret_provider_used: str | None = None
    ) -> ToolGovernanceDecisionTrace:
        trace = governance_trace_for(decision, secret_provider_used=secret_provider_used)
        self._metrics.record_governance(
            policy=decision.trace.policy is not None,
            authorization=decision.trace.authorization is not None,
            approval=decision.trace.approval is not None,
        )
        return trace

    # ── replay ─────────────────────────────────────────────────────────────────
    def record_replay(
        self, request: ToolRequest, result: ToolResult, execution_trace: ExecutionTrace, *,
        governance_trace: ToolGovernanceDecisionTrace | None = None,
    ) -> ReplayRecord:
        record = replay_record_for(
            request, result, execution_trace, governance_trace=governance_trace,
            now=self._clock(), serializer=self._tool_serializer,
        )
        run_coro_sync(self._repo.add_replay(record))
        self._emit(ObservabilityPhase.REPLAY_RECORDED, request_id=request.request_id,
                   extra={"replay_id": record.replay_id})
        return record

    def validate_replay(self, replay_id: str) -> ReplayComparison:
        from core.errors import ConfigError

        record = run_coro_sync(self._repo.get_replay(replay_id))
        if record is None:
            raise ConfigError(f"unknown replay {replay_id!r}")
        comparison = self._replay_engine.validate(record)
        self._metrics.record_replay(comparison)
        phase = (
            ObservabilityPhase.REPLAY_VALIDATED if comparison.matches
            else ObservabilityPhase.REPLAY_MISMATCH
        )
        self._emit(phase, request_id=record.execution_trace.request_id,
                   extra={"replay_id": replay_id, "matches": comparison.matches})
        self._emit(ObservabilityPhase.METRICS_UPDATED, request_id=record.execution_trace.request_id)
        return comparison

    def compare_replays(self, replay_id_a: str, replay_id_b: str) -> ReplayComparison:
        from core.errors import ConfigError

        a = run_coro_sync(self._repo.get_replay(replay_id_a))
        b = run_coro_sync(self._repo.get_replay(replay_id_b))
        if a is None or b is None:
            raise ConfigError("unknown replay id(s) for comparison")
        return self._replay_engine.compare(a, b)

    # ── failure analysis ──────────────────────────────────────────────────────
    def analyze_failure(self, request_id: str, result: ToolResult) -> FailureAnalysis | None:
        analysis = _analyze_failure(result)
        if analysis is not None:
            self._emit(ObservabilityPhase.FAILURE_ANALYZED, request_id=request_id,
                       extra={"category": analysis.category.value})
        return analysis

    # ── audit ──────────────────────────────────────────────────────────────────
    def record_audit(
        self, *, request_id: str, tool: str, action: str, decision: str,
        identity: GovernanceIdentity | None = None, org_id: str | None = None,
        workspace_id: str | None = None, replay_ref: str | None = None,
        trace_ref: str | None = None,
    ) -> ToolAuditRecord:
        record = audit_record_for(
            request_id=request_id, tool=tool, action=action, decision=decision,
            now=self._clock(), identity=identity, org_id=org_id, workspace_id=workspace_id,
            replay_ref=replay_ref, trace_ref=trace_ref,
        )
        run_coro_sync(self._repo.add_audit(record))
        self._emit(ObservabilityPhase.AUDIT_RECORDED, request_id=request_id,
                   extra={"audit_id": record.audit_id})
        return record

    # ── diagnostics + statistics ───────────────────────────────────────────────
    def diagnostics(
        self, *, registry: Any, catalog: Any, governance_stats: Mapping[str, object] | None = None,
        repository_result_count: int = 0, healthy: bool = True,
    ) -> ToolRuntimeDiagnostics:
        replay_count = run_coro_sync(self._repo.replay_count())
        audit_count = run_coro_sync(self._repo.audit_count())
        diag = build_diagnostics(
            config=self._config, registry=registry, catalog=catalog, metrics=self._metrics,
            governance_stats=governance_stats, repository_result_count=repository_result_count,
            replay_count=replay_count, audit_count=audit_count, healthy=healthy,
        )
        self._emit(ObservabilityPhase.DIAGNOSTICS_GENERATED, request_id="")
        return diag

    def statistics(
        self, results: Sequence[ToolResult], *, traces: Sequence[ExecutionTrace] = ()
    ) -> ToolRuntimeStatistics:
        snapshot = self._metrics.snapshot()
        return compute_statistics(
            results, traces=traces, replay_success=snapshot["replay_success"],
            replay_count=snapshot["replay_count"],
        )

    # ── internals ──────────────────────────────────────────────────────────────
    def _emit(
        self, phase: ObservabilityPhase, *, request_id: str, extra: dict[str, Any] | None = None,
    ) -> None:
        if self._publish is None:
            return
        event_type: EventType = PHASE_EVENT_TYPE[phase]
        payload = {"phase": phase.value, "request_id": request_id, **(extra or {})}
        self._publish(event_type, payload, session_id="", org_id="")
