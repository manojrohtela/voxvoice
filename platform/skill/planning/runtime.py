"""SkillPlanningRuntime (M6.2B-2) — the planning + planning-observability +
planning-replay facade.

Ties `SkillExecutionPlanner`, the default `SkillPolicyEngine`
(`AllowAllSkillPolicy`), planning-trace recording, and planning-replay
recording/validation into one object. **Never executes a Skill, never
executes a Tool, never resolves a capability** — every method here either
reads already-registered `SkillDescriptor`s from the injected `SkillRegistry`
or projects an already-built `SkillPlan`/`SkillExecutionContext`. Bundle 1
(`registry.py`, `runtime.py`, `domain.py`, ...) is untouched by this
milestone.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from typing import Any

from core.errors import ConfigError
from core.events import Event, EventType

from .._run import run_coro_sync
from ..extensions import SkillPolicyEngine
from ..registry import SkillRegistry
from .config import PlanningConfiguration
from .context import build_execution_context
from .diagnostics import build_diagnostics
from .domain import (
    PlanningDiagnostics,
    PlanningStatistics,
    ReplayComparison,
    SkillExecutionContext,
    SkillPlan,
    SkillPlanningTrace,
    SkillPlanReplayRecord,
    SkillPolicyVerdict,
)
from .events import PHASE_EVENT_TYPE, PlanningPhase
from .observability import planning_trace_for
from .planner import SkillExecutionPlanner
from .policy import AllowAllSkillPolicy
from .replay import plan_replay_record_for
from .replay_engine import PlanReplayEngine
from .repository import PlanningRepository
from .serializer import PlanningSerializer
from .statistics import compute_statistics

Publisher = Callable[..., Event | None]


class SkillPlanningRuntime:
    def __init__(
        self,
        *,
        config: PlanningConfiguration,
        repository: PlanningRepository,
        registry: SkillRegistry,
        policy_engine: SkillPolicyEngine | None = None,
        planner: SkillExecutionPlanner | None = None,
        replay_engine: PlanReplayEngine | None = None,
        serializer: PlanningSerializer | None = None,
        clock: Callable[[], float] | None = None,
        publish: Publisher | None = None,
    ) -> None:
        import time

        self._config = config
        self._repo = repository
        self._registry = registry
        self._policy = policy_engine or AllowAllSkillPolicy()
        self._planner = planner or SkillExecutionPlanner()
        self._replay_engine = replay_engine or PlanReplayEngine()
        self._serializer = serializer or PlanningSerializer()
        self._clock = clock or time.time
        self._publish = publish

    # ── planning ─────────────────────────────────────────────────────────────
    def create_plan(self, namespace: str, id_: str, version: str | None = None) -> SkillPlan:
        descriptor = self._registry.get_descriptor(namespace, id_, version)
        verdict = self._policy.authorize(descriptor)
        self._emit(PlanningPhase.POLICY_EVALUATED, plan_id="",
                   extra={"verdict": _verdict_value(verdict)})
        if verdict is SkillPolicyVerdict.DENY:
            self._emit(PlanningPhase.PLANNING_FAILED, plan_id="",
                       extra={"reason": "policy_denied", "skill": descriptor.qualified_name})
            raise ConfigError(f"planning denied for {descriptor.qualified_name!r} by policy")

        plan = self._planner.plan(self._registry, namespace, id_, version, now=self._clock())
        trace = planning_trace_for(plan, now=self._clock())
        run_coro_sync(self._repo.add_trace(trace))
        self._emit(PlanningPhase.PLAN_CREATED, plan_id=plan.plan_id,
                   extra={"skill": plan.qualified_name, "steps": len(plan.steps)})
        return plan

    def create_context(
        self, plan: SkillPlan, *, org_id: str = "", tenant_id: str = "", requested_by: str = "",
        metadata: Mapping[str, str] | None = None,
    ) -> SkillExecutionContext:
        context = build_execution_context(
            plan, org_id=org_id, tenant_id=tenant_id, requested_by=requested_by,
            now=self._clock(), metadata=metadata,
        )
        self._emit(PlanningPhase.CONTEXT_CREATED, plan_id=plan.plan_id,
                   extra={"context_id": context.context_id})
        return context

    # ── replay ─────────────────────────────────────────────────────────────────
    def record_replay(
        self, plan: SkillPlan, context: SkillExecutionContext
    ) -> SkillPlanReplayRecord:
        record = plan_replay_record_for(
            plan, context, now=self._clock(), serializer=self._serializer,
        )
        run_coro_sync(self._repo.add_replay(record))
        self._emit(PlanningPhase.REPLAY_RECORDED, plan_id=plan.plan_id,
                   extra={"replay_id": record.replay_id})
        return record

    def validate_replay(self, replay_id: str) -> ReplayComparison:
        record = run_coro_sync(self._repo.get_replay(replay_id))
        if record is None:
            raise ConfigError(f"unknown planning replay {replay_id!r}")
        comparison = self._replay_engine.validate(record)
        phase = (
            PlanningPhase.REPLAY_VALIDATED if comparison.matches else PlanningPhase.REPLAY_MISMATCH
        )
        self._emit(phase, plan_id="", extra={"replay_id": replay_id, "matches": comparison.matches})
        return comparison

    def compare_replays(self, replay_id_a: str, replay_id_b: str) -> ReplayComparison:
        a = run_coro_sync(self._repo.get_replay(replay_id_a))
        b = run_coro_sync(self._repo.get_replay(replay_id_b))
        if a is None or b is None:
            raise ConfigError("unknown planning replay id(s) for comparison")
        return self._replay_engine.compare(a, b)

    # ── diagnostics + statistics ───────────────────────────────────────────────
    def diagnostics(self, *, healthy: bool = True) -> PlanningDiagnostics:
        plan_count = run_coro_sync(self._repo.trace_count())
        replay_count = run_coro_sync(self._repo.replay_count())
        diag = build_diagnostics(
            config=self._config, registry=self._registry, plan_count=plan_count,
            replay_count=replay_count, healthy=healthy,
        )
        self._emit(PlanningPhase.DIAGNOSTICS_GENERATED, plan_id="")
        return diag

    def statistics(self, plans: Sequence[SkillPlan]) -> PlanningStatistics:
        return compute_statistics(plans)

    # ── internals ────────────────────────────────────────────────────────────
    def _emit(
        self, phase: PlanningPhase, *, plan_id: str, extra: dict[str, Any] | None = None,
    ) -> None:
        if self._publish is None:
            return
        event_type: EventType = PHASE_EVENT_TYPE[phase]
        payload = {"phase": phase.value, "plan_id": plan_id, **(extra or {})}
        self._publish(event_type, payload, session_id="", org_id="")


def _verdict_value(verdict: object) -> str:
    return verdict.value if isinstance(verdict, SkillPolicyVerdict) else str(verdict)


__all__ = ["SkillPlanningRuntime", "SkillPlanningTrace"]
