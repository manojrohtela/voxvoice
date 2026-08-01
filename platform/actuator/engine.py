"""Deployment Engine + coordinators (Epic 8, M8.16) — ADR-0022.

The `DeploymentEngine` is the **sole writer** of deployment execution state (§A2.5); it drives the
deterministic lifecycle FSM (`lifecycle.py`), applies a read-only `DeploymentPlan` through an
`InfrastructureAdapter`, and emits typed `deployment.*` facts through an injected sink (staying
decoupled from the control plane / audit store). Its *behaviour* is deterministic; only the infra
edge is not (§A2.4 — "own the loop, rent the edges").

Each coordinator has a single responsibility (AR-50): the `DeploymentQueue` (bounded, ordered
work queue), `DeploymentScheduler` (one in-flight deploy per product+environment), the
`ApprovalCoordinator` (separation-of-duties gate), the `RollbackCoordinator` (resolves + drives
rollback), the `HealthVerifier` (startup/readiness/liveness gate), the `CancellationManager`
(propagated cancellation — never an orphan), the `RetryManager` (deterministic backoff + budget),
and the `TimeoutManager` (per-stage + whole-deployment deadlines). `DeploymentExecutor` is the
apply/verify step-runner the engine dispatches to. `DeploymentActuator` is the public facade.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Protocol

from .domain import (
    ApprovalDecision,
    ApprovalRecord,
    DeploymentExecution,
    DeploymentExecutionResult,
    DeploymentExecutionState,
    DeploymentJob,
    HealthReport,
    HealthState,
    RollbackReason,
    RollbackRecord,
    RollbackTarget,
)
from .errors import (
    ApprovalError,
    DeploymentNotFoundError,
    InfrastructureError,
    InvalidDeploymentPlanError,
)
from .events import DeploymentEventType as ET
from .infrastructure import InfrastructureAdapter, InfrastructureRegistry
from .lifecycle import TERMINAL_STATES, assert_transition
from .repository import DeploymentExecutionRepository

S = DeploymentExecutionState


class _Clock(Protocol):
    def now(self) -> float: ...


class _Ids(Protocol):
    def new(self, prefix: str) -> str: ...


EventSink = Callable[[ET, DeploymentExecution, dict], None]

PREFIX_EXECUTION = "dpx"
PREFIX_APPROVAL = "apr"
PREFIX_ROLLBACK = "rbk"


# ── coordinators (each a single responsibility) ───────────────────────────────────


class DeploymentQueue:
    """A bounded, ordered work queue of execution ids (never an unbounded in-memory queue —
    §A11.1). The durable execution rows are the System of Record; this is the dispatch order."""

    def __init__(self, max_size: int = 256) -> None:
        self._items: list[str] = []
        self._max = max_size

    def enqueue(self, execution_id: str) -> None:
        if len(self._items) >= self._max:
            raise InfrastructureError("deployment queue is full")
        if execution_id not in self._items:
            self._items.append(execution_id)

    def remove(self, execution_id: str) -> None:
        if execution_id in self._items:
            self._items.remove(execution_id)

    def __contains__(self, execution_id: str) -> bool:
        return execution_id in self._items

    @property
    def size(self) -> int:
        return len(self._items)


class DeploymentScheduler:
    """Deterministic dispatch with concurrency control: at most one in-flight deployment per
    (product, environment) (ADR-0022 §Concurrency Control). Conflicting deploys serialize."""

    def __init__(self) -> None:
        self._in_flight: set[tuple[str, str]] = set()

    @staticmethod
    def _key(job: DeploymentJob) -> tuple[str, str]:
        return (job.project_id, job.environment)

    def try_acquire(self, job: DeploymentJob) -> bool:
        key = self._key(job)
        if key in self._in_flight:
            return False
        self._in_flight.add(key)
        return True

    def release(self, job: DeploymentJob) -> None:
        self._in_flight.discard(self._key(job))


class ApprovalCoordinator:
    """Gates execution on approval with separation of duties (ADR-0021 §5 / §A11.2): the author of
    a deployment can never approve their own. Verdict-only — it never actuates."""

    def decide(
        self,
        execution: DeploymentExecution,
        decision: ApprovalDecision,
        actor: str,
        reason: str,
        at: float,
        approval_id: str,
    ) -> ApprovalRecord:
        if execution.state is not S.PENDING_APPROVAL:
            raise ApprovalError(
                f"execution {execution.id!r} not awaiting approval (state {execution.state.value})"
            )
        if decision is ApprovalDecision.APPROVED and actor == execution.job.requested_by:
            raise ApprovalError(
                "separation of duties: the requester cannot approve their own deployment"
            )
        return ApprovalRecord(id=approval_id, decision=decision, actor=actor, at=at, reason=reason)


class RollbackCoordinator:
    """Resolves the rollback target from Epic 7's `RollbackPolicy` (same-major rule) and drives the
    revert through the adapter. A first-class execution path, not an afterthought."""

    def resolve_target(self, job: DeploymentJob) -> RollbackTarget:
        version = job.rollback_version or "previous"
        return RollbackTarget(version=version, mode=job.rollback_mode or "previous_version")

    def execute(
        self,
        adapter: InfrastructureAdapter,
        job: DeploymentJob,
        target: RollbackTarget,
        reason: RollbackReason,
        at: float,
        actor: str,
        rollback_id: str,
    ) -> RollbackRecord:
        try:
            res = adapter.rollback(job.deployment_id, target.version)
            return RollbackRecord(
                id=rollback_id,
                target=target,
                reason=reason,
                at=at,
                actor=actor,
                ok=res.ok,
                detail=res.detail,
            )
        except InfrastructureError as exc:
            return RollbackRecord(
                id=rollback_id,
                target=target,
                reason=reason,
                at=at,
                actor=actor,
                ok=False,
                detail=str(exc),
            )


class HealthVerifier:
    """Post-apply health verification: startup → readiness → liveness (ADR-0022 §Health). Gates
    completion; a failure routes to rollback."""

    def verify(self, adapter: InfrastructureAdapter, job: DeploymentJob, at: float) -> HealthReport:
        probe = adapter.health(job.deployment_id)
        healthy = probe.startup_ok and probe.ready and probe.live
        state = HealthState.LIVE if healthy else HealthState.UNHEALTHY
        return HealthReport(
            state=state,
            startup_ok=probe.startup_ok,
            ready=probe.ready,
            live=probe.live,
            at=at,
            checks=(
                ("startup", probe.startup_ok),
                ("readiness", probe.ready),
                ("liveness", probe.live),
            ),
            detail=probe.detail,
        )


class CancellationManager:
    """A propagated cancellation token (mirrors `PipelineCancellation`) — a cancelled deploy always
    reaches a terminal state, never an orphan (ADR-0022 §Cancellation)."""

    def __init__(self) -> None:
        self._requested: set[str] = set()

    def request(self, execution_id: str) -> None:
        self._requested.add(execution_id)

    def is_requested(self, execution_id: str) -> bool:
        return execution_id in self._requested

    def clear(self, execution_id: str) -> None:
        self._requested.discard(execution_id)


class RetryManager:
    """Deterministic exponential backoff within a retry budget (§A10). Idempotent by construction —
    a retry re-queues the same job with an incremented attempt count."""

    def __init__(self, max_attempts: int = 3, base_delay: float = 1.0) -> None:
        self.max_attempts = max_attempts
        self.base_delay = base_delay

    def can_retry(self, attempts: int) -> bool:
        return attempts < self.max_attempts

    def next_delay(self, attempt: int) -> float:
        return self.base_delay * (2 ** max(0, attempt - 1))


class TimeoutManager:
    """Per-stage and whole-deployment deadlines with typed errors (§A10). Deterministic thresholds;
    consulted defensively so a stuck stage routes to failure recovery rather than hanging."""

    def __init__(self, stage_timeout_s: float = 300.0, whole_timeout_s: float = 1800.0) -> None:
        self.stage_timeout_s = stage_timeout_s
        self.whole_timeout_s = whole_timeout_s

    def stage_exceeded(self, started_at: float, now: float) -> bool:
        return (now - started_at) > self.stage_timeout_s

    def whole_exceeded(self, created_at: float, now: float) -> bool:
        return (now - created_at) > self.whole_timeout_s


class DeploymentExecutor:
    """The apply/verify step-runner the engine dispatches to. Applies the plan through the adapter
    and returns the (ok, live_version, detail) outcome — no state writing (that stays the engine's,
    the one writer)."""

    def run_apply(self, adapter: InfrastructureAdapter, job: DeploymentJob):
        return adapter.apply(job.deployment_id, job.source_ref, job.environment)


# ── the engine (sole writer) ──────────────────────────────────────────────────────


class DeploymentEngine:
    """Drives the FSM and owns all writes to `DeploymentExecutionRepository`. Deterministic; the
    only non-determinism is behind the `InfrastructureAdapter` seam."""

    def __init__(
        self,
        *,
        repository: DeploymentExecutionRepository,
        registry: InfrastructureRegistry | None = None,
        clock: _Clock,
        ids: _Ids,
        emit: EventSink | None = None,
    ) -> None:
        self._repo = repository
        self._registry = registry or InfrastructureRegistry()
        self._clock = clock
        self._ids = ids
        self._emit_sink = emit
        self.queue = DeploymentQueue()
        self.scheduler = DeploymentScheduler()
        self.approvals = ApprovalCoordinator()
        self.rollbacks = RollbackCoordinator()
        self.health = HealthVerifier()
        self.cancellations = CancellationManager()
        self.retries = RetryManager()
        self.timeouts = TimeoutManager()
        self.executor = DeploymentExecutor()

    # ── helpers ───────────────────────────────────────────────────────────────────
    def _emit(self, event: ET, execution: DeploymentExecution, **data) -> None:
        if self._emit_sink is not None:
            self._emit_sink(event, execution, data)

    def _save(self, execution: DeploymentExecution) -> DeploymentExecution:
        return self._repo.update(execution)

    def _to(self, execution, to_state, *, actor="", reason="") -> DeploymentExecution:
        assert_transition(execution.state, to_state)
        return execution.transitioned(to_state, self._clock.now(), actor=actor, reason=reason)

    def _require(self, org_id: str, ref: str) -> DeploymentExecution:
        e = self._repo.get(org_id, ref)
        if e is None:
            raise DeploymentNotFoundError(f"deployment execution {ref!r} not found")
        return e

    def adapter_for(self, target: str) -> InfrastructureAdapter:
        return self._registry.get(target)

    def registry(self) -> InfrastructureRegistry:
        return self._registry

    # ── lifecycle: submit ───────────────────────────────────────────────────────────
    def submit(
        self, job: DeploymentJob, *, plan_valid: bool = True, auto_approve: bool = False
    ) -> DeploymentExecution:
        now = self._clock.now()
        execution = DeploymentExecution(
            id=self._ids.new(PREFIX_EXECUTION),
            job=job,
            state=S.REQUESTED,
            created_at=now,
            updated_at=now,
        )
        execution = self._repo.add(execution)
        self._emit(ET.REQUESTED, execution)
        execution = self._save(self._to(execution, S.VALIDATING, actor=job.requested_by))
        if not plan_valid:
            execution = self._save(self._to(execution, S.FAILED, reason="invalid deployment plan"))
            execution = self._save(
                execution.with_result(
                    DeploymentExecutionResult("failed", message="invalid deployment plan"), now
                )
            )
            self._emit(ET.FAILED, execution, error="invalid_plan")
            raise InvalidDeploymentPlanError("deployment plan failed validation")
        self._emit(ET.VALIDATED, execution)
        if job.requires_approval and not auto_approve:
            execution = self._save(self._to(execution, S.PENDING_APPROVAL))
            self._emit(ET.PENDING_APPROVAL, execution)
            return execution
        if job.requires_approval and auto_approve:
            record = ApprovalRecord(
                id=self._ids.new(PREFIX_APPROVAL),
                decision=ApprovalDecision.APPROVED,
                actor="system",
                at=now,
                reason="auto-approved",
            )
            execution = self._save(execution.with_approval(record))
            self._emit(ET.APPROVED, execution, actor="system")
        execution = self._save(self._to(execution, S.QUEUED))
        self.queue.enqueue(execution.id)
        self._emit(ET.QUEUED, execution)
        return execution

    # ── lifecycle: approve / reject ─────────────────────────────────────────────────
    def approve(self, org_id, ref, actor, reason="") -> DeploymentExecution:
        execution = self._require(org_id, ref)
        record = self.approvals.decide(
            execution,
            ApprovalDecision.APPROVED,
            actor,
            reason,
            self._clock.now(),
            self._ids.new(PREFIX_APPROVAL),
        )
        execution = self._save(execution.with_approval(record))
        self._emit(ET.APPROVED, execution, actor=actor)
        execution = self._save(self._to(execution, S.QUEUED, actor=actor))
        self.queue.enqueue(execution.id)
        self._emit(ET.QUEUED, execution)
        return execution

    def reject(self, org_id, ref, actor, reason="") -> DeploymentExecution:
        execution = self._require(org_id, ref)
        record = self.approvals.decide(
            execution,
            ApprovalDecision.REJECTED,
            actor,
            reason,
            self._clock.now(),
            self._ids.new(PREFIX_APPROVAL),
        )
        execution = self._save(execution.with_approval(record))
        self._emit(ET.REJECTED, execution, actor=actor)
        execution = self._save(self._to(execution, S.FAILED, actor=actor, reason="rejected"))
        execution = self._save(
            execution.with_result(
                DeploymentExecutionResult("failed", message="rejected"), self._clock.now()
            )
        )
        self._emit(ET.FAILED, execution, error="rejected")
        return execution

    # ── lifecycle: execute (apply → verify → complete / rollback) ────────────────────
    def execute(self, org_id, ref) -> DeploymentExecution:
        execution = self._require(org_id, ref)
        if execution.state is not S.QUEUED:
            raise ApprovalError(
                f"execution {execution.id!r} is not queued (state={execution.state.value})"
            )
        job = execution.job
        if not self.scheduler.try_acquire(job):
            raise InfrastructureError(
                f"another deployment is in flight for {job.project_id}/{job.environment}"
            )
        try:
            if self.cancellations.is_requested(execution.id):
                return self._finish_cancelled(execution)
            execution = self._save(self._to(execution, S.EXECUTING))
            execution = self._save(execution.logged(self._clock.now(), "execute", "apply started"))
            self._emit(ET.STARTED, execution)
            adapter = self.adapter_for(job.target)
            try:
                res = self.executor.run_apply(adapter, job)
            except InfrastructureError as exc:
                execution = self._save(execution.logged(self._clock.now(), "execute", str(exc)))
                execution = self._save(self._to(execution, S.FAILED, reason=str(exc)))
                execution = self._save(
                    execution.with_result(
                        DeploymentExecutionResult("failed", message=str(exc)), self._clock.now()
                    )
                )
                self._emit(ET.FAILED, execution, error=str(exc))
                return execution
            execution = self._save(
                execution.logged(self._clock.now(), "execute", f"applied {res.live_version}")
            )
            execution = self._save(self._to(execution, S.VERIFYING))
            self._emit(ET.VERIFYING, execution)
            report = self.health.verify(adapter, job, self._clock.now())
            execution = self._save(execution.with_health(report))
            if report.healthy:
                execution = self._save(self._to(execution, S.COMPLETED))
                execution = self._save(
                    execution.with_result(
                        DeploymentExecutionResult("completed", live_version=res.live_version),
                        self._clock.now(),
                    )
                )
                self._emit(ET.COMPLETED, execution, live_version=res.live_version)
                return execution
            # health failed → roll back
            self._emit(ET.HEALTH_FAILED, execution)
            return self._rollback_from(
                execution, adapter, RollbackReason.HEALTH_FAILED, actor="system"
            )
        finally:
            self.scheduler.release(job)

    def _rollback_from(
        self, execution, adapter, reason: RollbackReason, *, actor: str
    ) -> DeploymentExecution:
        execution = self._save(
            self._to(execution, S.ROLLING_BACK, actor=actor, reason=reason.value)
        )
        self._emit(ET.ROLLING_BACK, execution)
        target = self.rollbacks.resolve_target(execution.job)
        record = self.rollbacks.execute(
            adapter,
            execution.job,
            target,
            reason,
            self._clock.now(),
            actor,
            self._ids.new(PREFIX_ROLLBACK),
        )
        execution = self._save(execution.with_rollback(record))
        execution = self._save(self._to(execution, S.ROLLED_BACK, actor=actor))
        execution = self._save(
            execution.with_result(
                DeploymentExecutionResult(
                    "rolled_back",
                    live_version=target.version,
                    message=f"rolled back ({reason.value})",
                ),
                self._clock.now(),
            )
        )
        self._emit(ET.ROLLED_BACK, execution, target_version=target.version)
        return execution

    def _finish_cancelled(self, execution) -> DeploymentExecution:
        execution = self._save(self._to(execution, S.CANCELLED, reason="cancelled"))
        execution = self._save(
            execution.with_result(
                DeploymentExecutionResult("cancelled", message="cancelled"), self._clock.now()
            )
        )
        self.queue.remove(execution.id)
        self.cancellations.clear(execution.id)
        self._emit(ET.CANCELLED, execution)
        return execution

    # ── lifecycle: cancel / retry / rollback ─────────────────────────────────────────
    def cancel(self, org_id, ref, actor) -> DeploymentExecution:
        execution = self._require(org_id, ref)
        if execution.state in TERMINAL_STATES:
            raise ApprovalError(
                f"execution {execution.id!r} is terminal (state={execution.state.value})"
            )
        self.cancellations.request(execution.id)
        execution = self._save(self._to(execution, S.CANCELLED, actor=actor, reason="cancelled"))
        execution = self._save(
            execution.with_result(
                DeploymentExecutionResult("cancelled", message="cancelled by " + actor),
                self._clock.now(),
            )
        )
        self.queue.remove(execution.id)
        self.scheduler.release(execution.job)
        self._emit(ET.CANCELLED, execution, actor=actor)
        return execution

    def retry(self, org_id, ref, actor) -> DeploymentExecution:
        execution = self._require(org_id, ref)
        if execution.state is not S.FAILED:
            raise ApprovalError(
                f"only a failed deployment can be retried (state={execution.state.value})"
            )
        if not self.retries.can_retry(execution.attempts):
            raise InfrastructureError(f"retry budget exhausted after {execution.attempts} attempts")
        execution = self._save(execution.requeued(self._clock.now()))
        execution = self._save(self._to(execution, S.QUEUED, actor=actor, reason="retry"))
        self.queue.enqueue(execution.id)
        self._emit(ET.QUEUED, execution, retry=True)
        return execution

    def rollback(self, org_id, ref, actor, reason="") -> DeploymentExecution:
        execution = self._require(org_id, ref)
        if execution.state is not S.COMPLETED:
            raise ApprovalError(
                f"only a completed deployment can be rolled back (state={execution.state.value})"
            )
        adapter = self.adapter_for(execution.job.target)
        return self._rollback_from(execution, adapter, RollbackReason.MANUAL, actor=actor)

    # ── reads ───────────────────────────────────────────────────────────────────────
    def status(self, org_id, ref) -> DeploymentExecution:
        return self._require(org_id, ref)

    def logs(self, org_id, ref) -> tuple[dict, ...]:
        execution = self._require(org_id, ref)
        adapter = self.adapter_for(execution.job.target)
        infra_logs = tuple(
            {"at": 0.0, "stage": "infra", "message": m}
            for m in adapter.logs(execution.job.deployment_id)
        )
        return tuple(entry.as_dict() for entry in execution.logs) + infra_logs

    def live_health(self, org_id, ref) -> dict:
        execution = self._require(org_id, ref)
        adapter = self.adapter_for(execution.job.target)
        probe = adapter.health(execution.job.deployment_id)
        return {
            "deployment_id": execution.deployment_id,
            "infra_status": adapter.status(execution.job.deployment_id),
            "startup_ok": probe.startup_ok,
            "ready": probe.ready,
            "live": probe.live,
            "healthy": probe.startup_ok and probe.ready and probe.live,
            "last_report": execution.last_health.as_dict() if execution.last_health else None,
        }

    def list_for_project(self, org_id, project_id) -> tuple[DeploymentExecution, ...]:
        return self._repo.list_for_project(org_id, project_id)


class DeploymentActuator:
    """The public facade over the `DeploymentEngine` (ADR-0022 §Deployment Architecture). It is the
    single entry the control plane calls; it never contains vendor logic (that is the adapter's) and
    never mutates the `DeploymentPlan` (it consumes it read-only). A convenience `deploy` runs the
    whole local path (submit → [auto-approve] → execute) deterministically."""

    def __init__(self, engine: DeploymentEngine) -> None:
        self.engine = engine

    def deploy(
        self, job: DeploymentJob, *, plan_valid=True, auto_approve=False
    ) -> DeploymentExecution:
        execution = self.engine.submit(job, plan_valid=plan_valid, auto_approve=auto_approve)
        if execution.state is S.QUEUED:
            execution = self.engine.execute(execution.org_id, execution.id)
        return execution

    def approve(self, org_id, ref, actor, reason="") -> DeploymentExecution:
        execution = self.engine.approve(org_id, ref, actor, reason)
        return self.engine.execute(org_id, execution.id)

    def reject(self, org_id, ref, actor, reason="") -> DeploymentExecution:
        return self.engine.reject(org_id, ref, actor, reason)

    def cancel(self, org_id, ref, actor) -> DeploymentExecution:
        return self.engine.cancel(org_id, ref, actor)

    def retry(self, org_id, ref, actor) -> DeploymentExecution:
        execution = self.engine.retry(org_id, ref, actor)
        return self.engine.execute(org_id, execution.id)

    def rollback(self, org_id, ref, actor, reason="") -> DeploymentExecution:
        return self.engine.rollback(org_id, ref, actor, reason)
