"""Agent execution serialization (M6.5B-3).

Deterministic, adapter-neutral translation between the execution-side
domain aggregates (`AgentGoalExecution`, `AgentGoalSnapshot`, `AgentDecision`,
`AgentReplayRecord`) and the plain dict shapes used by storage.
Round-trippable; no I/O. Stateless — safe to share. Mirrors
`workflow.execution.serializer.WorkflowExecutionSerializer`'s shape and
conventions.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from ..domain import AgentDecision, AgentGoalState
from ..planning import AgentPlan, PlanStep
from .domain import AgentGoalExecution, AgentGoalSnapshot, AgentReplayRecord

SCHEMA_VERSION = "1"


def _step_to_dict(s: PlanStep) -> dict[str, Any]:
    return {
        "step_id": s.step_id, "kind": s.kind, "workflow_ref": s.workflow_ref,
        "inputs": dict(s.inputs),
    }


def _step_from_dict(d: Mapping[str, Any]) -> PlanStep:
    return PlanStep(
        step_id=d["step_id"], kind=d["kind"], workflow_ref=d.get("workflow_ref"),
        inputs=dict(d.get("inputs", {})),
    )


def _plan_to_dict(p: AgentPlan) -> dict[str, Any]:
    return {
        "plan_id": p.plan_id, "goal_id": p.goal_id,
        "steps": [_step_to_dict(s) for s in p.steps],
        "plan_hash": p.plan_hash, "revision": p.revision, "created_at": p.created_at,
    }


def _plan_from_dict(d: Mapping[str, Any]) -> AgentPlan:
    return AgentPlan(
        plan_id=d["plan_id"], goal_id=d["goal_id"],
        steps=tuple(_step_from_dict(s) for s in d.get("steps", ())),
        plan_hash=d.get("plan_hash", ""), revision=d.get("revision", 0),
        created_at=d.get("created_at", 0.0),
    )


def _decision_to_dict(d: AgentDecision) -> dict[str, Any]:
    return {
        "decision_id": d.decision_id, "goal_id": d.goal_id, "kind": d.kind,
        "reasoning_decision_ref": d.reasoning_decision_ref, "payload_hash": d.payload_hash,
        "created_at": d.created_at,
    }


def _decision_from_dict(d: Mapping[str, Any]) -> AgentDecision:
    return AgentDecision(
        decision_id=d["decision_id"], goal_id=d["goal_id"], kind=d["kind"],
        reasoning_decision_ref=d.get("reasoning_decision_ref"),
        payload_hash=d.get("payload_hash", ""), created_at=d.get("created_at", 0.0),
    )


class AgentExecutionSerializer:
    """Stateless, deterministic (de)serializer for goal-execution
    aggregates."""

    # ── AgentGoalExecution ────────────────────────────────────────────────────
    def execution_to_dict(self, e: AgentGoalExecution) -> dict[str, Any]:
        return {
            "schema_version": SCHEMA_VERSION, "execution_id": e.execution_id,
            "goal_id": e.goal_id, "agent_id": e.agent_id,
            "conversation_id": e.conversation_id, "state": e.state.value,
            "plan_id": e.plan_id, "delegated_execution_id": e.delegated_execution_id,
            "started_at": e.started_at, "updated_at": e.updated_at,
        }

    def execution_from_dict(self, d: Mapping[str, Any]) -> AgentGoalExecution:
        return AgentGoalExecution(
            execution_id=d["execution_id"], goal_id=d["goal_id"], agent_id=d["agent_id"],
            conversation_id=d["conversation_id"], state=AgentGoalState(d["state"]),
            plan_id=d.get("plan_id", ""), delegated_execution_id=d.get("delegated_execution_id"),
            started_at=d.get("started_at", 0.0), updated_at=d.get("updated_at", 0.0),
        )

    # ── AgentGoalSnapshot ─────────────────────────────────────────────────────
    def snapshot_to_dict(self, s: AgentGoalSnapshot) -> dict[str, Any]:
        return {
            "schema_version": SCHEMA_VERSION, "execution_id": s.execution_id,
            "state": s.state.value, "plan": _plan_to_dict(s.plan) if s.plan else None,
            "decisions": [_decision_to_dict(d) for d in s.decisions],
            "delegated_execution_id": s.delegated_execution_id,
            "pending_error": s.pending_error,
        }

    def snapshot_from_dict(self, d: Mapping[str, Any]) -> AgentGoalSnapshot:
        plan_doc = d.get("plan")
        return AgentGoalSnapshot(
            execution_id=d["execution_id"], state=AgentGoalState(d["state"]),
            plan=_plan_from_dict(plan_doc) if plan_doc else None,
            decisions=tuple(_decision_from_dict(x) for x in d.get("decisions", ())),
            delegated_execution_id=d.get("delegated_execution_id"),
            pending_error=d.get("pending_error"),
        )

    # ── AgentReplayRecord ─────────────────────────────────────────────────────
    def replay_to_dict(self, r: AgentReplayRecord) -> dict[str, Any]:
        return {
            "schema_version": SCHEMA_VERSION, "replay_id": r.replay_id, "goal_id": r.goal_id,
            "decision_hashes": list(r.decision_hashes), "final_state": r.final_state,
            "replay_hash": r.replay_hash, "replay_version": r.replay_version,
            "replay_timestamp": r.replay_timestamp,
        }

    def replay_from_dict(self, d: Mapping[str, Any]) -> AgentReplayRecord:
        return AgentReplayRecord(
            replay_id=d["replay_id"], goal_id=d["goal_id"],
            decision_hashes=tuple(d.get("decision_hashes", ())),
            final_state=d["final_state"], replay_hash=d["replay_hash"],
            replay_version=d.get("replay_version", "1"),
            replay_timestamp=d.get("replay_timestamp", 0.0),
        )
