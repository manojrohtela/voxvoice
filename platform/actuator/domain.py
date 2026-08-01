"""Deployment Actuator domain model (Epic 8, M8.16) — ADR-0022.

The **deployment execution** aggregate the Actuator owns (§A2.5 — one writer per fact). Pure,
frozen, slotted data: a `DeploymentJob` (the immutable execution request derived from a
read-only Epic 7 `DeploymentPlan`), the `DeploymentExecution` aggregate (state + full timelines
of transitions / approvals / rollbacks / health / logs), and their read-only views. **Nothing
here talks to infrastructure** (that is `infrastructure.py`); nothing here decides transitions
(that is `lifecycle.py`). The Actuator's *behaviour* is deterministic; only the infra edge is not.

Reuses Epic 7 (`product.deployment`) types read-only — the Actuator never modifies Product
Runtime (ADR-0018 / the Epic 7 freeze).
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from enum import StrEnum
from typing import Any


class DeploymentExecutionState(StrEnum):
    """The deterministic lifecycle FSM (ADR-0022). Terminal: COMPLETED / FAILED /
    ROLLED_BACK / CANCELLED."""

    REQUESTED = "requested"
    VALIDATING = "validating"
    PENDING_APPROVAL = "pending_approval"
    QUEUED = "queued"
    EXECUTING = "executing"
    VERIFYING = "verifying"
    COMPLETED = "completed"
    FAILED = "failed"
    ROLLING_BACK = "rolling_back"
    ROLLED_BACK = "rolled_back"
    CANCELLED = "cancelled"


class ApprovalDecision(StrEnum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"


class HealthState(StrEnum):
    UNKNOWN = "unknown"
    STARTING = "starting"
    READY = "ready"
    LIVE = "live"
    UNHEALTHY = "unhealthy"


class RollbackReason(StrEnum):
    HEALTH_FAILED = "health_failed"
    MANUAL = "manual"
    EXECUTION_FAILED = "execution_failed"


# ── timeline records ─────────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class StateTransition:
    """One FSM transition — every state change is an auditable fact (§A5)."""

    from_state: DeploymentExecutionState
    to_state: DeploymentExecutionState
    at: float
    actor: str = ""
    reason: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "from": self.from_state.value, "to": self.to_state.value,
            "at": self.at, "actor": self.actor, "reason": self.reason,
        }


@dataclass(frozen=True, slots=True)
class ApprovalRecord:
    id: str
    decision: ApprovalDecision
    actor: str
    at: float
    reason: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id, "decision": self.decision.value,
            "actor": self.actor, "at": self.at, "reason": self.reason,
        }


@dataclass(frozen=True, slots=True)
class RollbackTarget:
    """The version a rollback reverts to (resolved from Epic 7's `RollbackPolicy`)."""

    version: str
    mode: str = "previous_version"


@dataclass(frozen=True, slots=True)
class RollbackRecord:
    id: str
    target: RollbackTarget
    reason: RollbackReason
    at: float
    actor: str = ""
    ok: bool = True
    detail: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id, "target_version": self.target.version, "mode": self.target.mode,
            "reason": self.reason.value, "at": self.at, "actor": self.actor,
            "ok": self.ok, "detail": self.detail,
        }


@dataclass(frozen=True, slots=True)
class HealthReport:
    """Post-apply health verification (startup / readiness / liveness). Gates completion."""

    state: HealthState
    startup_ok: bool
    ready: bool
    live: bool
    at: float
    checks: tuple[tuple[str, bool], ...] = ()
    detail: str = ""

    @property
    def healthy(self) -> bool:
        return self.startup_ok and self.ready and self.live

    def as_dict(self) -> dict[str, Any]:
        return {
            "state": self.state.value, "startup_ok": self.startup_ok, "ready": self.ready,
            "live": self.live, "healthy": self.healthy, "at": self.at,
            "checks": [{"name": n, "ok": ok} for n, ok in self.checks], "detail": self.detail,
        }


