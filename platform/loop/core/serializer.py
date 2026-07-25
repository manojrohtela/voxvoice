"""Loop Engine serialization (M6.6B-1).

Deterministic, adapter-neutral translation between the model shapes (§4 of
the M6.6A design) and plain dict shapes. Round-trippable; no I/O. Stateless
— safe to share. Mirrors `agent.serializer.AgentSerializer`'s conventions.
Loop Engine itself never persists these dicts anywhere (no repository ships
in this package) — this exists so a future *caller* (Agent Runtime, Workflow
Runtime, or any adopter) can serialize a `LoopResult` into its own storage.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from .domain import (
    LoopCritique,
    LoopDefinition,
    LoopIteration,
    LoopProposal,
    LoopRepair,
    LoopResult,
    LoopStoppingPolicy,
    LoopValidation,
)

SCHEMA_VERSION = "1"


class LoopSerializer:
    """Stateless, deterministic (de)serializer for Loop Engine model shapes."""

    # ── LoopStoppingPolicy ────────────────────────────────────────────────────
    def stopping_policy_to_dict(self, p: LoopStoppingPolicy) -> dict[str, Any]:
        return {
            "max_iterations": p.max_iterations,
            "confidence_threshold": p.confidence_threshold,
            "stop_on_validation_success": p.stop_on_validation_success,
            "stop_on_convergence": p.stop_on_convergence,
        }

    def stopping_policy_from_dict(self, d: Mapping[str, Any]) -> LoopStoppingPolicy:
        return LoopStoppingPolicy(
            max_iterations=d.get("max_iterations", 5),
            confidence_threshold=d.get("confidence_threshold"),
            stop_on_validation_success=d.get("stop_on_validation_success", True),
            stop_on_convergence=d.get("stop_on_convergence", True),
        )

    # ── LoopDefinition ────────────────────────────────────────────────────────
    def definition_to_dict(self, d: LoopDefinition) -> dict[str, Any]:
        return {
            "schema_version": SCHEMA_VERSION, "loop_id": d.loop_id,
            "task_description": d.task_description,
            "stopping_policy": self.stopping_policy_to_dict(d.stopping_policy),
            "metadata": dict(d.metadata),
        }

    def definition_from_dict(self, d: Mapping[str, Any]) -> LoopDefinition:
        return LoopDefinition(
            loop_id=d["loop_id"], task_description=d.get("task_description", ""),
            stopping_policy=self.stopping_policy_from_dict(d.get("stopping_policy", {})),
            metadata=dict(d.get("metadata", {})),
        )

    # ── LoopProposal / LoopCritique / LoopRepair / LoopValidation ────────────
    def proposal_to_dict(self, p: LoopProposal) -> dict[str, Any]:
        return {
            "proposal_id": p.proposal_id, "content_ref": p.content_ref,
            "reasoning_decision_ref": p.reasoning_decision_ref,
        }

    def proposal_from_dict(self, d: Mapping[str, Any]) -> LoopProposal:
        return LoopProposal(
            proposal_id=d["proposal_id"], content_ref=d["content_ref"],
            reasoning_decision_ref=d.get("reasoning_decision_ref"),
        )

    def critique_to_dict(self, c: LoopCritique) -> dict[str, Any]:
        return {
            "critique_id": c.critique_id, "proposal_id": c.proposal_id,
            "content_ref": c.content_ref,
            "reasoning_decision_ref": c.reasoning_decision_ref,
        }

    def critique_from_dict(self, d: Mapping[str, Any]) -> LoopCritique:
        return LoopCritique(
            critique_id=d["critique_id"], proposal_id=d["proposal_id"],
            content_ref=d["content_ref"],
            reasoning_decision_ref=d.get("reasoning_decision_ref"),
        )

    def repair_to_dict(self, r: LoopRepair) -> dict[str, Any]:
        return {
            "repair_id": r.repair_id, "critique_id": r.critique_id,
            "content_ref": r.content_ref,
            "reasoning_decision_ref": r.reasoning_decision_ref,
        }

    def repair_from_dict(self, d: Mapping[str, Any]) -> LoopRepair:
        return LoopRepair(
            repair_id=d["repair_id"], critique_id=d["critique_id"],
            content_ref=d["content_ref"],
            reasoning_decision_ref=d.get("reasoning_decision_ref"),
        )

    def validation_to_dict(self, v: LoopValidation) -> dict[str, Any]:
        return {
            "validation_id": v.validation_id, "proposal_id": v.proposal_id,
            "passed": v.passed, "confidence": v.confidence, "reason": v.reason,
        }

    def validation_from_dict(self, d: Mapping[str, Any]) -> LoopValidation:
        return LoopValidation(
            validation_id=d["validation_id"], proposal_id=d["proposal_id"],
            passed=d["passed"], confidence=d.get("confidence"), reason=d.get("reason", ""),
        )

    # ── LoopIteration / LoopResult ───────────────────────────────────────────
    def iteration_to_dict(self, i: LoopIteration) -> dict[str, Any]:
        return {
            "iteration_number": i.iteration_number,
            "proposal": self.proposal_to_dict(i.proposal),
            "critique": self.critique_to_dict(i.critique) if i.critique else None,
            "repair": self.repair_to_dict(i.repair) if i.repair else None,
            "validation": self.validation_to_dict(i.validation),
            "stopped": i.stopped, "stop_reason": i.stop_reason,
        }

    def iteration_from_dict(self, d: Mapping[str, Any]) -> LoopIteration:
        critique = d.get("critique")
        repair = d.get("repair")
        return LoopIteration(
            iteration_number=d["iteration_number"],
            proposal=self.proposal_from_dict(d["proposal"]),
            critique=self.critique_from_dict(critique) if critique else None,
            repair=self.repair_from_dict(repair) if repair else None,
            validation=self.validation_from_dict(d["validation"]),
            stopped=d.get("stopped", False), stop_reason=d.get("stop_reason", ""),
        )

    def result_to_dict(self, r: LoopResult) -> dict[str, Any]:
        return {
            "schema_version": SCHEMA_VERSION, "loop_id": r.loop_id,
            "final_proposal_id": r.final_proposal_id, "converged": r.converged,
            "iteration_count": r.iteration_count, "stop_reason": r.stop_reason,
            "iterations": [self.iteration_to_dict(i) for i in r.iterations],
            "completed_at": r.completed_at,
        }

    def result_from_dict(self, d: Mapping[str, Any]) -> LoopResult:
        return LoopResult(
            loop_id=d["loop_id"], final_proposal_id=d["final_proposal_id"],
            converged=d.get("converged", False),
            iteration_count=d.get("iteration_count", 0),
            stop_reason=d.get("stop_reason", ""),
            iterations=tuple(
                self.iteration_from_dict(i) for i in d.get("iterations", ())
            ),
            completed_at=d.get("completed_at", 0.0),
        )
