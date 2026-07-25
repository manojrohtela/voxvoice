"""Loop Engine model shapes (M6.6B-1) — per `docs/architecture/epic6/
loop-engine-design.md` §4. Structure only: no pipeline execution, no
persistence, no reasoning. `LoopDefinition` is never registered or looked
up — the caller constructs one directly at the call site (§4.1); there is
no `LoopRegistry`.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True, slots=True)
class LoopStoppingPolicy:
    max_iterations: int = 5
    confidence_threshold: float | None = None  # None -> confidence not used to stop
    stop_on_validation_success: bool = True
    stop_on_convergence: bool = True  # successive proposals structurally identical


@dataclass(frozen=True, slots=True)
class LoopDefinition:
    loop_id: str  # caller-supplied; never registry-assigned, there is no registry
    task_description: str  # opaque prose, handed to ReasoningPort as-is, never parsed
    stopping_policy: LoopStoppingPolicy = field(default_factory=LoopStoppingPolicy)
    metadata: Mapping[str, str] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class LoopProposal:
    proposal_id: str
    content_ref: str  # content-hash reference to the proposal's own content --
    # never the raw content itself
    reasoning_decision_ref: str | None = None


@dataclass(frozen=True, slots=True)
class LoopCritique:
    critique_id: str
    proposal_id: str
    content_ref: str
    reasoning_decision_ref: str | None = None


@dataclass(frozen=True, slots=True)
class LoopRepair:
    repair_id: str
    critique_id: str
    content_ref: str  # the repaired proposal's content-hash reference
    reasoning_decision_ref: str | None = None


@dataclass(frozen=True, slots=True)
class LoopValidation:
    validation_id: str
    proposal_id: str  # the proposal (initial or repaired) being validated
    passed: bool
    confidence: float | None = None
    reason: str = ""


@dataclass(frozen=True, slots=True)
class LoopIteration:
    iteration_number: int
    proposal: LoopProposal
    critique: LoopCritique | None  # None on the final, no-critique-needed pass
    repair: LoopRepair | None  # None if the proposal already validated
    validation: LoopValidation
    stopped: bool  # did the stop-check end the loop after this iteration
    stop_reason: str = ""  # "max_iterations" | "validation_success" |
    # "confidence_threshold" | "convergence" | "external_stop" | ""


@dataclass(frozen=True, slots=True)
class LoopResult:
    loop_id: str
    final_proposal_id: str
    converged: bool
    iteration_count: int
    stop_reason: str
    iterations: tuple[LoopIteration, ...] = ()
    completed_at: float = 0.0


@dataclass(frozen=True, slots=True)
class LoopStatistics:
    loop_count: int = 0
    avg_iteration_count: float = 0.0
    per_stop_reason: Mapping[str, int] = field(default_factory=dict)
    convergence_rate: float = 0.0  # fraction of loops that converged vs. exhausted budget


@dataclass(frozen=True, slots=True)
class LoopExecutionContext:
    loop_id: str
    session_id: str
    trace_id: str
    org_id: str | None = None
    iteration: int = 0
    variables: Mapping[str, Any] = field(default_factory=dict)  # loop-carried state only