@dataclass(frozen=True, slots=True)
class LogEntry:
    at: float
    stage: str
    message: str

    def as_dict(self) -> dict[str, Any]:
        return {"at": self.at, "stage": self.stage, "message": self.message}


@dataclass(frozen=True, slots=True)
class DeploymentExecutionResult:
    """The terminal outcome of one execution."""

    status: str  # "completed" | "failed" | "rolled_back" | "cancelled"
    live_version: str = ""
    message: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {"status": self.status, "live_version": self.live_version, "message": self.message}


# ── the execution request (derived from a read-only DeploymentPlan) ───────────────


@dataclass(frozen=True, slots=True)
class DeploymentJob:
    """The immutable unit of work — what to deploy, where, how. Derived once from the
    read-only Epic 7 `DeploymentPlan` (the Actuator never mutates the plan)."""

    id: str
    deployment_id: str  # == plan.deployment_id (content-addressed Epic 7 identity)
    org_id: str
    workspace_id: str
    project_id: str
    source_ref: str  # the published agent version / package the plan describes
    environment: str
    profile: str  # DeploymentProfile value
    strategy: str  # DeploymentStrategy value
    target: str  # infrastructure adapter name (e.g. "local")
    requires_approval: bool
    rollback_mode: str
    rollback_version: str
    requested_by: str
    requested_at: float
    plan_content_hash: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "deployment_id": self.deployment_id, "source_ref": self.source_ref,
            "environment": self.environment, "profile": self.profile,
            "strategy": self.strategy, "target": self.target,
            "requires_approval": self.requires_approval, "rollback_mode": self.rollback_mode,
            "requested_by": self.requested_by, "requested_at": self.requested_at,
            "plan_content_hash": self.plan_content_hash,
        }


@dataclass(frozen=True, slots=True)
class DeploymentExecution:
    """The deployment execution aggregate — the Actuator is its sole writer (§A2.5).

    Immutable: every mutator returns a new value with an appended timeline entry, so the full
    history is the audit trail. `state` is always the last transition's target."""

    id: str  # execution id (dpx_*)
    job: DeploymentJob
    state: DeploymentExecutionState
    attempts: int = 1
    created_at: float = 0.0
    updated_at: float = 0.0
    transitions: tuple[StateTransition, ...] = ()
    approvals: tuple[ApprovalRecord, ...] = ()
    rollbacks: tuple[RollbackRecord, ...] = ()
    health: tuple[HealthReport, ...] = ()
    logs: tuple[LogEntry, ...] = ()
    result: DeploymentExecutionResult | None = None
    cancel_requested: bool = False

    # scoping shortcuts (mirror the job for repository queries)
    @property
    def org_id(self) -> str:
        return self.job.org_id

    @property
    def project_id(self) -> str:
        return self.job.project_id

    @property
    def deployment_id(self) -> str:
        return self.job.deployment_id

    @property
    def is_terminal(self) -> bool:
        from .lifecycle import TERMINAL_STATES

        return self.state in TERMINAL_STATES

    @property
    def approval_decision(self) -> ApprovalDecision:
        return self.approvals[-1].decision if self.approvals else ApprovalDecision.PENDING

    @property
    def last_health(self) -> HealthReport | None:
        return self.health[-1] if self.health else None

    # ── immutable mutators ────────────────────────────────────────────────────────
    def transitioned(
        self, to: DeploymentExecutionState, at: float, *, actor: str = "", reason: str = ""
    ) -> DeploymentExecution:
        t = StateTransition(self.state, to, at, actor=actor, reason=reason)
        return replace(
            self, state=to, updated_at=at, transitions=(*self.transitions, t)
        )

    def with_approval(self, record: ApprovalRecord) -> DeploymentExecution:
        return replace(self, approvals=(*self.approvals, record), updated_at=record.at)

    def with_rollback(self, record: RollbackRecord) -> DeploymentExecution:
        return replace(self, rollbacks=(*self.rollbacks, record), updated_at=record.at)

    def with_health(self, report: HealthReport) -> DeploymentExecution:
        return replace(self, health=(*self.health, report), updated_at=report.at)

    def logged(self, at: float, stage: str, message: str) -> DeploymentExecution:
        return replace(self, logs=(*self.logs, LogEntry(at, stage, message)))

    def with_result(self, result: DeploymentExecutionResult, at: float) -> DeploymentExecution:
        return replace(self, result=result, updated_at=at)

    def requeued(self, at: float) -> DeploymentExecution:
        return replace(self, attempts=self.attempts + 1, updated_at=at)

    def cancel_flagged(self) -> DeploymentExecution:
        return replace(self, cancel_requested=True)

    # ── views ─────────────────────────────────────────────────────────────────────
    def as_summary(self) -> DeploymentExecutionSummary:
        return DeploymentExecutionSummary(
            id=self.id, deployment_id=self.deployment_id, environment=self.job.environment,
            state=self.state, attempts=self.attempts,
            live_version=self.result.live_version if self.result else "",
            requested_by=self.job.requested_by, created_at=self.created_at,
            updated_at=self.updated_at,
        )

    def as_view(self) -> DeploymentExecutionView:
        return DeploymentExecutionView(
            id=self.id, deployment_id=self.deployment_id, org_id=self.org_id,
            workspace_id=self.job.workspace_id, project_id=self.project_id,
            environment=self.job.environment, profile=self.job.profile,
            strategy=self.job.strategy, target=self.job.target, state=self.state.value,
            attempts=self.attempts, requires_approval=self.job.requires_approval,
            approval_decision=self.approval_decision.value,
            live_version=self.result.live_version if self.result else "",
            result=self.result.as_dict() if self.result else None,
            job=self.job.as_dict(), created_at=self.created_at, updated_at=self.updated_at,
        )

    def as_history(self) -> DeploymentExecutionHistory:
        return DeploymentExecutionHistory(
            id=self.id, deployment_id=self.deployment_id, state=self.state.value,
            transitions=tuple(t.as_dict() for t in self.transitions),
            approvals=tuple(a.as_dict() for a in self.approvals),
            rollbacks=tuple(r.as_dict() for r in self.rollbacks),
            health=tuple(h.as_dict() for h in self.health),
        )


@dataclass(frozen=True, slots=True)
class DeploymentExecutionSummary:
    id: str
    deployment_id: str
    environment: str
    state: DeploymentExecutionState
    attempts: int
    live_version: str
    requested_by: str
    created_at: float
    updated_at: float

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id, "deployment_id": self.deployment_id, "environment": self.environment,
            "state": self.state.value, "attempts": self.attempts,
            "live_version": self.live_version, "requested_by": self.requested_by,
            "created_at": self.created_at, "updated_at": self.updated_at,
        }


@dataclass(frozen=True, slots=True)
class DeploymentExecutionView:
    id: str
    deployment_id: str
    org_id: str
    workspace_id: str
    project_id: str
    environment: str
    profile: str
    strategy: str
    target: str
    state: str
    attempts: int
    requires_approval: bool
    approval_decision: str
    live_version: str
    result: dict[str, Any] | None
    job: dict[str, Any]
    created_at: float
    updated_at: float


@dataclass(frozen=True, slots=True)
class DeploymentExecutionHistory:
    """The immutable, content-ordered timeline projection Studio renders (read-only)."""

    id: str
    deployment_id: str
    state: str
    transitions: tuple[dict[str, Any], ...]
    approvals: tuple[dict[str, Any], ...]
    rollbacks: tuple[dict[str, Any], ...]
    health: tuple[dict[str, Any], ...]

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id, "deployment_id": self.deployment_id, "state": self.state,
            "transitions": list(self.transitions), "approvals": list(self.approvals),
            "rollbacks": list(self.rollbacks), "health": list(self.health),
        }
